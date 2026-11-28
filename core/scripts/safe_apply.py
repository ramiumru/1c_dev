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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _root import find_root as _find_root_shared
except ImportError:
    _find_root_shared = None
from platform_path import resolve_v8path
from os_lock import FileLock, Busy
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
BUSY_EXIT = 5
STAGE_MARKER = None      # file the skill writes: "ack" at its start, "platform-started" before 1cv8
LAST_STAGE_SIGNAL = None # after a real run_skill: "ack" | "platform-started" | "missing" | "incompatible"
STAGE_PROTOCOL_TOKEN = "HARNESS_STAGE_PROTOCOL: 1"
INCOMPATIBLE_EXIT = 6
UNRESOLVED_EXIT = 3
BUDGET_EXIT = 4
MIN_STAGE_SECONDS = 60
BUDGET_MARGIN_SECONDS = 20  # journal writes, output and process exit after the last stage


def stage_timeout_seconds(db: dict) -> int:
    value = db.get("apply_stage_timeout_seconds", DEFAULT_STAGE_TIMEOUT_SECONDS)
    if type(value) is not int or not 60 <= value <= 7200:
        raise ValueError("apply_stage_timeout_seconds must be an integer 60..7200")
    return value


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def unresolved_for_database(project_root: Path, db_id: str):
    """Unresolved apply runs on this database in the whole workspace — every TASK-ID.
    Returns [(journal, run, why)]."""
    found = []
    for path in sorted((project_root / "pilot-control").glob("*/apply-run.json")):
        task = path.parent.name
        try:
            journal = ApplyJournal(project_root, task, db_id)
        except (OSError, ValueError) as exc:
            # an unreadable journal of this workspace is itself an unknown state
            found.append((None, {"run_id": "?", "task": task}, f"журнал {path} не читается: {exc}"))
            continue
        for run, why in journal.unresolved():
            found.append((journal, dict(run, task=task), why))
    return found


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
        """Earlier runs whose infobase state is not established, judged on the whole operation:
        - a stage started but its completion was never confirmed (timeout, runner killed);
        - load succeeded but update was not confirmed (failed, not started, interrupted
          between stages) — the main configuration may differ from the database configuration.
        - load ran the platform and failed without confirmation (exit != 0, no /DumpResult, ...).
        Only a refusal before the platform started (failed_before_launch) leaves the IB untouched."""
        found = []
        for run in self.data.get("runs", []):
            if run.get("resolution") or run.get("db") != self.db_id:
                continue
            stages = {st.get("name"): st for st in run.get("stages", [])}
            for stage in run.get("stages", []):
                if stage.get("outcome") in ("started", "unknown"):
                    found.append((run, f"этап '{stage.get('name')}' запущен {stage.get('started_at')}, "
                                       "завершение не подтверждено"))
            load, update = stages.get("db-load-xml"), stages.get("db-update")
            if load and load.get("outcome") in ("failed_after_launch", "failed_no_signal"):
                found.append((run, f"загрузка запускалась {load.get('started_at')} и завершилась без "
                                   f"подтверждения (exit {load.get('exit')}): ИБ могла измениться"))
            if load and load.get("outcome") == "succeeded" and not (update and update.get("outcome") == "succeeded"):
                if not (update and update.get("outcome") in ("started", "unknown")):
                    state = update.get("outcome") if update else "не запускался"
                    found.append((run, f"загрузка выполнена {load.get('finished_at')}, обновление БД "
                                       f"не подтверждено ({state})"))
        return found

    def resolve(self, reason: str, by_task: str = "") -> None:
        for run, _why in self.unresolved():
            run["resolution"] = {"at": _utc_now(), "reason": reason, "resolved_from_task": by_task}
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

    def skip(self, name: str, reason: str) -> None:
        self.run["stages"].append({"name": name, "outcome": "not_started", "reason": reason, "at": _utc_now()})
        self._save()

    def finish(self, code: int, signal=None) -> str:
        """signal: None = untracked (test doubles); otherwise the stage marker content."""
        self.stage["finished_at"] = _utc_now()
        self.stage["exit"] = code
        self.stage["signal"] = signal
        if code == STAGE_TIMEOUT_EXIT:
            outcome = "unknown"
        elif signal == "missing":
            outcome = "failed_no_signal"       # no protocol signal at all: nothing is confirmed
        elif code == 0:
            outcome = "succeeded"
        elif signal in ("ack", "incompatible"):
            outcome = "failed_before_launch"   # explicit refusal before 1cv8 started: IB untouched
        else:
            outcome = "failed_after_launch"    # 1cv8 ran (or may have run): result not confirmed
        self.stage["outcome"] = outcome
        self._save()
        return outcome


