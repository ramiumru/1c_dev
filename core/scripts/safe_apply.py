#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
safe_apply.py — единая безопасная точка входа для apply.

Единственный пользовательский вход:
  python scripts/safe_apply.py --task <TASK-ID> --db <database-id>

Wrapper сам:
  1. Находит базу и её configSrc из .v8-project.json.
  2. Запускает guard (approved SDD, scope_hash, review, environment, backup_mode).
  3. Читает изменённые файлы из 06_change_report.md.
  4. Проверяет существование файлов и соответствие scope.
  5. Вызывает db-load-xml.ps1 в режиме Partial.
  6. При exit code 0 вызывает db-update.ps1 для UpdateDBCfg.
  7. При любой ошибке останавливается и возвращает ненулевой exit code.

Безопасность секретов:
  - Пароль НИКОГДА не передаётся как аргумент PowerShell.
  - username передаётся как -UserName (прямое значение) или -UserNameEnv (env-имя).
  - password_mode: none — параметры пароля не передаются.
  - password_mode: env — передаётся -PasswordEnv (env-имя).
  - Единая redact() маскирует чувствительные значения.

Exit codes: 0 = OK, 1 = guard failed / skill error, 2 = config error.
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

SENSITIVE_PARAMS = {"-Password", "/P", "--password", "-PasswordEnv", "-UserName", "-UserNameEnv", "-V8Path"}


def redact(text: str) -> str:
    if not text:
        return text
    for param in SENSITIVE_PARAMS:
        text = re.sub(rf"({re.escape(param)}\s+)([^\s-]+)", r"\1***REDACTED***", text)
    text = re.sub(r"(Password=)[^;\s]+", r"\1***REDACTED***", text, flags=re.IGNORECASE)
    text = re.sub(r"(Usr=)[^;\s]+", r"\1***REDACTED***", text, flags=re.IGNORECASE)
    return text


def find_skills_dir(root: Path) -> Path:
    for c in [root / ".kilo" / "skills", root / ".claude" / "skills",
              root / ".openworks" / "skills", root / "skills"]:
        if c.is_dir():
            return c
    return root / ".kilo" / "skills"


def load_db_config(db_id: str, project_root: Path = None) -> dict:
    root = project_root or ROOT
    cfg_path = root / ".v8-project.json"
    if not cfg_path.exists():
        print(f"ERROR: .v8-project.json не найден: {cfg_path}", file=sys.stderr)
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


def read_change_report_files(specs_dir: Path, task: str) -> list[str]:
    """Прочитать 06_change_report.md и извлечь файлы из раздела «Изменённые файлы»."""
    report_path = specs_dir / task / "06_change_report.md"
    if not report_path.exists():
        print(f"ERROR: 06_change_report.md не найден: {report_path}", file=sys.stderr)
        return []
    text = report_path.read_text(encoding="utf-8", errors="replace")
    raw_paths = re.findall(r"(projects/[^\s`]+?/src/[^\s`]+)", text)
    return [p.rstrip(",.;:—-") for p in raw_paths]


def normalize_files(config_src: str, plan_files: list[str]) -> list[str]:
    """Убрать префикс configSrc из путей для передачи как -Files в db-load-xml.ps1.

    Путь в change report: projects/collector/src/Documents/X/Ext/ObjectModule.bsl
    configSrc:             projects/collector/src
    Files для PowerShell:  Documents/X/Ext/ObjectModule.bsl
    """
    normalized = []
    prefix = config_src.replace("\\", "/").rstrip("/") + "/"
    for p in plan_files:
        p_norm = p.replace("\\", "/")
        if p_norm.startswith(prefix):
            normalized.append(p_norm[len(prefix):])
        else:
            normalized.append(p_norm)
    return normalized


def build_ps_args(db: dict, config_src: str, files_rel: list[str], is_update: bool = False) -> list[str]:
    """Построить аргументы для db-load-xml.ps1 или db-update.ps1."""
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
    # Credentials
    username = str(db.get("username", "")).strip()
    username_env = str(db.get("username_env", "")).strip()
    password_mode = str(db.get("password_mode", "")).strip().lower()
    password_env = str(db.get("password_env", "")).strip()
    if username_env:
        ps_args.extend(["-UserNameEnv", username_env])
    elif username:
        ps_args.extend(["-UserName", username])
    if password_mode == "env" and password_env:
        ps_args.extend(["-PasswordEnv", password_env])
    # password_mode == "none" — не передаём password

    if is_update:
        if db.get("extension"): ps_args.extend(["-Extension", str(db["extension"])])
    else:
        ps_args.extend(["-ConfigDir", config_src])
        ps_args.extend(["-Mode", "Partial"])
        if files_rel:
            ps_args.extend(["-Files", ",".join(files_rel)])
    return ps_args


