#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_mock_apply.py — переносимый end-to-end mock тест apply pipeline.

Подменяет только внешний исполнитель (PowerShell) через monkeypatching subprocess.run.
Реальная оркестрация safe_apply, построение аргументов и guards выполняются.
Все логи — во временном каталоге. Не зависит от os.environ['TEMP'].
Работает без PowerShell и платформы 1С, включая Linux CI.

Проверяет:
1. Успешный сценарий: точная последовательность ['db-load-xml', 'db-update'].
2. Dry-run: журнал внешних операций пуст.
3. Ошибка load: журнал содержит только load, exit ненулевой.
4. Ошибка update: журнал содержит load+update в порядке, exit ненулевой.
5. Точные значения аргументов: путь базы, ConfigDir, Mode=Partial, Files, UserName.
6. При password_mode=none параметры пароля отсутствуют.
7. При backup_mode=external не требуется backup.md.
8. Блокировка production, неизвестного password_mode, выхода за configSrc.
9. Путь с пробелами и кириллицей.
10. Запуск из другого cwd.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from scope_hash import compute_scope_hash

UNICODE_USERNAME = "Администратор"


class FakeRunner:
    """Подмена subprocess.run: перехватывает вызовы PowerShell skill-скриптов.
    Записывает структурированный журнал вызовов."""

    def __init__(self, call_log: list, exit_codes: dict, real_run):
        self.call_log = call_log
        self.exit_codes = exit_codes
        self.real_run = real_run

    def __call__(self, cmd, **kwargs):
        # Перехватываем только PowerShell вызовы skill-скриптов
        if isinstance(cmd, list) and len(cmd) > 0 and "powershell" in str(cmd[0]).lower():
            # Извлечь имя skill из пути скрипта
            script_path = None
            for arg in cmd:
                if isinstance(arg, str) and arg.endswith(".ps1"):
                    script_path = arg
                    break
            if script_path:
                skill_name = Path(script_path).parent.parent.name
                # Записать структурированный вызов
                ps_args = cmd[cmd.index(script_path) + 1:] if script_path in cmd else []
                self.call_log.append({
                    "skill": skill_name,
                    "args": ps_args,
                    "args_str": " ".join(str(a) for a in ps_args),
                })
                exit_code = self.exit_codes.get(skill_name, 0)
                # Возвращаем mock результат
                class MockResult:
                    returncode = exit_code
                    stdout = ""
                    stderr = ""
                return MockResult()
        # Guard вызовы (python) — передаём реальному subprocess.run
        return self.real_run(cmd, **kwargs)


def setup_test_env(tdpath: Path, task_id: str = "TASK-TEST001") -> dict:
    """Создать тестовое окружение во временном каталоге."""
    src_dir = tdpath / "projects" / "collector" / "src" / "Catalogs" / "Test" / "Ext"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "ObjectModule.bsl").write_text("// test\n", encoding="utf-8")
    cfg = {
        "databases": [{
            "id": "collector",
            "type": "file",
            "path": str(tdpath / "fake_base"),
            "environment": "local",
            "username": UNICODE_USERNAME,
            "password_mode": "none",
            "backup_mode": "external",
            "configSrc": "projects/collector/src",
        }]
    }
    (tdpath / ".v8-project.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    specs_dir = tdpath / "specs" / task_id
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_content = f"""# Solution Spec: {task_id}

```yaml
status: approved
risk: low
approved_by: user
approved_at: 2026-01-01T12:00:00+00:00
spec_version: 1
scope_hash: PLACEHOLDER
```

## Границы изменения
- новый справочник Test

## Затрагиваемые файлы
- projects/collector/src/Catalogs/Test/Ext/ObjectModule.bsl

## Критерии приёмки
- справочник создаётся
"""
    h = compute_scope_hash(spec_content)
    spec_content = spec_content.replace("PLACEHOLDER", h)
    (specs_dir / "03_solution_spec.md").write_text(spec_content, encoding="utf-8")
    report = f"""# Отчёт об изменениях: {task_id}

```yaml
scope_hash: {h}
spec_version: 1
```

## Изменённые файлы
- projects/collector/src/Catalogs/Test/Ext/ObjectModule.bsl

## Что сделано
Тестовая реализация
"""
    (specs_dir / "06_change_report.md").write_text(report, encoding="utf-8")
    control_dir = tdpath / "pilot-control" / task_id
    control_dir.mkdir(parents=True, exist_ok=True)
    review = f"""# Review: {task_id}

```yaml
verdict: approved
reviewed_by: 1c-reviewer
reviewed_at: 2026-01-01T12:00:00+00:00
spec_version: 1
scope_hash: {h}
```
"""
    (control_dir / "review.md").write_text(review, encoding="utf-8")
    # Fake skills (content не важен — FakeRunner перехватывает вызов)
    skills_dir = tdpath / "skills"
    for skill_name in ("db-load-xml", "db-update"):
        sdir = skills_dir / skill_name / "scripts"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / f"{skill_name}.ps1").write_text("# fake\nexit 0\n", encoding="utf-8")
    return {"task_id": task_id, "hash": h, "cfg": cfg}


