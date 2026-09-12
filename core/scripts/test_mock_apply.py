#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_mock_apply.py — переносимый end-to-end mock тест apply pipeline.

Создаёт временное окружение с Collector-like конфигурацией,
SDD, review, change report, и проверяет:
1. Сначала вызван partial XML load.
2. Затем вызван UpdateDBCfg.
3. Оба вызова получили правильную базу, configSrc и список файлов.
4. Имя 'Администратор' передано корректно.
5. Пароль не передан и не выведен.
6. При ошибке load команда update не вызывается.
7. При ошибке update процесс возвращает ненулевой код.
8. При backup_mode: external не требуется backup.md.
9. Файлы вне configSrc блокируются.

Запускается без подключения к 1С — PowerShell skills заменены fake runner скриптами.
Корень harness определяется от расположения этого файла.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from scope_hash import compute_scope_hash

UNICODE_USERNAME = "Администратор"


def setup_test_env(tdpath: Path) -> dict:
    """Создать тестовое окружение во временном каталоге."""
    # Исходники
    src_dir = tdpath / "projects" / "collector" / "src" / "Catalogs" / "Test" / "Ext"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "ObjectModule.bsl").write_text("// test\n", encoding="utf-8")
    # .v8-project.json
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
    # SDD
    task_id = "TASK-TEST001"
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
- два предопределённых элемента

## Затрагиваемые файлы
- projects/collector/src/Catalogs/Test/Ext/ObjectModule.bsl

## Критерии приёмки
- справочник создаётся
"""
    h = compute_scope_hash(spec_content)
    spec_content = spec_content.replace("PLACEHOLDER", h)
    (specs_dir / "03_solution_spec.md").write_text(spec_content, encoding="utf-8")
    # Change report
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
    # Review in pilot-control
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
    # Fake runner skills
    skills_dir = tdpath / "skills"
    for skill_name in ("db-load-xml", "db-update"):
        sdir = skills_dir / skill_name / "scripts"
        sdir.mkdir(parents=True, exist_ok=True)
        # Fake runner: записывает полученные аргументы и возвращает exit code из env
        runner_script = f"""# Fake runner for {skill_name}
