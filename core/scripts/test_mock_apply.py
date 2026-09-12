#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_mock_apply.py — Windows end-to-end mock тест apply pipeline.

Использует временные fake .ps1-скрипты, которые пишут в единый append-only
JSONL журнал внутри TemporaryDirectory. Требует Windows PowerShell.
На не-Windows или без PowerShell — SKIP с exit 0.

Проверяет:
1. Успешный apply: ровно db-load-xml, затем db-update (единый журнал).
2. Dry-run: изменяющих внешних вызовов нет.
3. Ошибка load: вызван только db-load-xml, update отсутствует, exit ненулевой.
4. Ошибка update: вызваны load и update в порядке, exit ненулевой.
5. Нет повторных или лишних вызовов.
6. -InfoBasePath в обоих вызовах равен точному пути тестовой базы.
7. load получает точные -ConfigDir, -Mode Partial, -Files.
8. load и update получают точное -UserName Администратор.
9. Параметры пароля отсутствуют.
10. backup_mode: external не требует backup.md.
11. production и файлы вне configSrc блокируются до первого внешнего вызова.
12. Путь с пробелами и кириллицей на Windows.
13. Запуск из другого рабочего каталога на Windows.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from scope_hash import compute_scope_hash

UNICODE_USERNAME = "Администратор"


def check_powershell() -> bool:
    """Проверить доступность Windows PowerShell."""
    if platform.system() != "Windows":
        return False
    pwsh = shutil.which("powershell") or shutil.which("pwsh")
    return pwsh is not None


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
    return {"task_id": task_id, "hash": h, "cfg": cfg}


def create_fake_skills(tdpath: Path, exit_codes: dict) -> Path:
    """Создать fake .ps1 скрипты, пишущие в единый JSONL журнал.
    Возвращает путь к файлу журнала."""
    log_file = tdpath / "call_log.jsonl"
    skills_dir = tdpath / "skills"
    log_path_str = str(log_file)
    for skill_name in ("db-load-xml", "db-update"):
        sdir = skills_dir / skill_name / "scripts"
        sdir.mkdir(parents=True, exist_ok=True)
        env_key = skill_name.upper().replace("-", "_") + "_EXIT"
        # JSONL writer: append one line per call. Log path embedded directly.
        runner_script = f'''# Fake runner for {skill_name}
$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$logFile = '{log_path_str}'
$seq = 1
if (Test-Path $logFile) {{
    $existing = Get-Content $logFile -Encoding UTF8 -ErrorAction SilentlyContinue
    if ($existing) {{ $seq = @($existing).Count + 1 }}
}}
$entry = @{{
    sequence = $seq
    skill = '{skill_name}'
    args = @($args)
}}
$json = $entry | ConvertTo-Json -Compress -Depth 3
$json | Out-File -FilePath $logFile -Encoding UTF8 -Append
$exitCode = 0
$envVal = [Environment]::GetEnvironmentVariable('{env_key}')
if ($envVal) {{ $exitCode = [int]$envVal }}
exit $exitCode
'''
        (sdir / f"{skill_name}.ps1").write_text(runner_script, encoding="utf-8-sig")
    return log_file


def read_call_log(log_file: Path) -> list:
    """Прочитать единый JSONL журнал вызовов."""
    if not log_file.exists():
        return []
    # Читаем с utf-8-sig для автоматического удаления BOM (Out-File -Encoding UTF8 добавляет BOM)
    content = log_file.read_text(encoding="utf-8-sig", errors="replace")
    entries = []
    for line in content.strip().split("\n"):
        line = line.strip().lstrip("\ufeff")
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    # Сортируем по sequence для гарантии порядка
    entries.sort(key=lambda e: e.get("sequence", 0))
    return entries


def clear_call_log(log_file: Path) -> None:
    if log_file.exists():
        log_file.unlink()


def run_guard(guard_path: Path, cfg_path: Path, specs_dir: Path, control_dir: Path,
              project_root: Path, task: str, db: str, op: str, mode: str = "Partial") -> tuple:
    cmd = [sys.executable, str(guard_path), "--config", str(cfg_path),
           "--specs-dir", str(specs_dir), "--control-dir", str(control_dir),
           "--project-root", str(project_root), "--mode", mode,
           "--task", task, "--db", db, "--op", op]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30)
    return r.returncode, r.stdout, r.stderr


def run_safe_apply(tdpath: Path, task_id: str, db_id: str,
                   exit_codes: dict, dry_run: bool = False, cwd: str = None) -> tuple:
    """Запустить safe_apply с fake skills. Возвращает (exit_code, call_log)."""
    log_file = create_fake_skills(tdpath, exit_codes)
    clear_call_log(log_file)
    safe_apply = SCRIPT_DIR / "safe_apply.py"
    cmd = [sys.executable, str(safe_apply), "--task", task_id, "--db", db_id,
           "--project-root", str(tdpath)]
    if dry_run:
        cmd.append("--dry-run")
    env = dict(os.environ)
    env["MOCK_LOG_DIR"] = str(tdpath)
    for skill_name, code in exit_codes.items():
        env_key = skill_name.upper().replace("-", "_") + "_EXIT"
        env[env_key] = str(code)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd or str(tdpath), env=env, timeout=60)
    log = read_call_log(log_file)
    return r.returncode, log


