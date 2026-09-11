#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
safe_apply.py — единая безопасная точка входа для опасных операций apply.

Wrapper: запускает applier_guard.py (preflight) и ТОЛЬКО при успешном
прохождении вызывает skill-скрипт.

Безопасность секретов (P0-5):
  - Пароль НИКОГДА не передаётся как аргумент PowerShell.
  - Python передаёт имя env-переменной через -UserNameEnv/-PasswordEnv.
  - PowerShell-скрипт читает значение из $env:<NAME> перед запуском платформы.
  - Единая функция redact() маскирует все чувствительные значения в выводе.

P0-6: load-xml и update разделены. --update-db удалён.
  - load-xml загружает только XML, без обновления БД.
  - update — отдельная операция с отдельным guard.

P0-7: особо опасные операции (load-dt, create, load-cf, web-*) заблокированы.

Exit codes:
  0 — операция выполнена успешно
  1 — guard заблокировал операцию
  2 — ошибка аргументов / конфигурации
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _root import find_root as _find_root_shared
except ImportError:
    _find_root_shared = None


def _find_root(start: Path) -> Path:
    if _find_root_shared is not None:
        root = _find_root_shared(start)
        if root is not None:
            return root
    return start.resolve().parent.parent


ROOT = _find_root(Path(__file__).resolve())

# P0-7: только разрешённые операции для первого пилота
ALLOWED_OPS = {"load-xml", "update"}
# Особо опасные — заблокированы независимо от approval
BLOCKED_OPS = {"load-dt", "create", "load-cf", "web-publish", "web-unpublish"}

OP_TO_SKILL = {
    "load-xml": "db-load-xml",
    "load-cf": "db-load-cf",
    "load-dt": "db-load-dt",
    "update": "db-update",
    "create": "db-create",
}

SENSITIVE_PARAMS = {"-Password", "/P", "--password", "-PasswordEnv", "-UserName", "-UserNameEnv", "-V8Path"}


def redact(text: str) -> str:
    """Единая функция redaction: маскирует значения после чувствительных параметров."""
    if not text:
        return text
    for param in SENSITIVE_PARAMS:
        text = re.sub(
            rf"({re.escape(param)}\s+)([^\s-]+)",
            r"\1***REDACTED***",
            text,
        )
    text = re.sub(r"(Password=)[^;\s]+", r"\1***REDACTED***", text, flags=re.IGNORECASE)
    text = re.sub(r"(Usr=)[^;\s]+", r"\1***REDACTED***", text, flags=re.IGNORECASE)
    return text