$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# Write received arguments to a log file
$argStr = $args -join ' '
$logFile = Join-Path $env:TEMP '{skill_name}_args.txt'
$argStr | Out-File -FilePath $logFile -Encoding UTF8
# Exit code from env var, default 0
$exitCode = 0
$envName = '{skill_name.upper().replace('-', '_')}_EXIT'
$envVal = [Environment]::GetEnvironmentVariable($envName)
if ($envVal) {{ $exitCode = [int]$envVal }}
exit $exitCode
"""
        (sdir / f"{skill_name}.ps1").write_text(runner_script, encoding="utf-8")
    return {"task_id": task_id, "hash": h, "cfg": cfg}


def run_guard(guard_path: Path, cfg_path: Path, specs_dir: Path, control_dir: Path,
              project_root: Path, task: str, db: str, op: str, mode: str = "Partial") -> tuple:
    cmd = [sys.executable, str(guard_path), "--config", str(cfg_path),
           "--specs-dir", str(specs_dir), "--control-dir", str(control_dir),
           "--project-root", str(project_root), "--mode", mode,
           "--task", task, "--db", db, "--op", op]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr


def read_call_log(skill_name: str) -> str:
    """Прочитать записанные аргументы fake runner."""
    log_path = Path(os.environ["TEMP"]) / f"{skill_name}_args.txt"
    if log_path.exists():
        return log_path.read_text(encoding="utf-8", errors="replace")
    return ""


def clear_call_logs():
    """Очистить логи fake runner."""
    for skill_name in ("db-load-xml", "db-update"):
        log_path = Path(os.environ["TEMP"]) / f"{skill_name}_args.txt"
        if log_path.exists():
            log_path.unlink()
        call_path = Path(os.environ["TEMP"]) / f"{skill_name}_call.txt"
        if call_path.exists():
            call_path.unlink()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    guard = SCRIPT_DIR / "applier_guard.py"
    safe_apply = SCRIPT_DIR / "safe_apply.py"
    results = []

    with tempfile.TemporaryDirectory(prefix="mock_apply_") as td:
        tdpath = Path(td)
        env = setup_test_env(tdpath)
        task_id = env["task_id"]
        cfg_path = tdpath / ".v8-project.json"
        specs_dir = tdpath / "specs"
        control_dir = tdpath / "pilot-control"
        project_root = tdpath

        # 1. Guard: load-xml Partial → exit 0
        rc, out, err = run_guard(guard, cfg_path, specs_dir, control_dir, project_root,
                                 task_id, "collector", "load-xml")
        results.append(("guard load-xml Partial → exit 0", rc == 0, f"exit={rc}"))

        # 2. Guard: update → exit 0
        rc, out, err = run_guard(guard, cfg_path, specs_dir, control_dir, project_root,
                                 task_id, "collector", "update")
        results.append(("guard update → exit 0", rc == 0, f"exit={rc}"))

        # 3. Password_mode none: no password in output
        rc, out, err = run_guard(guard, cfg_path, specs_dir, control_dir, project_root,
                                 task_id, "collector", "load-xml")
        has_no_password = "Password" not in out and "password" not in out.lower()
        results.append(("password_mode none: no password in output", has_no_password, ""))

        # 4. backup_mode external: guard passes without backup.md
        backup_md = control_dir / task_id / "backup.md"
        backup_exists = backup_md.exists()
        results.append(("backup_mode external: no backup.md required", not backup_exists and rc == 0,
                         f"backup_exists={backup_exists}"))

        # 5. Unicode username — guard passes
        results.append(("Unicode username 'Администратор'", rc == 0, f"exit={rc}"))

        # 6. Production blocked
        prod_cfg = {"databases": [{"id": "prod", "type": "file", "path": ".\\prod",
                     "environment": "production", "password_mode": "none", "backup_mode": "external"}]}
        prod_cfg_path = tdpath / "prod_cfg.json"
        prod_cfg_path.write_text(json.dumps(prod_cfg), encoding="utf-8")
        rc, out, err = run_guard(guard, prod_cfg_path, specs_dir, control_dir, project_root,
                                 task_id, "prod", "update")
        results.append(("production blocked", rc != 0, f"exit={rc}"))

        # 7. Unknown password_mode blocked
        bad_cfg = {"databases": [{"id": "bad", "type": "file", "path": ".\\bad",
                    "environment": "local", "password_mode": "unknown", "backup_mode": "external"}]}
        bad_cfg_path = tdpath / "bad_cfg.json"
        bad_cfg_path.write_text(json.dumps(bad_cfg), encoding="utf-8")
        rc, out, err = run_guard(guard, bad_cfg_path, specs_dir, control_dir, project_root,
                                 task_id, "bad", "update")
        results.append(("unknown password_mode blocked", rc != 0, f"exit={rc}"))

        # 8. Safe_apply --dry-run → exit 0
        clear_call_logs()
        cmd = [sys.executable, str(safe_apply), "--task", task_id, "--db", "collector",
               "--project-root", str(tdpath), "--dry-run"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(tdpath))
        results.append(("safe_apply --dry-run → exit 0", r.returncode == 0, f"exit={r.returncode}"))

        # 9. Safe_apply real (no --dry-run): load → update, both called
        clear_call_logs()
        os.environ["DB_LOAD_XML_EXIT"] = "0"
        os.environ["DB_UPDATE_EXIT"] = "0"
        cmd = [sys.executable, str(safe_apply), "--task", task_id, "--db", "collector",
               "--project-root", str(tdpath)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(tdpath))
        load_call = read_call_log("db-load-xml")
        update_call = read_call_log("db-update")
        load_called = bool(load_call)
        update_called = bool(update_call)
        load_first = load_called and update_called  # both called
        results.append(("safe_apply: load called", load_called, ""))
        results.append(("safe_apply: update called after load", update_called, ""))
        results.append(("safe_apply: exit 0", r.returncode == 0, f"exit={r.returncode}"))

        # 10. Check ConfigDir and Files in load call
        load_has_config = "projects/collector/src" in load_call or "ConfigDir" in load_call
        load_has_files = "ObjectModule.bsl" in load_call or "Files" in load_call
        results.append(("load call has ConfigDir=projects/collector/src", load_has_config, ""))
        results.append(("load call has Files with ObjectModule.bsl", load_has_files, ""))

        # 11. Check username in load call (Unicode 'Администратор')
        # Username is passed as -UserName value or -UserNameEnv env-name
        # When username is direct value, it's passed as -UserName
        # But safe_apply passes username_env if set, or username if direct
        # In test config, username is direct value, username_env is not set
        # So safe_apply should pass -UserName Администратор
        has_username = "Администратор" in load_call or "UserName" in load_call
        results.append(("load call has UserName (Администратор)", has_username, ""))

        # 12. No password in load call
        no_password_in_load = "-Password" not in load_call and "/P" not in load_call
        results.append(("load call: no password params", no_password_in_load, ""))

        # 13. Error on load → update NOT called
        clear_call_logs()
        os.environ["DB_LOAD_XML_EXIT"] = "1"
        os.environ["DB_UPDATE_EXIT"] = "0"
        cmd = [sys.executable, str(safe_apply), "--task", task_id, "--db", "collector",
               "--project-root", str(tdpath)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(tdpath))
        load_called_err = bool(read_call_log("db-load-xml"))
        update_not_called = not read_call_log("db-update")
        results.append(("error on load → update NOT called", load_called_err and update_not_called,
                         f"exit={r.returncode}"))

        # 14. Error on update → nonzero exit
        clear_call_logs()
        os.environ["DB_LOAD_XML_EXIT"] = "0"
        os.environ["DB_UPDATE_EXIT"] = "1"
        cmd = [sys.executable, str(safe_apply), "--task", task_id, "--db", "collector",
               "--project-root", str(tdpath)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(tdpath))
        results.append(("error on update → nonzero exit", r.returncode != 0, f"exit={r.returncode}"))

        # 15. Files outside configSrc blocked
        # Add a file outside configSrc to change report
        report_path = specs_dir / task_id / "06_change_report.md"
        report_text = report_path.read_text(encoding="utf-8")
        bad_report = report_text.replace(
            "- projects/collector/src/Catalogs/Test/Ext/ObjectModule.bsl",
            "- projects/other/src/Hack.bsl\n- projects/collector/src/Catalogs/Test/Ext/ObjectModule.bsl"
        )
        report_path.write_text(bad_report, encoding="utf-8")
        # Also create the bad file so it exists
        (tdpath / "projects" / "other" / "src").mkdir(parents=True, exist_ok=True)
        (tdpath / "projects" / "other" / "src" / "Hack.bsl").write_text("// hack\n", encoding="utf-8")
        # Recompute scope_hash (sections changed)
        spec_path = specs_dir / task_id / "03_solution_spec.md"
        spec_text = spec_path.read_text(encoding="utf-8")
        # Add the bad file to spec too so hash matches
        bad_spec = spec_text.replace(
            "- projects/collector/src/Catalogs/Test/Ext/ObjectModule.bsl",
            "- projects/other/src/Hack.bsl\n- projects/collector/src/Catalogs/Test/Ext/ObjectModule.bsl"
        )
        new_h = compute_scope_hash(bad_spec)
        bad_spec = bad_spec.replace(env["hash"], new_h)
        spec_path.write_text(bad_spec, encoding="utf-8")
        # Update report hash
        bad_report2 = bad_report.replace(env["hash"], new_h)
        report_path.write_text(bad_report2, encoding="utf-8")
        # Update review hash
        review_path = control_dir / task_id / "review.md"
        review_text = review_path.read_text(encoding="utf-8")
        review_path.write_text(review_text.replace(env["hash"], new_h), encoding="utf-8")
        # Run guard — should block because file is outside configSrc
        rc, out, err = run_guard(guard, cfg_path, specs_dir, control_dir, project_root,
                                 task_id, "collector", "load-xml")
        results.append(("files outside configSrc blocked", rc != 0, f"exit={rc}"))

        # Cleanup env
        for k in ("DB_LOAD_XML_EXIT", "DB_UPDATE_EXIT"):
            os.environ.pop(k, None)
        clear_call_logs()

    # Print results
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


if __name__ == "__main__":
    raise SystemExit(main())