def run_guard(task: str, db: str, config_src: str, files_rel: list[str], project_root: str) -> int:
    """Запустить applier_guard.py для load-xml Partial."""
    guard = ROOT / "scripts" / "applier_guard.py"
    if not guard.exists():
        guard = ROOT / "core" / "scripts" / "applier_guard.py"
    root_path = Path(project_root).resolve() if project_root else ROOT
    cmd = [
        sys.executable, str(guard),
        "--task", task,
        "--db", db,
        "--op", "load-xml",
        "--mode", "Partial",
        "--specs-dir", str(root_path / "specs"),
        "--control-dir", str(root_path / "pilot-control"),
        "--config", str(root_path / ".v8-project.json"),
    ]
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


def run_skill(skill_name: str, ps_args: list[str], skills_dir: Path) -> int:
    """Запустить PowerShell skill-скрипт."""
    skill_script = skills_dir / skill_name / "scripts" / f"{skill_name}.ps1"
    if not skill_script.exists():
        print(f"ERROR: skill-скрипт не найден: {skill_script}", file=sys.stderr)
        return 2
    pwsh = os.environ.get("PWSH", "powershell")
    cmd = [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(skill_script)] + ps_args
    print(f"\n=== safe_apply: запуск skill '{skill_name}' ===")
    print(f"  script: {skill_script}")
    print(f"  args: {redact(' '.join(ps_args))}")
    try:
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print(f"\n=== safe_apply: skill '{skill_name}' завершился с ошибкой (exit {r.returncode}) ===")
        return r.returncode
    except FileNotFoundError:
        print(f"ERROR: PowerShell не найден ({pwsh})", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {redact(str(e))}", file=sys.stderr)
        return 2


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="safe_apply.py",
        description="Единая безопасная точка входа для apply (load-xml Partial → update).",
    )
    parser.add_argument("--task", required=True, help="TASK-ID (строгий формат TASK-<alnum>)")
    parser.add_argument("--db", required=True, help="id базы из .v8-project.json")
    parser.add_argument("--project-root", default="", help="явный корень проекта (для тестов)")
    parser.add_argument("--dry-run", action="store_true", help="только проверки, без вызова skill")
    args = parser.parse_args()

    task = args.task.strip()
    db_id = args.db.strip()
    project_root = args.project_root.strip()

    # 1. Найти базу и configSrc
    project_root_path = Path(project_root).resolve() if project_root else ROOT
    db = load_db_config(db_id, project_root_path)
    if not db:
        return 2
    config_src = str(db.get("configSrc", "")).strip()
    if not config_src:
        print(f"ERROR: configSrc не задан для базы '{db_id}'", file=sys.stderr)
        return 2

    # 2. Прочитать файлы из 06_change_report.md
    specs_dir = project_root_path / "specs"
    plan_files = read_change_report_files(specs_dir, task)
    if not plan_files:
        print(f"ERROR: не найдены файлы в 06_change_report.md для задачи '{task}'", file=sys.stderr)
        return 1

    # 3. Проверить существование файлов
    base_root = project_root_path
    for p in plan_files:
        if not (base_root / p).exists():
            print(f"ERROR: файл плана отсутствует: '{p}'", file=sys.stderr)
            return 1

    # 4. Нормализовать пути (strip configSrc prefix)
    files_rel = normalize_files(config_src, plan_files)
    print(f"=== safe_apply: configSrc={config_src}, files={len(files_rel)} ===")

    # 5. Guard (load-xml Partial)
    guard_exit = run_guard(task, db_id, config_src, files_rel, project_root)
    if guard_exit != 0:
        return 1

    if args.dry_run:
        print("=== safe_apply: --dry-run — guard пройден, skill не запускается ===")
        return 0

    # 6. db-load-xml Partial
    skills_dir = find_skills_dir(project_root_path)
    ps_args_load = build_ps_args(db, config_src, files_rel, is_update=False)
    load_exit = run_skill("db-load-xml", ps_args_load, skills_dir)
    if load_exit != 0:
        print("=== safe_apply: load-xml не прошёл — update не запускается ===")
        print("Восстановление — ручная процедура (внешний backup).")
        return 1

    # 7. db-update (UpdateDBCfg)
    ps_args_update = build_ps_args(db, config_src, files_rel, is_update=True)
    update_exit = run_skill("db-update", ps_args_update, skills_dir)
    if update_exit != 0:
        print("=== safe_apply: update завершился с ошибкой ===")
        print("Восстановление — ручная процедура (внешний backup).")
        return 1

    print(f"\n=== safe_apply: apply выполнен успешно (load-xml + update) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
