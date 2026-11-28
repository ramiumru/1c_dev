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
from platform_path import resolve_v8path
try:
    from plan_parser import parse_plan_from_file, normalize_files_for_powershell
except ImportError:
    parse_plan_from_file = None
    normalize_files_for_powershell = None


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
              root / ".opencode" / "skills", root / ".agents" / "skills", root / "skills"]:
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
    try:
        # one choice for the whole apply: load-xml and update get the same -V8Path
        db["__v8path"], db["__v8path_source"] = resolve_v8path(cfg, db)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return {}
    return db


def read_change_report_files(specs_dir: Path, task: str, config_src: str = "",
                            project_root: Path = None) -> list[str]:
    """Прочитать 06_change_report.md и извлечь файлы через общий парсер plan_parser."""
    report_path = specs_dir / task / "06_change_report.md"
    if parse_plan_from_file is not None:
        plan_files, errors = parse_plan_from_file(report_path, config_src, project_root)
        if errors:
            for e in errors:
                print(f"ERROR: {e}", file=sys.stderr)
            return []
        return plan_files
    # Fallback (не должно использоваться — план-парсер должен быть установлен)
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


def run_guard(task: str, db: str, config_src: str, files_rel: list[str], project_root: str,
              baseline: str = "unknown") -> int:
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
        "--baseline", baseline,
        "--specs-dir", str(root_path / "specs"),
        "--control-dir", str(root_path / "pilot-control"),
        "--config", str(root_path / ".v8-project.json"),
    ]
    if project_root:
        cmd += ["--project-root", project_root]
    print("=== safe_apply: preflight guard ===")
    print(f"  cmd: {redact(' '.join(cmd))}")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.stdout:
        print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    if r.returncode != 0:
        print(f"=== safe_apply: GUARD ЗАБЛОКИРОВАЛ операцию (exit {r.returncode}) ===")
    else:
        print("=== safe_apply: guard пройден, продолжаем ===")
    return r.returncode


# --- Stage timeout and durable stage journal (pilot incident 2026-11-28) ---------------------
# An agent shell kills commands after <=600 s; a stage that outlives it leaves no answer.
# The journal pilot-control/<TASK>/apply-run.json records each stage before and after it runs,
# so a later look shows which stage started and whether its completion was confirmed.
DEFAULT_STAGE_TIMEOUT_SECONDS = 540
STAGE_TIMEOUT_SECONDS = DEFAULT_STAGE_TIMEOUT_SECONDS
STAGE_TIMEOUT_EXIT = 124
SKILL_CWD = None  # project root: configSrc and plan paths are relative to it
UNRESOLVED_EXIT = 3


def stage_timeout_seconds(db: dict) -> int:
    value = db.get("apply_stage_timeout_seconds", DEFAULT_STAGE_TIMEOUT_SECONDS)
    if type(value) is not int or not 60 <= value <= 7200:
        raise ValueError("apply_stage_timeout_seconds must be an integer 60..7200")
    return value


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class ApplyJournal:
    def __init__(self, project_root: Path, task: str, db_id: str):
        self.path = project_root / "pilot-control" / task / "apply-run.json"
        self.db_id = db_id
        self.data = {"runs": []}
        if self.path.is_file():
            self.data = json.loads(self.path.read_text(encoding="utf-8-sig"))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def unresolved(self):
        """Stages of earlier runs whose completion was never confirmed."""
        found = []
        for run in self.data.get("runs", []):
            if run.get("resolution"):
                continue
            for stage in run.get("stages", []):
                if stage.get("outcome") in ("started", "unknown"):
                    found.append((run, stage))
        return found

    def resolve(self, reason: str) -> None:
        for run, _stage in self.unresolved():
            run["resolution"] = {"at": _utc_now(), "reason": reason}
        self._save()

    def begin_run(self) -> None:
        import uuid
        self.run = {"run_id": uuid.uuid4().hex[:12], "db": self.db_id, "started_at": _utc_now(), "stages": []}
        self.data.setdefault("runs", []).append(self.run)
        self._save()

    def start(self, name: str, timeout: int) -> None:
        self.stage = {"name": name, "started_at": _utc_now(), "timeout_seconds": timeout, "outcome": "started"}
        self.run["stages"].append(self.stage)
        self._save()

    def finish(self, code: int) -> None:
        self.stage["finished_at"] = _utc_now()
        self.stage["exit"] = code
        self.stage["outcome"] = ("unknown" if code == STAGE_TIMEOUT_EXIT
                                 else "succeeded" if code == 0 else "failed")
        self._save()