def get_arg_value(call: dict, param: str) -> str:
    """Извлечь значение параметра из списка аргументов вызова."""
    args = call.get("args", [])
    for i, a in enumerate(args):
        if a == param and i + 1 < len(args):
            return str(args[i + 1])
    return ""


def has_param(call: dict, param: str) -> bool:
    """Проверить наличие параметра в списке аргументов."""
    args = call.get("args", [])
    return param in [str(a) for a in args]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if not check_powershell():
        print("SKIP: Windows PowerShell required")
        return 0

    guard = SCRIPT_DIR / "applier_guard.py"
    results = []

    with tempfile.TemporaryDirectory(prefix="mock_apply_") as td:
        tdpath = Path(td)
        env_data = setup_test_env(tdpath)
        task_id = env_data["task_id"]
        cfg_path = tdpath / ".v8-project.json"
        specs_dir = tdpath / "specs"
        control_dir = tdpath / "pilot-control"
        project_root = tdpath
        expected_ib_path = str(tdpath / "fake_base")

        # --- Guard проверки ---

        rc, _, _ = run_guard(guard, cfg_path, specs_dir, control_dir, project_root, task_id, "collector", "load-xml")
        results.append(("guard load-xml -> exit 0", rc == 0, f"exit={rc}"))

        rc, _, _ = run_guard(guard, cfg_path, specs_dir, control_dir, project_root, task_id, "collector", "update")
        results.append(("guard update -> exit 0", rc == 0, f"exit={rc}"))

        backup_md = control_dir / task_id / "backup.md"
        rc, _, _ = run_guard(guard, cfg_path, specs_dir, control_dir, project_root, task_id, "collector", "load-xml")
        results.append(("backup_mode external: no backup.md", not backup_md.exists() and rc == 0, ""))

        # Production blocked
        prod_cfg = {"databases": [{"id": "prod", "type": "file", "path": ".\\prod",
                     "environment": "production", "password_mode": "none", "backup_mode": "external"}]}
        prod_cfg_path = tdpath / "prod_cfg.json"
        prod_cfg_path.write_text(json.dumps(prod_cfg), encoding="utf-8")
        rc, _, _ = run_guard(guard, prod_cfg_path, specs_dir, control_dir, project_root, task_id, "prod", "update")
        results.append(("production blocked", rc != 0, f"exit={rc}"))

        # Unknown password_mode blocked
        bad_cfg = {"databases": [{"id": "bad", "type": "file", "path": ".\\bad",
                    "environment": "local", "password_mode": "unknown", "backup_mode": "external"}]}
        bad_cfg_path = tdpath / "bad_cfg.json"
        bad_cfg_path.write_text(json.dumps(bad_cfg), encoding="utf-8")
        rc, _, _ = run_guard(guard, bad_cfg_path, specs_dir, control_dir, project_root, task_id, "bad", "update")
        results.append(("unknown password_mode blocked", rc != 0, f"exit={rc}"))

        # Files outside configSrc blocked
        report_path = specs_dir / task_id / "06_change_report.md"
        spec_path = specs_dir / task_id / "03_solution_spec.md"
        review_path = control_dir / task_id / "review.md"
        old_h = env_data["hash"]
        report_text = report_path.read_text(encoding="utf-8")
        spec_text = spec_path.read_text(encoding="utf-8")
        bad_file = "projects/other/src/Hack.bsl"
        good_file = "projects/collector/src/Catalogs/Test/Ext/ObjectModule.bsl"
        bad_report = report_text.replace(f"- {good_file}", f"- {bad_file}\n- {good_file}")
        bad_spec = spec_text.replace(f"- {good_file}", f"- {bad_file}\n- {good_file}")
        new_h = compute_scope_hash(bad_spec)
        bad_report = bad_report.replace(old_h, new_h)
        bad_spec = bad_spec.replace(old_h, new_h)
        report_path.write_text(bad_report, encoding="utf-8")
        spec_path.write_text(bad_spec, encoding="utf-8")
        review_path.write_text(review_path.read_text(encoding="utf-8").replace(old_h, new_h), encoding="utf-8")
        (tdpath / "projects" / "other" / "src").mkdir(parents=True, exist_ok=True)
        (tdpath / "projects" / "other" / "src" / "Hack.bsl").write_text("// hack\n", encoding="utf-8")
        rc, _, _ = run_guard(guard, cfg_path, specs_dir, control_dir, project_root, task_id, "collector", "load-xml")
        results.append(("files outside configSrc blocked", rc != 0, f"exit={rc}"))
        # Restore
        report_path.write_text(report_text, encoding="utf-8")
        spec_path.write_text(spec_text, encoding="utf-8")
        review_path.write_text(review_path.read_text(encoding="utf-8").replace(new_h, old_h), encoding="utf-8")

        # --- safe_apply pipeline проверки ---

        # Dry-run: no external calls
        rc, log = run_safe_apply(tdpath, task_id, "collector", {}, dry_run=True)
        results.append(("dry-run: no external calls", rc == 0 and len(log) == 0,
                         f"exit={rc}, calls={len(log)}"))

        # Success: exact sequence
        rc, log = run_safe_apply(tdpath, task_id, "collector", {})
        seq = [e["skill"] for e in log]
        results.append(("success: exit 0", rc == 0, f"exit={rc}"))
        results.append(("success: sequence [db-load-xml, db-update]",
                         seq == ["db-load-xml", "db-update"], f"seq={seq}"))
        results.append(("success: no extra calls", len(log) == 2, f"count={len(log)}"))

        # Exact arg values for success
        if len(log) >= 2:
            load_call = log[0]
            update_call = log[1]

            # -InfoBasePath exact value in both calls
            load_ib = get_arg_value(load_call, "-InfoBasePath")
            update_ib = get_arg_value(update_call, "-InfoBasePath")
            results.append(("load -InfoBasePath exact", load_ib == expected_ib_path, f"got={load_ib}"))
            results.append(("update -InfoBasePath exact", update_ib == expected_ib_path, f"got={update_ib}"))

            # -ConfigDir exact
            load_cd = get_arg_value(load_call, "-ConfigDir")
            results.append(("load -ConfigDir exact", load_cd == "projects/collector/src", f"got={load_cd}"))

            # -Mode Partial
            load_mode = get_arg_value(load_call, "-Mode")
            results.append(("load -Mode Partial", load_mode == "Partial", f"got={load_mode}"))

            # -Files exact
            load_files = get_arg_value(load_call, "-Files")
            results.append(("load -Files exact",
                             load_files == "Catalogs/Test/Ext/ObjectModule.bsl", f"got={load_files}"))

            # -UserName exact in both
            load_user = get_arg_value(load_call, "-UserName")
            update_user = get_arg_value(update_call, "-UserName")
            results.append(("load -UserName exact", load_user == UNICODE_USERNAME, f"got={load_user}"))
            results.append(("update -UserName exact", update_user == UNICODE_USERNAME, f"got={update_user}"))

            # No password params in either
            load_no_pw = not has_param(load_call, "-Password") and not has_param(load_call, "-PasswordEnv") and not has_param(load_call, "/P")
            update_no_pw = not has_param(update_call, "-Password") and not has_param(update_call, "-PasswordEnv") and not has_param(update_call, "/P")
            results.append(("load: no password params", load_no_pw, ""))
            results.append(("update: no password params", update_no_pw, ""))
        else:
            for n in range(10):
                results.append((f"arg checks (skipped, log short)", False, ""))

        # Error on load: only load called
        rc, log = run_safe_apply(tdpath, task_id, "collector", {"db-load-xml": 1})
        seq = [e["skill"] for e in log]
        results.append(("error load: only load called", seq == ["db-load-xml"], f"seq={seq}"))
        results.append(("error load: nonzero exit", rc != 0, f"exit={rc}"))

        # Error on update: load+update in order
        rc, log = run_safe_apply(tdpath, task_id, "collector", {"db-update": 1})
        seq = [e["skill"] for e in log]
        results.append(("error update: load+update in order",
                         seq == ["db-load-xml", "db-update"], f"seq={seq}"))
        results.append(("error update: nonzero exit", rc != 0, f"exit={rc}"))

        # Path with spaces and Cyrillic
        with tempfile.TemporaryDirectory(prefix="кириллица пробел ") as td2:
            td2path = Path(td2)
            env2 = setup_test_env(td2path, "TASK-CYRILLIC")
            rc, log = run_safe_apply(td2path, "TASK-CYRILLIC", "collector", {})
            seq = [e["skill"] for e in log]
            results.append(("cyrillic+spaces path: success sequence",
                             seq == ["db-load-xml", "db-update"], f"seq={seq}, exit={rc}"))

        # Run from different cwd
        with tempfile.TemporaryDirectory(prefix="other_cwd_") as other_cwd:
            rc, log = run_safe_apply(tdpath, task_id, "collector", {}, cwd=other_cwd)
            seq = [e["skill"] for e in log]
            results.append(("different cwd: success sequence",
                             seq == ["db-load-xml", "db-update"], f"seq={seq}, exit={rc}"))

    # Print results
    print("=== Mock Apply Integration Test (Windows PowerShell) ===")
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


if __name__ == "__main__":
    raise SystemExit(main())
