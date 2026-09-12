#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
safe_backup.py — trusted backup wrapper.

Создаёт backup информационной базы и атомарно формирует
pilot-control/<TASK-ID>/backup.md с метаданными.

1. Получает TASK-ID и database id.
2. Проверяет environment, однозначность базы, запрет production.
3. Выполняет backup через allowlisted skill (db-dump-dt).
4. Проверяет exit code и существование непустого artifact.
5. Вычисляет SHA-256 самостоятельно.
6. Атомарно создаёт backup.md только после успешного backup.
7. Не выводит логин, пароль, строку подключения.
8. Не принимает от агента готовые status/hash/artifact.

Exit codes: 0 = OK, 1 = backup failed, 2 = config error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _root import find_root as _find_root_shared
except ImportError:
    _find_root_shared = None
try:
    from scope_hash import validate_hash_format
except ImportError:
    validate_hash_format = None


def _find_root(start: Path) -> Path:
    if _find_root_shared is not None:
        root = _find_root_shared(start)
        if root is not None:
            return root
    return start.resolve().parent.parent


ROOT = _find_root(Path(__file__).resolve())
ALLOWED_ENVS = {"local", "test", "staging"}
BACKUP_ROOT = "backups"  # относительный путь внутри project root
_TASK_ID_RE = __import__("re").compile(r"^TASK-[A-Za-z0-9]+(-[A-Za-z0-9]+)*$")


def redact(text: str) -> str:
    import re
    for param in ("-Password", "/P", "--password", "-PasswordEnv", "-UserName", "-UserNameEnv", "-V8Path"):
        text = re.sub(rf"({re.escape(param)}\s+)([^\s-]+)", r"\1***REDACTED***", text)
    return text