def find_skills_dir(root: Path) -> Path:
    candidates = [
        root / ".kilo" / "skills",
        root / ".claude" / "skills",
        root / ".openworks" / "skills",
        root / "skills",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return root / ".kilo" / "skills"


def run_guard(task: str, db: str, op: str, mode: str, files: str, project_root: str) -> int:
    """Запустить applier_guard.py как subprocess."""
    guard = ROOT / "scripts" / "applier_guard.py"
    if not guard.exists():
        guard = ROOT / "core" / "scripts" / "applier_guard.py"
    cmd = [
        sys.executable, str(guard),
        "--task", task,
        "--db", db,
        "--op", op,
        "--mode", mode,
    ]
    if files:
        cmd += ["--files", files]
    if project_root:
        cmd += ["--project-root", project_root]
    print("=== safe_apply: preflight guard ===")
    print(f"  cmd: {redact(' '.join(cmd))}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"=== safe_apply: GUARD ЗАБЛОКИРОВАЛ операцию (exit {r.returncode}) ===")
    else:
        print("=== safe_apply: guard пройден, продолжаем ===")
    return r.returncode


def load_db_config(db_id: str) -> dict:
    cfg_path = ROOT / ".v8-project.json"
    if not cfg_path.exists():
        print("ERROR: .v8-project.json не найден", file=sys.stderr)
        return {}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception as e:
        print(f"ERROR: .v8-project.json не читается: {redact(str(e))}", file=sys.stderr)
        return {}
    dbs = cfg.get("databases") or []
    matches = [d for d in dbs if isinstance(d, dict) and d.get("id") == db_id]
    if not matches:
        print(f"ERROR: база '{db_id}' не найдена в реестре", file=sys.stderr)
        return {}
    db = matches[0]
    db["__v8path"] = str(cfg.get("v8path", "")).strip()
    return db


def build_ps_args_load_xml(db: dict, args) -> list[str]:
    """Аргументы для db-load-xml (P0-5: per-op, P0-6: без -UpdateDB)."""
    ps_args: list[str] = []
    v8path = db.get("__v8path", "")
    if v8path:
        ps_args.extend(["-V8Path", v8path])
    db_type = str(db.get("type", "")).strip().lower()
    if db_type == "server":
        if db.get("server"): ps_args.extend(["-InfoBaseServer", str(db["server"])])
        if db.get("ref"): ps_args.extend(["-InfoBaseRef", str(db["ref"])])
    else:
        if db.get("path"): ps_args.extend(["-InfoBasePath", str(db["path"])])
    username_env = db.get("username_env", "")
    password_env = db.get("password_env", "")
    if username_env: ps_args.extend(["-UserNameEnv", username_env])
    if password_env: ps_args.extend(["-PasswordEnv", password_env])
    if args.config_dir: ps_args.extend(["-ConfigDir", str(args.config_dir)])
    ps_args.extend(["-Mode", args.mode])
    if args.files: ps_args.extend(["-Files", args.files])
    if args.extension: ps_args.extend(["-Extension", args.extension])
    return ps_args


def build_ps_args_update(db: dict, args) -> list[str]:
    """Аргументы для db-update (P0-5: per-op)."""
    ps_args: list[str] = []
    v8path = db.get("__v8path", "")
    if v8path:
        ps_args.extend(["-V8Path", v8path])
    db_type = str(db.get("type", "")).strip().lower()
    if db_type == "server":
        if db.get("server"): ps_args.extend(["-InfoBaseServer", str(db["server"])])
        if db.get("ref"): ps_args.extend(["-InfoBaseRef", str(db["ref"])])
    else:
        if db.get("path"): ps_args.extend(["-InfoBasePath", str(db["path"])])
    username_env = db.get("username_env", "")
    password_env = db.get("password_env", "")
    if username_env: ps_args.extend(["-UserNameEnv", username_env])
    if password_env: ps_args.extend(["-PasswordEnv", password_env])
    if args.extension: ps_args.extend(["-Extension", args.extension])
    if args.dynamic: ps_args.extend(["-Dynamic", args.dynamic])
    return ps_args


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="safe_apply.py",
        description="Единая безопасная точка входа для опасных операций apply.",
    )
    parser.add_argument("--task", required=True, help="TASK-ID (строгий формат TASK-<alnum>)")
    parser.add_argument("--db", required=True, help="id базы из .v8-project.json")
    parser.add_argument("--op", required=True, choices=sorted(ALLOWED_OPS),
                        help="класс операции (load-xml или update)")
    parser.add_argument("--config-dir", default="", help="ConfigDir (каталог XML-исходников)")
    parser.add_argument("--mode", default="Partial", choices=["Full", "Partial"], help="режим загрузки")
    parser.add_argument("--files", default="", help="относительные пути через запятую (для Partial)")
    parser.add_argument("--extension", default="", help="имя расширения")
    parser.add_argument("--dynamic", default="", choices=["+", "-"], help="динамическое обновление (для update)")
    parser.add_argument("--project-root", default="", help="явный корень проекта (для тестов)")
    parser.add_argument("--dry-run", action="store_true", help="только guard, без вызова skill")
    args = parser.parse_args()

    op = args.op.strip()

    # P0-7: проверка, что операция не заблокирована
    if op in BLOCKED_OPS:
        print(f"ERROR: операция '{op}' заблокирована — особо опасная операция недоступна через wrapper", file=sys.stderr)
        return 1

    # 1. Preflight guard
    guard_exit = run_guard(args.task.strip(), args.db.strip(), op, args.mode, args.files.strip(),
                          args.project_root.strip())
    if guard_exit != 0:
        return 1

    if args.dry_run:
        print("=== safe_apply: --dry-run — guard пройден, skill не запускается ===")
        return 0

    # 2. Конфигурация базы
    db = load_db_config(args.db.strip())
    if not db:
        return 2

    # 3. Найти skill-скрипт
    skill_name = OP_TO_SKILL[op]
    skills_dir = find_skills_dir(ROOT)
    skill_script = skills_dir / skill_name / "scripts" / f"{skill_name}.ps1"
    if not skill_script.exists():
        print(f"ERROR: skill-скрипт не найден: {skill_script}", file=sys.stderr)
        return 2

    # 4. Построить аргументы (per-op)
    if op == "load-xml":
        ps_args = build_ps_args_load_xml(db, args)
    elif op == "update":
        ps_args = build_ps_args_update(db, args)
    else:
        print(f"ERROR: операция '{op}' не поддерживается wrapper", file=sys.stderr)
        return 2

    # 5. Запустить skill-скрипт
    pwsh = os.environ.get("PWSH", "powershell")
    cmd = [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(skill_script)] + ps_args

    print(f"\n=== safe_apply: запуск skill '{skill_name}' ===")
    print(f"  script: {skill_script}")
    print(f"  args: {redact(' '.join(ps_args))}")

    try:
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print(f"\n=== safe_apply: skill '{skill_name}' завершился с ошибкой (exit {r.returncode}) ===")
            print("Восстановление — ручная процедура (db-load-dt из бэкапа).")
            return 1
        print(f"\n=== safe_apply: skill '{skill_name}' выполнен успешно ===")
        return 0
    except FileNotFoundError:
        print(f"ERROR: PowerShell не найден ({pwsh})", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: непредвиденная ошибка: {redact(str(e))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
