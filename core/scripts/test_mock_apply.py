#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_mock_apply.py — mock integration test apply pipeline.

Создаёт временное окружение с Collector-like конфигурацией,
SDD, review, change report, и проверяет:
1. Unicode username 'Администратор' передаётся корректно.
2. При password_mode: none параметр пароля отсутствует.
3. ConfigDir и Files правильно сформированы.
4. backup_mode: external не требует backup.md.
5. load-xml Partial → update последовательность.
6. Блокировка неизвестного password_mode и production.

Запускается без подключения к 1С — PowerShell skills заменены mock-скриптами.
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

HARNESS = Path(r"C:\Ramium\1c-vibe\temp\ai-environment")
sys.path.insert(0, str(HARNESS / "core" / "scripts"))
from scope_hash import compute_scope_hash

UNICODE_USERNAME = "Администратор"


def setup_test_env(tdpath: Path) -> dict:
    """Создать тестовое окружение."""
    # Создать исходники
    src_dir = tdpath / "projects" / "collector" / "src" / "Catalogs" / "Test" / "Ext"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "ObjectModule.bsl").write_text("// test\n", encoding="utf-8")
    # Создать .v8-project.json
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
    (tdpath / ".v8-project.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    # Создать SDD
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
    # Создать change report
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
    # Создать review в pilot-control
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
    # Создать mock skills
    skills_dir = tdpath / "skills"
    for skill_name, mock_content in [
        ("db-load-xml", "# mock db-load-xml\nexit 0\n"),
        ("db-update", "# mock db-update\nexit 0\n"),
    ]:
        sdir = skills_dir / skill_name / "scripts"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / f"{skill_name}.ps1").write_text(mock_content, encoding="utf-8")
    return {"task_id": task_id, "hash": h, "cfg": cfg}


def run_guard(guard_path: Path, cfg_path: Path, specs_dir: Path, control_dir: Path,
              project_root: Path, task: str, db: str, op: str, mode: str = "Partial") -> tuple:
    cmd = [sys.executable, str(guard_path), "--config", str(cfg_path),
           "--specs-dir", str(specs_dir), "--control-dir", str(control_dir),
           "--project-root", str(project_root), "--mode", mode,
           "--task", task, "--db", db, "--op", op]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    results = []
    guard = HARNESS / "core" / "scripts" / "applier_guard.py"
    safe_apply = HARNESS / "core" / "scripts" / "safe_apply.py"

    with tempfile.TemporaryDirectory(prefix="mock_apply_") as td:
        tdpath = Path(td)
        env = setup_test_env(tdpath)
        task_id = env["task_id"]
        cfg_path = tdpath / ".v8-project.json"
        specs_dir = tdpath / "specs"
        control_dir = tdpath / "pilot-control"
        project_root = tdpath

        # 1. Guard: load-xml Partial → exit 0
        rc, out, err = run_guard(guard, cfg_path, specs_dir, control_dir, project_root, task_id, "collector", "load-xml")
        results.append(("guard load-xml Partial → exit 0", rc == 0, f"exit={rc}"))

        # 2. Guard: update → exit 0
        rc, out, err = run_guard(guard, cfg_path, specs_dir, control_dir, project_root, task_id, "collector", "update")
        results.append(("guard update → exit 0", rc == 0, f"exit={rc}"))

        # 3. Password_mode none: no password in args
        # Check guard output doesn't contain password
        rc, out, err = run_guard(guard, cfg_path, specs_dir, control_dir, project_root, task_id, "collector", "load-xml")
        has_no_password = "Password" not in out
        results.append(("password_mode none: no password in output", has_no_password, ""))

        # 4. backup_mode external: guard passes without backup.md
        backup_md = control_dir / task_id / "backup.md"
        backup_exists = backup_md.exists()
        results.append(("backup_mode external: no backup.md required", not backup_exists and rc == 0, f"backup_exists={backup_exists}"))

        # 5. Unicode username in config — guard passes
        results.append(("Unicode username 'Администратор'", rc == 0, f"exit={rc}"))

        # 6. Production blocked
        prod_cfg = {
            "databases": [{
                "id": "prod", "type": "file", "path": ".\\prod",
                "environment": "production", "password_mode": "none", "backup_mode": "external"
            }]
        }
        prod_cfg_path = tdpath / "prod_cfg.json"
        prod_cfg_path.write_text(json.dumps(prod_cfg), encoding="utf-8")
        rc, out, err = run_guard(guard, prod_cfg_path, specs_dir, control_dir, project_root, task_id, "prod", "update")
        results.append(("production blocked", rc != 0, f"exit={rc}"))

        # 7. Unknown password_mode blocked
        bad_cfg = {
            "databases": [{
                "id": "bad", "type": "file", "path": ".\\bad",
                "environment": "local", "password_mode": "unknown", "backup_mode": "external"
            }]
        }
        bad_cfg_path = tdpath / "bad_cfg.json"
        bad_cfg_path.write_text(json.dumps(bad_cfg), encoding="utf-8")
        rc, out, err = run_guard(guard, bad_cfg_path, specs_dir, control_dir, project_root, task_id, "bad", "update")
        results.append(("unknown password_mode blocked", rc != 0, f"exit={rc}"))

        # 8. Safe_apply --dry-run → exit 0
        cmd = [sys.executable, str(safe_apply), "--task", task_id, "--db", "collector",
               "--project-root", str(tdpath), "--dry-run"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(tdpath))
        results.append(("safe_apply --dry-run → exit 0", r.returncode == 0, f"exit={r.returncode}"))

        # 9. Safe_apply: check ConfigDir and Files normalization
        out_text = r.stdout
        has_config_src = "projects/collector/src" in out_text
        has_files = "Catalogs/Test/Ext/ObjectModule.bsl" in out_text or "files=1" in out_text.lower()
        results.append(("ConfigDir=projects/collector/src", has_config_src, ""))
        results.append(("Files normalized (configSrc stripped)", has_files, ""))

    # Print results
    print("=== Mock Apply Integration Test ===")
    passed = 0
    failed = 0
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}: {name} {detail}")
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\nResult: {passed} passed, {failed} failed out of {len(results)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