def run_skill(skill_name: str, ps_args: list[str], skills_dir: Path) -> int:
    """Запустить PowerShell skill-скрипт."""
    skill_script = skills_dir / skill_name / "scripts" / f"{skill_name}.ps1"
    if not skill_script.exists():
        print(f"ERROR: skill-скрипт не найден: {skill_script}", file=sys.stderr)
        return 2
    pwsh = os.environ.get("PWSH", "powershell")
    cmd = [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(skill_script)] + ps_args
    global LAST_STAGE_SIGNAL
    LAST_STAGE_SIGNAL = None
    env = None
    if STAGE_MARKER:
        try:
            compatible = STAGE_PROTOCOL_TOKEN in skill_script.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            compatible = False
        if not compatible:
            LAST_STAGE_SIGNAL = "incompatible"
            print(f"\n=== safe_apply: этап '{skill_name}' НЕ запущен: навык {skill_script} не поддерживает "
                  f"протокол этапов ({STAGE_PROTOCOL_TOKEN}) — обновите Harness; платформа не запускалась ===",
                  flush=True)
            return INCOMPATIBLE_EXIT
        marker = Path(STAGE_MARKER)
        marker.parent.mkdir(parents=True, exist_ok=True)
        if marker.exists():
            marker.unlink()
        env = dict(os.environ, HARNESS_STAGE_MARKER=str(marker))
    print(f"\n=== safe_apply: этап '{skill_name}' ЗАПУЩЕН — до подтверждения исход неизвестен ===", flush=True)
    print(f"  script: {skill_script}")
    print(f"  args: {redact(' '.join(ps_args))}", flush=True)
    try:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=STAGE_TIMEOUT_SECONDS, cwd=SKILL_CWD, env=env)
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
        if STAGE_MARKER:
            marker = Path(STAGE_MARKER)
            content = marker.read_text(encoding="utf-8-sig", errors="replace").strip() if marker.exists() else ""
            LAST_STAGE_SIGNAL = content if content in ("ack", "platform-started") else "missing"
        if LAST_STAGE_SIGNAL == "missing":
            print(f"\n=== safe_apply: этап '{skill_name}' не прислал сигнал протокола (exit {r.returncode}) — "
                  "результат НЕ подтверждён, платформа могла запускаться ===")
        elif r.returncode != 0:
            when = ("явный отказ до запуска платформы" if LAST_STAGE_SIGNAL == "ack"
                    else "после запуска платформы — результат не подтверждён")
            print(f"\n=== safe_apply: skill '{skill_name}' завершился с ошибкой (exit {r.returncode}), {when} ===")
        return r.returncode
    except FileNotFoundError:
        print(f"ERROR: PowerShell не найден ({pwsh})", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {redact(str(e))}", file=sys.stderr)
        return 2


def validate_task_id_simple(task: str) -> bool:
    return bool(re.fullmatch(r"TASK-[A-Za-z0-9]+(-[A-Za-z0-9]+)*", task or ""))


def main() -> int:
    held = []  # per call: the apply lock lives exactly as long as this call
    try:
        return _main(held)
    finally:
        for lock in held:
            lock.__exit__(None, None, None)


def _main(held: list) -> int:
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
    parser.add_argument("--shell-budget-seconds", type=int, default=0,
                        help="лимит внешней оболочки на весь вызов (напр. 570 при 600 с): этап, которому не "
                             "хватает остатка, не запускается; лимит этапа урезается до остатка")
    parser.add_argument("--resolve-unknown-outcome", default="",
                        help="после проверки человеком: что установлено о незавершённом этапе прошлого apply")
    parser.add_argument("--baseline", default="unknown", choices=["unknown", "confirmed", "stale"],
                        help="DB baseline state: unknown (default, WARN в guard) | confirmed "
                             "(явное подтверждение пользователя baseline sync repository → DB) | "
                             "stale (guard заблокирует apply)")
    args = parser.parse_args()
    started = time.monotonic()
    if args.shell_budget_seconds and args.shell_budget_seconds < MIN_STAGE_SECONDS + BUDGET_MARGIN_SECONDS:
        print(f"ERROR: --shell-budget-seconds < {MIN_STAGE_SECONDS + BUDGET_MARGIN_SECONDS}", file=sys.stderr)
        return 2

    def stage_budget(name: str) -> int:
        """Timeout for the next stage, or 0 when the shell budget cannot cover a stage start."""
        if not args.shell_budget_seconds:
            return stage_limit
        remaining = int(args.shell_budget_seconds - BUDGET_MARGIN_SECONDS - (time.monotonic() - started))
        if remaining < MIN_STAGE_SECONDS:
            print(f"=== safe_apply: этап '{name}' НЕ запущен: остаток бюджета оболочки {max(remaining, 0)} с "
                  f"меньше {MIN_STAGE_SECONDS} с ===", flush=True)
            return 0
        return min(stage_limit, remaining)

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

    # 1a. Незавершённый apply: проверяется ДО guard и lock-refresh (платформа не запускается)
    global STAGE_TIMEOUT_SECONDS, SKILL_CWD, STAGE_MARKER
    # one apply per database at a time: held until this process exits (kernel lock)
    import hashlib
    lock_path = project_root_path / "logs" / "apply-locks" / (hashlib.sha256(db_id.encode("utf-8")).hexdigest()[:16] + ".lock")
    lock = FileLock(lock_path, 0)
    try:
        lock.__enter__()
    except Busy:
        print(f"ERROR: для базы '{db_id}' уже выполняется другой safe_apply — эта попытка ничего не меняет",
              file=sys.stderr)
        return BUSY_EXIT
    held.append(lock)
    SKILL_CWD = str(project_root_path)
    if validate_task_id_simple(task):
        STAGE_MARKER = str(project_root_path / "pilot-control" / task / "stage-platform-started.marker")
    try:
        STAGE_TIMEOUT_SECONDS = stage_timeout_seconds(db)
        stage_limit = STAGE_TIMEOUT_SECONDS
        journal = ApplyJournal(project_root_path, task, db_id)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    # under the database lock: every task's journal of this workspace counts
    pending = unresolved_for_database(project_root_path, db_id)
    if pending and not args.resolve_unknown_outcome:
        for _journal, run, why in pending:
            print(f"ERROR: незавершённый apply по базе '{db_id}' (задача {run.get('task')}, run {run.get('run_id')}): "
                  f"{why}. Повторная загрузка не выполняется: "
                  "сверьте состояние ИБ (процессы 1cv8, основная конфигурация против конфигурации БД), затем "
                  "запустите с --resolve-unknown-outcome \"<что проверено>\".", file=sys.stderr)
        return UNRESOLVED_EXIT
    if pending:
        if len(args.resolve_unknown_outcome.strip()) < 10:
            print("ERROR: --resolve-unknown-outcome: опишите, что проверено (не менее 10 символов)", file=sys.stderr)
            return 2
        if any(j is None for j, _r, _w in pending):
            print("ERROR: нечитаемый журнал apply нельзя закрыть ключом — восстановите или уберите его вручную",
                  file=sys.stderr)
            return UNRESOLVED_EXIT
        if args.dry_run:
            print("=== safe_apply: --dry-run — решение по незавершённому apply НЕ записано ===")
        else:
            for journal_with_pending in {id(j): j for j, _r, _w in pending}.values():
                journal_with_pending.resolve(args.resolve_unknown_outcome.strip(), by_task=task)
            journal = ApplyJournal(project_root_path, task, db_id)  # re-read: resolutions were written

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
    journal.begin_run()
    skills_dir = find_skills_dir(project_root_path)
    if not skills_dir.is_dir():
        # Fallback: harness skills (for development)
        skills_dir = find_skills_dir(ROOT)
    ps_args_load = build_ps_args(db, config_src, files_rel, is_update=False)
    STAGE_TIMEOUT_SECONDS = stage_budget("db-load-xml")
    if not STAGE_TIMEOUT_SECONDS:
        journal.skip("db-load-xml", "shell budget exhausted before start")
        return BUDGET_EXIT
    journal.start("db-load-xml", STAGE_TIMEOUT_SECONDS)
    load_exit = run_skill("db-load-xml", ps_args_load, skills_dir)
    load_outcome = journal.finish(load_exit, LAST_STAGE_SIGNAL)
    if load_outcome == "failed_no_signal" and load_exit == 0:
        load_exit = 1  # success without a protocol signal is not success
    if load_exit == STAGE_TIMEOUT_EXIT:
        print("=== safe_apply: load-xml — исход неизвестен; update НЕ запускался ===")
        return UNRESOLVED_EXIT
    if load_exit != 0:
        print("=== safe_apply: load-xml не прошёл — update не запускается ===")
        print("Восстановление — ручная процедура (внешний backup).")
        return 1

    # 7. db-update (UpdateDBCfg)
    ps_args_update = build_ps_args(db, config_src, files_rel, is_update=True)
    STAGE_TIMEOUT_SECONDS = stage_budget("db-update")
    if not STAGE_TIMEOUT_SECONDS:
        journal.skip("db-update", "shell budget exhausted before start")
        print("=== safe_apply: загрузка выполнена, обновление БД НЕ запускалось; следующий apply потребует "
              "сверки (--resolve-unknown-outcome) ===")
        return BUDGET_EXIT
    journal.start("db-update", STAGE_TIMEOUT_SECONDS)
    update_exit = run_skill("db-update", ps_args_update, skills_dir)
    update_outcome = journal.finish(update_exit, LAST_STAGE_SIGNAL)
    if update_outcome == "failed_no_signal" and update_exit == 0:
        update_exit = 1
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