def run_skill(skill_name: str, ps_args: list[str], skills_dir: Path) -> int:
    """Запустить PowerShell skill-скрипт."""
    skill_script = skills_dir / skill_name / "scripts" / f"{skill_name}.ps1"
    if not skill_script.exists():
        print(f"ERROR: skill-скрипт не найден: {skill_script}", file=sys.stderr)
        return 2
    pwsh = os.environ.get("PWSH", "powershell")
    cmd = [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(skill_script)] + ps_args
    print(f"\n=== safe_apply: этап '{skill_name}' ЗАПУЩЕН — до подтверждения исход неизвестен ===", flush=True)
    print(f"  script: {skill_script}")
    print(f"  args: {redact(' '.join(ps_args))}", flush=True)
    try:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=STAGE_TIMEOUT_SECONDS, cwd=SKILL_CWD)
        except subprocess.TimeoutExpired as exc:
            for chunk in (exc.stdout, exc.stderr):
                if chunk:
                    print(redact(chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")), end="")
            print(f"\n=== safe_apply: этап '{skill_name}' запущен, ЗАВЕРШЕНИЕ НЕ ПОДТВЕРЖДЕНО за "
                  f"{STAGE_TIMEOUT_SECONDS} с. Процесс 1cv8 может продолжать работу; состояние базы неизвестно. "
                  "Автоматический повтор не выполняется. ===", flush=True)
            return STAGE_TIMEOUT_EXIT
        if r.stdout:
            print(r.stdout, end="")
        if r.stderr:
            print(r.stderr, end="", file=sys.stderr)
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
    parser.add_argument("--resolve-unknown-outcome", default="",
                        help="после проверки человеком: что установлено о незавершённом этапе прошлого apply")
    parser.add_argument("--baseline", default="unknown", choices=["unknown", "confirmed", "stale"],
                        help="DB baseline state: unknown (default, WARN в guard) | confirmed "
                             "(явное подтверждение пользователя baseline sync repository → DB) | "
                             "stale (guard заблокирует apply)")
    args = parser.parse_args()

    task = args.task.strip()
    db_id = args.db.strip()
    project_root = args.project_root.strip()

    # 1. Найти базу и configSrc
    project_root_path = Path(project_root).resolve() if project_root else ROOT
    db = load_db_config(db_id, project_root_path)
    if not db:
        return 2
    if not isinstance(db.get("repository", {}), dict):
        print("ERROR: repository profile must be an object", file=sys.stderr)
        return 2
    if "enabled" in db.get("repository", {}) and type(db["repository"]["enabled"]) is not bool:
        print("ERROR: repository.enabled must be boolean", file=sys.stderr)
        return 2
    config_src = str(db.get("configSrc", "")).strip()
    if not config_src:
        print(f"ERROR: configSrc не задан для базы '{db_id}'", file=sys.stderr)
        return 2

    # 2. Прочитать файлы из 06_change_report.md через общий парсер
    specs_dir = project_root_path / "specs"
    plan_files = read_change_report_files(specs_dir, task, config_src, project_root_path)
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
    if normalize_files_for_powershell is not None:
        files_rel = normalize_files_for_powershell(config_src, plan_files)
    else:
        files_rel = normalize_files(config_src, plan_files)
    print(f"=== safe_apply: configSrc={config_src}, files={len(files_rel)} ===")

    # 5. Guard (load-xml Partial)
    guard_exit = run_guard(task, db_id, config_src, files_rel, project_root,
                           baseline=str(getattr(args, "baseline", "unknown")))
    if guard_exit != 0:
        return 1

    if db.get("repository", {}).get("enabled"):
        from safe_repository import before_apply
        try:
            before_apply(project_root_path, db_id, task, plan_files, config_src, args.dry_run)
        except (ValueError, OSError) as exc:
            print(f"ERROR repository preflight: {exc}", file=sys.stderr)
            return 1

    if args.dry_run:
        print("=== safe_apply: --dry-run — guard пройден, skill не запускается ===")
        return 0

    # 6. db-load-xml Partial
    global STAGE_TIMEOUT_SECONDS, SKILL_CWD
    SKILL_CWD = str(project_root_path)
    try:
        STAGE_TIMEOUT_SECONDS = stage_timeout_seconds(db)
        journal = ApplyJournal(project_root_path, task, db_id)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    pending = journal.unresolved()
    if pending and not args.resolve_unknown_outcome:
        for run, stage in pending:
            print(f"ERROR: предыдущий apply (run {run.get('run_id')}): этап '{stage.get('name')}' запущен "
                  f"{stage.get('started_at')}, завершение не подтверждено. Повтор не выполняется: проверьте "
                  "процессы 1cv8 и состояние базы, затем запустите с --resolve-unknown-outcome \"<что проверено>\".",
                  file=sys.stderr)
        return UNRESOLVED_EXIT
    if pending:
        if len(args.resolve_unknown_outcome.strip()) < 10:
            print("ERROR: --resolve-unknown-outcome: опишите, что проверено (не менее 10 символов)", file=sys.stderr)
            return 2
        journal.resolve(args.resolve_unknown_outcome.strip())
    journal.begin_run()
    skills_dir = find_skills_dir(project_root_path)
    if not skills_dir.is_dir():
        # Fallback: harness skills (for development)
        skills_dir = find_skills_dir(ROOT)
    ps_args_load = build_ps_args(db, config_src, files_rel, is_update=False)
    journal.start("db-load-xml", STAGE_TIMEOUT_SECONDS)
    load_exit = run_skill("db-load-xml", ps_args_load, skills_dir)
    journal.finish(load_exit)
    if load_exit == STAGE_TIMEOUT_EXIT:
        print("=== safe_apply: load-xml — исход неизвестен; update НЕ запускался ===")
        return UNRESOLVED_EXIT
    if load_exit != 0:
        print("=== safe_apply: load-xml не прошёл — update не запускается ===")
        print("Восстановление — ручная процедура (внешний backup).")
        return 1

    # 7. db-update (UpdateDBCfg)
    ps_args_update = build_ps_args(db, config_src, files_rel, is_update=True)
    journal.start("db-update", STAGE_TIMEOUT_SECONDS)
    update_exit = run_skill("db-update", ps_args_update, skills_dir)
    journal.finish(update_exit)
    if update_exit == STAGE_TIMEOUT_EXIT:
        print("=== safe_apply: update — исход неизвестен; загрузка выполнена, обновление БД не подтверждено ===")
        return UNRESOLVED_EXIT
    if update_exit != 0:
        print("=== safe_apply: update завершился с ошибкой ===")
        print("Восстановление — ручная процедура (внешний backup).")
        return 1

    if db.get("repository", {}).get("enabled"):
        from repository_support import record_apply
        try:
            record_apply(project_root_path, task, db_id, plan_files, config_src)
        except (OSError, ValueError) as exc:
            print(f"ERROR: БД уже обновлена, но запись evidence не удалась: {exc}. "
                  "Не повторять apply автоматически; помещение требует диагностики.", file=sys.stderr)
            return 1

    print(f"\n=== safe_apply: apply выполнен успешно (load-xml + update) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