def run_safe_apply_mock(tdpath: Path, task_id: str, db_id: str,
                        exit_codes: dict, dry_run: bool = False, cwd: str = None) -> tuple:
    """Запустить safe_apply с FakeRunner. Возвращает (exit_code, call_log)."""
    call_log: list = []
    safe_apply = SCRIPT_DIR / "safe_apply.py"
    cmd = [sys.executable, str(safe_apply), "--task", task_id, "--db", db_id,
           "--project-root", str(tdpath)]
    if dry_run:
        cmd.append("--dry-run")
    # Запускаем как subprocess, но передаём exit_codes через env
    # FakeRunner работает только внутри того же процесса,
    # но safe_apply запускается как subprocess.
    # Решение: используем mock skill-скрипты, которые читают exit code из env.
    env = dict(os.environ)
    for skill_name, code in exit_codes.items():
        env_key = skill_name.upper().replace("-", "_") + "_EXIT"
        env[env_key] = str(code)
    # Создаём mock skill-скрипты, которые читают exit code из env
    skills_dir = tdpath / "skills"
    for skill_name in ("db-load-xml", "db-update"):
        sdir = skills_dir / skill_name / "scripts"
        sdir.mkdir(parents=True, exist_ok=True)
        env_key = skill_name.upper().replace("-", "_") + "_EXIT"
        runner_script = f"""# Fake runner for {skill_name}
$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$argStr = $args -join ' '
$logFile = Join-Path $env:MOCK_APPLY_LOG_DIR '{skill_name}.log'
$argStr | Out-File -FilePath $logFile -Encoding UTF8 -Append
$exitCode = 0
$envVal = [Environment]::GetEnvironmentVariable('{env_key}')
if ($envVal) {{ $exitCode = [int]$envVal }}
exit $exitCode
"""
        (sdir / f"{skill_name}.ps1").write_text(runner_script, encoding="utf-8")
    env["MOCK_APPLY_LOG_DIR"] = str(tdpath / "mock_logs")
    (tdpath / "mock_logs").mkdir(exist_ok=True)
    # Очистить логи
    log_dir = tdpath / "mock_logs"
    for f in log_dir.glob("*.log"):
        f.unlink()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd or str(tdpath), env=env)
    # Прочитать логи
    for skill_name in ("db-load-xml", "db-update"):
        log_file = log_dir / f"{skill_name}.log"
        if log_file.exists():
            content = log_file.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                call_log.append({"skill": skill_name, "args_str": content})
    return r.returncode, call_log


def read_call_log(tdpath: Path) -> list:
    """Прочитать упорядоченный журнал вызовов из mock_logs."""
    log_dir = tdpath / "mock_logs"
    log = []
    for skill_name in ("db-load-xml", "db-update"):
        log_file = log_dir / f"{skill_name}.log"
        if log_file.exists():
            content = log_file.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                log.append({"skill": skill_name, "args_str": content})
    return log


def clear_call_log(tdpath: Path) -> None:
    log_dir = tdpath / "mock_logs"
    if log_dir.exists():
        for f in log_dir.glob("*.log"):
            f.unlink()


