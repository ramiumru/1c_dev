#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
safe_apply.py — единая безопасная точка входа для опасных операций apply.

Это wrapper: запускает applier_guard.py (preflight) и ТОЛЬКО при его успешном
прохождении вызывает skill-скрипт (db-load-xml / db-load-cf / db-update / ...).

Назначение (8.5): агент 1c-applier не должен вызывать db-load-*/db-update напрямую
через skill/bash — только через safe_apply.py. Права в frontmatter (1c-applier.yml)
разрешают safe_apply.py и запрещают прямые db-load-*/db-update, поэтому обойти
guard невозможно на уровне движка прав.

Запуск:
  python scripts/safe_apply.py --task <TASK-ID> --db <id> --op <load-xml|load-cf|load-dt|update|create> \\
      --config-dir <ConfigSrc> --mode <Full|Partial> [--files <rel1,rel2,...>] [--extension <name>]

Логика:
  1. Запустить applier_guard.py --task <TASK-ID> --db <id> --op <op>
     → ненулевой exit = СТОП, операция не выполняется.
  2. Прочитать .v8-project.json, найти запись базы по --db.
  3. Разрешить параметры подключения (путь/сервер/ref, v8path, username/password из env).
  4. Найти skill-скрипт по --op (skills/<skill-name>/scripts/<skill-name>.ps1).
  5. Запустить skill-скрипт через PowerShell с нужными параметрами.

Exit codes:
  0 — операция выполнена успешно
  1 — guard заблокировал операцию (или операция завершилась ошибкой)
  2 — ошибка аргументов / конфигурации / корень не найден
"""

from __future__ import annotations

import argparse
import json
import os
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
    cur = start.resolve()
    return cur.parent.parent


ROOT = _find_root(Path(__file__).resolve())

# Маппинг op → skill-имя
OP_TO_SKILL = {
    "load-xml": "db-load-xml",
    "load-cf": "db-load-cf",
    "load-dt": "db-load-dt",
    "update": "db-update",
    "create": "db-create",
}


def find_skills_dir(root: Path) -> Path:
    """Найти каталог skills в установленной раскладке (адаптер-зависимый)."""
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


def run_guard(task: str, db: str, op: str) -> int:
    """Запустить applier_guard.py как subprocess. Возвращает exit code."""
    guard = ROOT / "scripts" / "applier_guard.py"
    if not guard.exists():
        guard = ROOT / "core" / "scripts" / "applier_guard.py"
    cmd = [
        sys.executable, str(guard),
        "--task", task,
        "--db", db,
        "--op", op,
    ]
    print(f"=== safe_apply: preflight guard ===")
    print(f"  cmd: {' '.join(cmd)}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"=== safe_apply: GUARD ЗАБЛОКИРОВАЛ операцию (exit {r.returncode}) ===")
    else:
        print(f"=== safe_apply: guard пройден, продолжаем ===")
    return r.returncode


def load_db_config(db_id: str) -> dict:
    """Прочитать .v8-project.json и найти запись базы по id."""
    cfg_path = ROOT / ".v8-project.json"
    if not cfg_path.exists():
        print(f"ERROR: .v8-project.json не найден: {cfg_path}", file=sys.stderr)
        return {}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception as e:
        print(f"ERROR: .v8-project.json не читается: {e}", file=sys.stderr)
        return {}
    dbs = cfg.get("databases") or []
    matches = [d for d in dbs if isinstance(d, dict) and d.get("id") == db_id]
    if not matches:
        print(f"ERROR: база '{db_id}' не найдена в реестре", file=sys.stderr)
        return {}
    db = matches[0]
    db["__v8path"] = str(cfg.get("v8path", "")).strip()
    return db


def resolve_credentials(db: dict) -> tuple[str, str]:
    """Разрешить логин/пароль из env-переменных (по username_env/password_env)."""
    username_env = db.get("username_env", "")
    password_env = db.get("password_env", "")
    username = os.environ.get(username_env, "") if username_env else ""
    password = os.environ.get(password_env, "") if password_env else ""
    return username, password


def build_ps_args(db: dict, args) -> list[str]:
    """Построить список аргументов для skill-скрипта PowerShell."""
    ps_args: list[str] = []
    v8path = db.get("__v8path", "")
    if v8path:
        ps_args.extend(["-V8Path", v8path])

    db_type = str(db.get("type", "")).strip().lower()
    if db_type == "server":
        server = db.get("server", "")
        ref = db.get("ref", "")
        if server:
            ps_args.extend(["-InfoBaseServer", str(server)])
        if ref:
            ps_args.extend(["-InfoBaseRef", str(ref)])
    else:
        path = db.get("path", "")
        if path:
            ps_args.extend(["-InfoBasePath", str(path)])

    username, password = resolve_credentials(db)
    if username:
        ps_args.extend(["-UserName", username])
    if password:
        ps_args.extend(["-Password", password])

    if args.config_dir:
        ps_args.extend(["-ConfigDir", str(args.config_dir)])
    if args.mode:
        ps_args.extend(["-Mode", args.mode])
    if args.files:
        ps_args.extend(["-Files", args.files])
    if args.extension:
        ps_args.extend(["-Extension", args.extension])
    if args.update_db:
        ps_args.append("-UpdateDB")

    return ps_args


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="safe_apply.py",
        description="Единая безопасная точка входа для опасных операций apply (guard + skill).",
    )
    parser.add_argument("--task", required=True, help="TASK-ID (обязателен; без него guard блокирует)")
    parser.add_argument("--db", required=True, help="id базы из .v8-project.json")
    parser.add_argument("--op", required=True, choices=sorted(OP_TO_SKILL.keys()),
                        help="класс операции")
    parser.add_argument("--config-dir", default="", help="ConfigDir (каталог XML-исходников)")
    parser.add_argument("--mode", default="Partial", choices=["Full", "Partial"], help="режим загрузки")
    parser.add_argument("--files", default="", help="относительные пути через запятую (для Partial)")
    parser.add_argument("--extension", default="", help="имя расширения")
    parser.add_argument("--update-db", action="store_true", help="совместить load + db-update (-UpdateDB)")
    parser.add_argument("--dry-run", action="store_true", help="только guard, без вызова skill")
    args = parser.parse_args()

    # 1. Preflight guard — обязателен
    guard_exit = run_guard(args.task.strip(), args.db.strip(), args.op.strip())
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
    skill_name = OP_TO_SKILL[args.op]
    skills_dir = find_skills_dir(ROOT)
    skill_script = skills_dir / skill_name / "scripts" / f"{skill_name}.ps1"
    if not skill_script.exists():
        print(f"ERROR: skill-скрипт не найден: {skill_script}", file=sys.stderr)
        return 2

    # 4. Построить аргументы для PowerShell
    ps_args = build_ps_args(db, args)

    # 5. Запустить skill-скрипт
    pwsh = os.environ.get("PWSH", "powershell")
    cmd = [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(skill_script)] + ps_args

    print(f"\n=== safe_apply: запуск skill '{skill_name}' ===")
    print(f"  script: {skill_script}")
    print(f"  args: {' '.join(ps_args[:6])}{'...' if len(ps_args) > 6 else ''}")
    # Не выводим пароль
    safe_args = [a if a != "-Password" else "-Password" for a in ps_args]

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
        print(f"ERROR: непредвиденная ошибка: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