def find_skills_dir(root: Path) -> Path:
    for c in [root / ".kilo" / "skills", root / ".claude" / "skills",
              root / ".openworks" / "skills", root / "skills"]:
        if c.is_dir():
            return c
    return root / ".kilo" / "skills"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="safe_backup.py",
        description="Trusted backup wrapper — создаёт backup и формирует pilot-control/<TASK-ID>/backup.md",
    )
    parser.add_argument("--task", required=True, help="TASK-ID (строгий формат TASK-<alnum>)")
    parser.add_argument("--db", required=True, help="id базы из .v8-project.json")
    parser.add_argument("--project-root", default="", help="явный корень проекта")
    parser.add_argument("--config", default="", help="путь к .v8-project.json")
    parser.add_argument("--dry-run", action="store_true", help="только проверки, без backup")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve() if args.project_root else ROOT
    config_path = Path(args.config).resolve() if args.config else (project_root / ".v8-project.json")
    control_dir = project_root / "pilot-control"
    task_id = args.task.strip()
    db_id = args.db.strip()

    # 1. Валидация TASK-ID
    if not task_id or not _TASK_ID_RE.match(task_id):
        print(f"ERROR: некорректный TASK-ID '{task_id}'", file=sys.stderr)
        return 2

    # 2. Чтение конфигурации
    if not config_path.exists():
        print(f"ERROR: .v8-project.json не найден: {config_path}", file=sys.stderr)
        return 2
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception as e:
        print(f"ERROR: конфиг не читается: {redact(str(e))}", file=sys.stderr)
        return 2

    dbs = cfg.get("databases") or []
    matches = [d for d in dbs if isinstance(d, dict) and d.get("id") == db_id]
    if not matches:
        print(f"ERROR: база '{db_id}' не найдена", file=sys.stderr)
        return 2
    if len(matches) > 1:
        print(f"ERROR: несколько записей с id '{db_id}'", file=sys.stderr)
        return 2
    db = matches[0]

    if "user" in db or "password" in db:
        print("ERROR: plaintext user/password — мигрируйте конфигурацию", file=sys.stderr)
        return 2

    db_env = str(db.get("environment", "")).strip()
    if not db_env:
        print(f"ERROR: база '{db_id}' без per-db environment", file=sys.stderr)
        return 2
    if db_env == "production":
        print(f"ERROR: production — backup через wrapper запрещён", file=sys.stderr)
        return 2
    if db_env not in ALLOWED_ENVS:
        print(f"ERROR: неизвестное environment '{db_env}'", file=sys.stderr)
        return 2

    # 3. Проверить, что backup.md ещё не существует (или --force)
    backup_md_path = control_dir / task_id / "backup.md"
    if backup_md_path.exists():
        print(f"ERROR: backup.md уже существует: {backup_md_path} — удалите вручную или используйте отдельное решение", file=sys.stderr)
        return 1

    if args.dry_run:
        print("OK: safe_backup --dry-run — проверки пройдены, backup не выполняется")
        return 0

    # 4. Найти skill-скрипт db-dump-dt
    skills_dir = find_skills_dir(project_root)
    skill_script = skills_dir / "db-dump-dt" / "scripts" / "db-dump-dt.ps1"
    if not skill_script.exists():
        print(f"ERROR: skill-скрипт не найден: {skill_script}", file=sys.stderr)
        return 2

    # 5. Сформировать аргументы для backup
    v8path = str(cfg.get("v8path", "")).strip()
    ps_args: list[str] = []
    if v8path:
        ps_args.extend(["-V8Path", v8path])
    db_type = str(db.get("type", "")).strip().lower()
    if db_type == "server":
        if db.get("server"): ps_args.extend(["-InfoBaseServer", str(db["server"])])
        if db.get("ref"): ps_args.extend(["-InfoBaseRef", str(db["ref"])])
    else:
        if db.get("path"): ps_args.extend(["-InfoBasePath", str(db["path"])])
    username_env = str(db.get("username_env", "")).strip()
    password_mode = str(db.get("password_mode", "")).strip().lower()
    if username_env:
        ps_args.extend(["-UserNameEnv", username_env])
    if password_mode == "env":
        pw_env = str(db.get("password_env", "")).strip()
        if pw_env:
            ps_args.extend(["-PasswordEnv", pw_env])
    # password_mode == "none" — не передаём password

    # 6. Путь артефакта backup
    backup_dir = project_root / BACKUP_ROOT
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_rel = f"{BACKUP_ROOT}/{db_id}_{task_id}_{ts}.dt"
    artifact_path = project_root / artifact_rel
    ps_args.extend(["-OutputFile", str(artifact_path)])

    # 7. Запустить backup skill
    pwsh = os.environ.get("PWSH", "powershell")
    cmd = [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(skill_script)] + ps_args
    print(f"=== safe_backup: запуск db-dump-dt ===")
    print(f"  args: {redact(' '.join(ps_args))}")

    try:
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print(f"=== safe_backup: backup FAILED (exit {r.returncode}) ===", file=sys.stderr)
            return 1
    except FileNotFoundError:
        print(f"ERROR: PowerShell не найден ({pwsh})", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {redact(str(e))}", file=sys.stderr)
        return 2

    # 8. Проверить существование и непустоту artifact
    if not artifact_path.exists():
        print(f"ERROR: artifact не создан: {artifact_rel}", file=sys.stderr)
        return 1
    if not artifact_path.is_file():
        print(f"ERROR: artifact не является обычным файлом: {artifact_rel}", file=sys.stderr)
        return 1
    if artifact_path.stat().st_size == 0:
        print(f"ERROR: artifact пуст (нулевой размер): {artifact_rel}", file=sys.stderr)
        return 1

    # 9. Вычислить SHA-256
    artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()

    # 10. Атомарно создать backup.md
    backup_md_dir = control_dir / task_id
    backup_md_dir.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    backup_md_content = f"""# Backup: {task_id}

```yaml
backup_version: 1
database_id: {db_id}
environment: {db_env}
created_at: {now_iso}
artifact: {artifact_rel}
artifact_sha256: {artifact_sha}
status: success
```
"""
    tmp_path = backup_md_path.with_suffix(".tmp")
    tmp_path.write_text(backup_md_content, encoding="utf-8")
    tmp_path.replace(backup_md_path)

    print(f"=== safe_backup: backup создан успешно ===")
    print(f"  artifact: {artifact_rel}")
    print(f"  sha256: {artifact_sha}")
    print(f"  backup.md: {backup_md_path.relative_to(project_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