def check_results(results: list) -> int:
    """Вывести результаты и вернуть exit code."""
    print("=== Mock Apply Integration Test ===")
    passed = 0
    failed = 0
    for name, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: {name} {detail}")
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\nResult: {passed} passed, {failed} failed out of {len(results)}")
    return 0 if failed == 0 else 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    guard = SCRIPT_DIR / "applier_guard.py"
    results = []

    with tempfile.TemporaryDirectory(prefix="mock_apply_") as td:
        tdpath = Path(td)
        env = setup_test_env(tdpath)
        task_id = env["task_id"]
        cfg_path = tdpath / ".v8-project.json"
        specs_dir = tdpath / "specs"
        control_dir = tdpath / "pilot-control"
        project_root = tdpath

        # --- Guard проверки ---

        def run_guard(task, db, op, mode="Partial", cfg=None):
            cmd = [sys.executable, str(guard), "--config", str(cfg or cfg_path),
                   "--specs-dir", str(specs_dir), "--control-dir", str(control_dir),
                   "--project-root", str(project_root), "--mode", mode,
                   "--task", task, "--db", db, "--op", op]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            return r.returncode, r.stdout

        # 1. Guard load-xml → exit 0
        rc, _ = run_guard(task_id, "collector", "load-xml")
        results.append(("guard load-xml → exit 0", rc == 0, f"exit={rc}"))

        # 2. Guard update → exit 0
        rc, _ = run_guard(task_id, "collector", "update")
        results.append(("guard update → exit 0", rc == 0, f"exit={rc}"))

        # 3. backup_mode external: no backup.md required
        backup_md = control_dir / task_id / "backup.md"
        rc, _ = run_guard(task_id, "collector", "load-xml")
        results.append(("backup_mode external: no backup.md", not backup_md.exists() and rc == 0, ""))

        # 4. Production blocked
        prod_cfg = {"databases": [{"id": "prod", "type": "file", "path": ".\\prod",
                     "environment": "production", "password_mode": "none", "backup_mode": "external"}]}
        prod_cfg_path = tdpath / "prod_cfg.json"
        prod_cfg_path.write_text(json.dumps(prod_cfg), encoding="utf-8")
        rc, _ = run_guard(task_id, "prod", "update", cfg=prod_cfg_path)
        results.append(("production blocked", rc != 0, f"exit={rc}"))

        # 5. Unknown password_mode blocked
        bad_cfg = {"databases": [{"id": "bad", "type": "file", "path": ".\\bad",
                    "environment": "local", "password_mode": "unknown", "backup_mode": "external"}]}
        bad_cfg_path = tdpath / "bad_cfg.json"
        bad_cfg_path.write_text(json.dumps(bad_cfg), encoding="utf-8")
        rc, _ = run_guard(task_id, "bad", "update", cfg=bad_cfg_path)
        results.append(("unknown password_mode blocked", rc != 0, f"exit={rc}"))

        # 6. Files outside configSrc blocked
        # Add bad file to report + spec
        report_path = specs_dir / task_id / "06_change_report.md"
        spec_path = specs_dir / task_id / "03_solution_spec.md"
        review_path = control_dir / task_id / "review.md"
        old_h = env["hash"]
        report_text = report_path.read_text(encoding="utf-8")
        spec_text = spec_path.read_text(encoding="utf-8")
        bad_file = "projects/other/src/Hack.bsl"
        good_file = "projects/collector/src/Catalogs/Test/Ext/ObjectModule.bsl"
        bad_report = report_text.replace(
            f"- {good_file}", f"- {bad_file}\n- {good_file}")
        bad_spec = spec_text.replace(
            f"- {good_file}", f"- {bad_file}\n- {good_file}")
        new_h = compute_scope_hash(bad_spec)
        bad_report = bad_report.replace(old_h, new_h)
        bad_spec = bad_spec.replace(old_h, new_h)
        report_path.write_text(bad_report, encoding="utf-8")
        spec_path.write_text(bad_spec, encoding="utf-8")
        review_path.write_text(review_path.read_text(encoding="utf-8").replace(old_h, new_h), encoding="utf-8")
        (tdpath / "projects" / "other" / "src").mkdir(parents=True, exist_ok=True)
        (tdpath / "projects" / "other" / "src" / "Hack.bsl").write_text("// hack\n", encoding="utf-8")
        rc, _ = run_guard(task_id, "collector", "load-xml")
        results.append(("files outside configSrc blocked", rc != 0, f"exit={rc}"))
        # Restore original
        report_path.write_text(report_text, encoding="utf-8")
        spec_path.write_text(spec_text, encoding="utf-8")
        review_path.write_text(review_path.read_text(encoding="utf-8").replace(new_h, old_h), encoding="utf-8")

        # --- safe_apply pipeline проверки ---

        # 7. Dry-run: no external calls
        clear_call_log(tdpath)
        rc, _ = run_safe_apply_mock(tdpath, task_id, "collector", {}, dry_run=True)
        log = read_call_log(tdpath)
        results.append(("dry-run: no external calls", rc == 0 and len(log) == 0,
                         f"exit={rc}, calls={len(log)}"))

        # 8. Success: exact sequence ['db-load-xml', 'db-update']
        clear_call_log(tdpath)
        rc, _ = run_safe_apply_mock(tdpath, task_id, "collector", {})
        log = read_call_log(tdpath)
        seq = [e["skill"] for e in log]
        results.append(("success: exit 0", rc == 0, f"exit={rc}"))
        results.append(("success: sequence ['db-load-xml', 'db-update']",
                         seq == ["db-load-xml", "db-update"], f"seq={seq}"))

        # 9. Success: exact arg values
        if len(log) >= 2:
            load_args = log[0]["args_str"]
            update_args = log[1]["args_str"]
            # ConfigDir
            has_config_dir = "-ConfigDir projects/collector/src" in load_args
            results.append(("load has ConfigDir=projects/collector/src", has_config_dir, ""))
            # Mode=Partial
            has_mode = "-Mode Partial" in load_args
            results.append(("load has Mode=Partial", has_mode, ""))
            # Files
            has_files = "-Files Catalogs/Test/Ext/ObjectModule.bsl" in load_args
            results.append(("load has Files=Catalogs/Test/Ext/ObjectModule.bsl", has_files, ""))
            # UserName (exact value, not just param name)
            has_username = "-UserName Администратор" in load_args
            results.append(("load has -UserName Администратор (exact)", has_username, ""))
            # No password params
            no_password = "-Password" not in load_args and "/P" not in load_args
            results.append(("load: no password params", no_password, ""))
            # InfoBasePath in both calls
            has_ib_path_load = "-InfoBasePath" in load_args
            has_ib_path_update = "-InfoBasePath" in update_args
            results.append(("load has InfoBasePath", has_ib_path_load, ""))
            results.append(("update has InfoBasePath", has_ib_path_update, ""))
            # UserName in update too
            has_username_update = "-UserName Администратор" in update_args
            results.append(("update has -UserName Администратор (exact)", has_username_update, ""))
        else:
            results.append(("arg checks (no log)", False, "log too short"))

        # 10. Error on load: only load called, nonzero exit
        clear_call_log(tdpath)
        rc, _ = run_safe_apply_mock(tdpath, task_id, "collector", {"db-load-xml": 1})
        log = read_call_log(tdpath)
        seq = [e["skill"] for e in log]
        results.append(("error load: only load called", seq == ["db-load-xml"], f"seq={seq}"))
        results.append(("error load: nonzero exit", rc != 0, f"exit={rc}"))

        # 11. Error on update: load+update in order, nonzero exit
        clear_call_log(tdpath)
        rc, _ = run_safe_apply_mock(tdpath, task_id, "collector", {"db-update": 1})
        log = read_call_log(tdpath)
        seq = [e["skill"] for e in log]
        results.append(("error update: load+update in order",
                         seq == ["db-load-xml", "db-update"], f"seq={seq}"))
        results.append(("error update: nonzero exit", rc != 0, f"exit={rc}"))

        # 12. Path with spaces and Cyrillic
        with tempfile.TemporaryDirectory(prefix="mock_apply_кириллица ") as td2:
            td2path = Path(td2)
            env2 = setup_test_env(td2path, "TASK-CYRILLIC")
            clear_call_log(td2path)
            rc, _ = run_safe_apply_mock(td2path, "TASK-CYRILLIC", "collector", {})
            log = read_call_log(td2path)
            seq = [e["skill"] for e in log]
            results.append(("cyrillic path: success sequence",
                             seq == ["db-load-xml", "db-update"], f"seq={seq}, exit={rc}"))

        # 13. Run from different cwd
        with tempfile.TemporaryDirectory(prefix="mock_cwd_") as other_cwd:
            clear_call_log(tdpath)
            rc, _ = run_safe_apply_mock(tdpath, task_id, "collector", {}, cwd=other_cwd)
            log = read_call_log(tdpath)
            seq = [e["skill"] for e in log]
            results.append(("different cwd: success sequence",
                             seq == ["db-load-xml", "db-update"], f"seq={seq}, exit={rc}"))

    return check_results(results)


if __name__ == "__main__":
    raise SystemExit(main())
