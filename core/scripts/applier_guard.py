#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
applier_guard.py — проверяемый preflight-guard для агента 1c-applier.

Не подключается к 1С. Только локальные read-only проверки.

P0-3: Строгая целостность SDD — все поля обязательны, null/пусто/invalid = FAIL.
P0-4: Backup gate — все поля обязательны, artifact_sha256 проверяется.
P0-7: Особо опасные операции (load-dt, create, load-cf, web-*, Full) — заблокированы.
P0-8: approval.md и backup.md — в pilot-control/, не в specs/ (read-only для агентов).
P0-10: --project-root для явного корня проекта (тесты не зависят от harness ROOT).

Операции:
  Разрешённые для пилота: load-xml (Partial), update.
  Заблокированные (всегда FAIL): load-dt, create, load-cf, web-publish, web-unpublish, Full.

Exit codes: 0 = OK, 1 = FAIL, 2 = ошибка аргументов.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _root import find_root as _find_root_shared
except ImportError:
    _find_root_shared = None
try:
    from scope_hash import compute_scope_hash_from_file, validate_hash_format
except ImportError:
    compute_scope_hash_from_file = None
    validate_hash_format = None


def _find_root(start: Path) -> Path:
    if _find_root_shared is not None:
        root = _find_root_shared(start)
        if root is not None:
            return root
    return start.resolve().parent.parent


ROOT = _find_root(Path(__file__).resolve())

ALLOWED_ENVS = {"local", "test", "staging"}
ALLOWED_OPS = {"load-xml", "update"}
BLOCKED_OPS = {"load-dt", "create", "load-cf", "web-publish", "web-unpublish"}
ALL_DANGEROUS_OPS = ALLOWED_OPS | BLOCKED_OPS
VALID_RISKS = {"low", "medium", "high"}
BACKUP_MAX_AGE_HOURS = 24
_TASK_ID_RE = re.compile(r"^TASK-[A-Za-z0-9]+(-[A-Za-z0-9]+)*$")
_SELF_APPROVAL_DENY = {"1c-developer"}
_HIGH_RISK_SELF_DENY = {"1c-developer", "1c-analyst"}
HIGH_RISK_MARKERS = (
    "проведени", "движени", "транзакци", "блокиров", "RLS", "права",
    "регламентн", "фонов", "экспортн", "метаданны", "интеграционн",
    "структур", "массовое", "обмен", "финансов",
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _parse_yaml_block(text: str) -> dict:
    blocks = re.findall(r"```yaml\s*\r?\n(.*?)```", text, re.S)
    if not blocks:
        blocks = re.findall(r"```\s*\r?\n(.*?)```", text, re.S)
    result: dict = {}
    if not blocks:
        return result
    for line in blocks[0].splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if v.lower() == "null":
            v = ""
        result[k] = v
    return result


def _validate_iso_timestamp(ts: str) -> bool:
    """Проверка ISO 8601 timestamp с timezone."""
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return False
        return True
    except Exception:
        return False


def validate_task_id(task: str) -> bool:
    if not task or "\\" in task or "/" in task or ".." in task or len(task) > 100:
        return False
    return bool(_TASK_ID_RE.match(task))


def validate_path_safe(path_str: str, base: Path) -> bool:
    if not path_str:
        return False
    path_str = path_str.replace("\\", "/").strip()
    if path_str.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", path_str):
        return False
    if ".." in path_str.split("/"):
        return False
    if re.search(r"[\x00-\x1f<>|`$;]", path_str):
        return False
    try:
        resolved = (base / path_str).resolve()
        base_resolved = base.resolve()
        # Используем is_relative_to если доступен (Python 3.9+), иначе string check
        try:
            return resolved.is_relative_to(base_resolved)
        except AttributeError:
            return str(resolved).startswith(str(base_resolved))
    except Exception:
        return False


class Guard:
    def __init__(self, config: Path, specs_dir: Path, control_dir: Path,
                 cli_files: str = "", cli_mode: str = "Partial", project_root: Path = None):
        self.config = config
        self.specs_dir = specs_dir
        self.control_dir = control_dir
        self.cli_files = cli_files
        self.cli_mode = cli_mode
        self.project_root = project_root or ROOT
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self._db_id = ""
        self._db_env = ""

    def fail(self, msg: str) -> None:
        self.failures.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def load_config(self) -> dict:
        if not self.config.exists():
            self.fail(f"реестр баз не найден: {self.config}")
            return {}
        try:
            raw = self.config.read_text(encoding="utf-8-sig", errors="replace")
            return json.loads(raw)
        except Exception as e:
            self.fail(f"реестр баз не читается: {e}")
            return {}

    def check_environment(self, cfg: dict, db_id: str) -> None:
        if not db_id:
            self.fail("выбор базы: --db не задан")
            return
        dbs = cfg.get("databases") or []
        if not isinstance(dbs, list) or not dbs:
            self.fail("выбор базы: реестр баз пуст")
            return
        matches = [d for d in dbs if isinstance(d, dict) and d.get("id") == db_id]
        if not matches:
            self.fail(f"выбор базы: база '{db_id}' не зарегистрирована")
            return
        if len(matches) > 1:
            self.fail(f"выбор базы: несколько записей с id '{db_id}'")
            return
        db = matches[0]
        # `password` (plaintext) запрещён; `username` разрешён (не секрет)
        if "password" in db:
            self.fail("безопасность: plaintext password в записи базы — мигрируйте на password_mode: none или password_mode: env + password_env")

        # P1: password_mode model — явная модель аутентификации
        password_mode = str(db.get("password_mode", "")).strip().lower()
        has_password_env = bool(str(db.get("password_env", "")).strip())

        if password_mode not in ("none", "env"):
            self.fail(f"конфигурация: password_mode='{password_mode}' — допускается только 'none' или 'env'")
        elif password_mode == "none":
            if has_password_env:
                self.fail("конфигурация: password_mode='none' но password_env задан — противоречие")
        elif password_mode == "env":
            if not has_password_env:
                self.fail("конфигурация: password_mode='env' но password_env не задан")
            else:
                pw_env_name = str(db.get("password_env", "")).strip()
                pw_val = os.environ.get(pw_env_name, "")
                if not pw_val:
                    self.fail(f"конфигурация: password_env='{pw_env_name}' — переменная не установлена или пуста")
        db_env = str(db.get("environment", "")).strip()
        global_env = str(cfg.get("environment", "")).strip()
        db_type = str(db.get("type", "")).strip().lower()
        is_server = db_type == "server"
        if not db_env:
            if is_server:
                self.fail(f"environment: серверная база '{db_id}' без per-db environment")
            elif global_env:
                db_env = global_env
            else:
                self.fail(f"environment: база '{db_id}' — environment отсутствует")
                return
        if db_env not in ALLOWED_ENVS and db_env != "production":
            self.fail(f"environment: неизвестное значение '{db_env}' для базы '{db_id}'")
            return
        if db_env == "production":
            self.fail(f"environment: production (база '{db_id}') — операции запрещены")
            return
        if is_server:
            allow_apply = db.get("allow_apply")
            if not (allow_apply is True or str(allow_apply).strip().lower() == "true"):
                self.fail(f"environment: серверная база '{db_id}' без allow_apply: true")
        self._db_id = db_id
        self._db_env = db_env
        self._db_record = db

    def check_task_id(self, task: str) -> None:
        if not task:
            self.fail("TASK-ID: не задан — обязателен")
            return
        if not validate_task_id(task):
            self.fail(f"TASK-ID: некорректный формат '{task}'")
            return
        try:
            spec_path = (self.specs_dir / task).resolve()
            specs_resolved = self.specs_dir.resolve()
            try:
                if not spec_path.is_relative_to(specs_resolved):
                    self.fail(f"TASK-ID: путь '{task}' выходит за пределы specs/ — path traversal")
            except AttributeError:
                if not str(spec_path).startswith(str(specs_resolved)):
                    self.fail(f"TASK-ID: путь '{task}' выходит за пределы specs/ — path traversal")
        except Exception:
            self.fail(f"TASK-ID: ошибка разрешения пути '{task}'")

    def check_operation_class(self, op: str, mode: str) -> None:
        """P0-7: особо опасные операции заблокированы."""
        if mode == "Full":
            self.fail("особо опасная операция: Full-режим заблокирован")
        if op in BLOCKED_OPS:
            self.fail(
                f"особо опасная операция: '{op}' заблокирована — "
                f"операция технически отключена для первого пилота"
            )

    def check_sdd(self, task: str) -> None:
        if not task:
            return
        spec_path = self.specs_dir / task / "03_solution_spec.md"
        if not spec_path.exists():
            self.fail(f"SDD: спецификация отсутствует: {spec_path}")
            return
        spec_text = _read_text(spec_path)
        meta = _parse_yaml_block(spec_text)

        # P0-3: Все поля обязательны, null/пусто/invalid = FAIL
        status = str(meta.get("status", "")).strip()
        if status != "approved":
            self.fail(f"SDD: status='{status or '(пусто)'}' — требуется 'approved'")

        risk = str(meta.get("risk", "")).strip().lower()
        if risk not in VALID_RISKS:
            self.fail(f"SDD: risk='{risk or '(пусто)'}' — требуется low/medium/high")

        approved_by = str(meta.get("approved_by", "")).strip()
        if not approved_by:
            self.fail("SDD: approved_by пуст — само-подтверждение запрещено")
        else:
            is_high = risk == "high" or any(m.lower() in spec_text.lower() for m in HIGH_RISK_MARKERS)
            deny_set = _HIGH_RISK_SELF_DENY if is_high else _SELF_APPROVAL_DENY
            for denied in deny_set:
                if approved_by.lower() == denied.lower():
                    self.fail(f"SDD: approved_by='{approved_by}' — само-подтверждение запрещено")

        approved_at = str(meta.get("approved_at", "")).strip()
        if not approved_at:
            self.fail("SDD: approved_at пуст — timestamp подтверждения отсутствует")
        elif not _validate_iso_timestamp(approved_at):
            self.fail(f"SDD: approved_at='{approved_at}' — некорректный ISO 8601 timestamp (требуется timezone)")

        spec_version = str(meta.get("spec_version", "")).strip()
        if not spec_version:
            self.fail("SDD: spec_version пуст — обязателен")
        elif not spec_version.isdigit() or int(spec_version) < 1:
            self.fail(f"SDD: spec_version='{spec_version}' — должно быть положительное целое")

        # P0-3: scope_hash ВСЕГДА заново вычисляется
        if compute_scope_hash_from_file is not None:
            computed_hash = compute_scope_hash_from_file(spec_path)
        else:
            computed_hash = ""
        if not computed_hash:
            self.fail("SDD: не удалось вычислить scope_hash из spec")
        else:
            if validate_hash_format and not validate_hash_format(computed_hash):
                self.fail(f"SDD: вычисленный scope_hash имеет неверный формат (ожидается 64 lowercase hex)")

        # Сверка с yaml-блоком spec
        spec_stored_hash = str(meta.get("scope_hash", "")).strip()
        if not spec_stored_hash:
            self.fail("SDD: scope_hash в yaml-блоке spec отсутствует/пуст — обязателен")
        elif computed_hash and spec_stored_hash != computed_hash:
            self.fail("SDD: scope_hash в yaml-блоке spec не совпадает с заново вычисленным — scope изменён после approval")

        # --- review.md (в pilot-control/, не в specs/) ---
        review_path = self.control_dir / task / "review.md"
        if not review_path.exists():
            self.fail("SDD: review.md отсутствует в pilot-control/ — review обязателен")
            return
        review_text = _read_text(review_path)
        review_meta = _parse_yaml_block(review_text)

        verdict = str(review_meta.get("verdict", "")).strip().lower()
        if verdict != "approved":
            self.fail(f"SDD: review verdict='{verdict}' — требуется 'approved'")

        reviewed_by = str(review_meta.get("reviewed_by", "")).strip()
        if not reviewed_by:
            self.fail("SDD: reviewed_by в review.md пуст")
        reviewed_at = str(review_meta.get("reviewed_at", "")).strip()
        if not reviewed_at:
            self.fail("SDD: reviewed_at в review.md пуст")
        elif not _validate_iso_timestamp(reviewed_at):
            self.fail(f"SDD: reviewed_at='{reviewed_at}' — некорректный ISO 8601 timestamp")

        if approved_by and reviewed_by and approved_by.lower() == reviewed_by.lower():
            self.fail(f"SDD: нарушение независимости — reviewed_by='{reviewed_by}' совпадает с approved_by")

        review_spec_version = str(review_meta.get("spec_version", "")).strip()
        if not review_spec_version:
            self.fail("SDD: spec_version в review.md отсутствует")
        elif spec_version and review_spec_version and spec_version != review_spec_version:
            self.fail(f"SDD: spec_version расходится (spec={spec_version}, review={review_spec_version})")

        review_hash = str(review_meta.get("scope_hash", "")).strip()
        if not review_hash:
            self.fail("SDD: scope_hash в review.md отсутствует/пуст")
        elif computed_hash and review_hash != computed_hash:
            self.fail("SDD: scope_hash в review.md не совпадает с заново вычисленным")

        # --- 06_change_report.md ---
        report_path = self.specs_dir / task / "06_change_report.md"
        if not report_path.exists():
            self.fail(f"SDD: 06_change_report.md отсутствует: {report_path}")
            return
        report_meta = _parse_yaml_block(_read_text(report_path))
        report_version = str(report_meta.get("spec_version", "")).strip()
        if not report_version:
            self.fail("SDD: spec_version в 06_change_report.md отсутствует")
        elif spec_version and report_version and spec_version != report_version:
            self.fail(f"SDD: spec_version расходится (spec={spec_version}, report={report_version})")
        report_hash = str(report_meta.get("scope_hash", "")).strip()
        if not report_hash:
            self.fail("SDD: scope_hash в 06_change_report.md отсутствует/пуст")
        elif computed_hash and report_hash != computed_hash:
            self.fail("SDD: scope_hash в 06_change_report.md не совпадает с заново вычисленным")

    def check_plan_files(self, task: str, db_record: dict = None) -> list[str]:
        if not task:
            return []
        report_path = self.specs_dir / task / "06_change_report.md"
        if not report_path.exists():
            return []
        text = _read_text(report_path)
        if "Изменённые файлы" not in text and "## Изменённые" not in text:
            self.fail("план: раздел «Изменённые файлы» отсутствует — apply заблокирован")
            return []
        raw_paths = re.findall(r"(projects/[^\s`]+?/src/[^\s`]+)", text)
        if not raw_paths:
            self.fail("план: пустой список файлов — apply заблокирован")
            return []
        # Определить configSrc для проверки scope
        config_src = str(db_record.get("configSrc", "")).strip().replace("\\", "/").rstrip("/") if db_record else ""
        seen = set()
        plan_files = []
        for p in raw_paths:
            p = p.rstrip(",.;:—-")
            if p in seen:
                self.fail(f"план: дубликат пути '{p}'")
                continue
            seen.add(p)
            p_norm = p.replace("\\", "/")
            if p_norm.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", p_norm):
                self.fail(f"план: абсолютный путь заблокирован: '{p}'")
                continue
            if ".." in p_norm.split("/"):
                self.fail(f"план: path traversal заблокирован: '{p}'")
                continue
            if not (self.project_root / p).exists():
                self.fail(f"план: файл отсутствует: '{p}'")
            # Блокировка файлов вне configSrc
            if config_src and not p_norm.startswith(config_src + "/"):
                self.fail(f"план: файл вне configSrc '{config_src}' заблокирован: '{p}'")
            plan_files.append(p)
        return plan_files

    def check_cli_files_match(self, plan_files: list[str], op: str) -> None:
        """CLI --files должен совпадать с планом. Только для load-xml.
        Если --files не задан (пустой) — план из report используется как canonical."""
        if op != "load-xml":
            return
        if not plan_files:
            return  # уже залогировано в check_plan_files
        if not self.cli_files:
            return  # --files не задан — guard использует plan_files из report как canonical
        cli_set = {f.strip().replace("\\", "/") for f in self.cli_files.split(",") if f.strip()}
        plan_set = {f.replace("\\", "/") for f in plan_files}
        extra = cli_set - plan_set
        missing = plan_set - cli_set
        if extra:
            self.fail(f"план: лишние файлы в --files (отсутствуют в плане): {sorted(extra)}")
        if missing:
            self.fail(f"план: отсутствуют файлы из плана в --files: {sorted(missing)}")

    def check_backup(self, task: str, db_id: str, db_record: dict) -> None:
        """Backup gate. backup_mode: external skips it entirely."""
        if not task or not db_id:
            return
        backup_mode = str(db_record.get("backup_mode", "")).strip().lower()
        if backup_mode == "external":
            # Владелец сделал внешний backup — не требовать backup.md
            return
        # Стандартный путь: backup.md в pilot-control/
        backup_path = self.control_dir / task / "backup.md"
        if not backup_path.exists():
            self.fail(f"backup: backup.md отсутствует в pilot-control/ — apply заблокирован")
            return
        backup_meta = _parse_yaml_block(_read_text(backup_path))

        # P0-4: Все поля обязательны, FAIL not WARN
        bv = str(backup_meta.get("backup_version", "")).strip()
        if not bv:
            self.fail("backup: backup_version отсутствует")
        elif bv != "1":
            self.fail(f"backup: backup_version='{bv}' — поддерживается только '1'")

        bdb = str(backup_meta.get("database_id", "")).strip()
        if not bdb:
            self.fail("backup: database_id отсутствует")
        elif bdb != db_id:
            self.fail(f"backup: database_id='{bdb}' не совпадает с '{db_id}'")

        benv = str(backup_meta.get("environment", "")).strip()
        if not benv:
            self.fail("backup: environment отсутствует")
        elif benv == "production":
            self.fail("backup: production — операции запрещены")
        elif self._db_env and benv != self._db_env:
            self.fail(f"backup: environment='{benv}' не совпадает с '{self._db_env}'")

        bcreated = str(backup_meta.get("created_at", "")).strip()
        if not bcreated:
            self.fail("backup: created_at отсутствует")
        elif not _validate_iso_timestamp(bcreated):
            self.fail(f"backup: created_at='{bcreated}' — некорректный ISO 8601 timestamp")
        else:
            try:
                created = datetime.fromisoformat(bcreated.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                age_hours = (now - created).total_seconds() / 3600
                if age_hours > BACKUP_MAX_AGE_HOURS:
                    self.fail(f"backup: просрочен (возраст {age_hours:.1f}ч > {BACKUP_MAX_AGE_HOURS}ч)")
                if age_hours < -1:
                    self.fail("backup: created_at в будущем")
            except Exception:
                self.fail(f"backup: created_at='{bcreated}' — ошибка парсинга timestamp")

        artifact = str(backup_meta.get("artifact", "")).strip()
        if not artifact:
            self.fail("backup: artifact отсутствует")
        elif not validate_path_safe(artifact, self.project_root):
            self.fail(f"backup: artifact путь небезопасен (traversal/symlink/absolute): '{artifact}'")
        else:
            artifact_path = (self.project_root / artifact).resolve()
            project_root_resolved = self.project_root.resolve()
            try:
                if not artifact_path.is_relative_to(project_root_resolved):
                    self.fail(f"backup: artifact выходит за корень проекта: '{artifact}'")
            except AttributeError:
                if not str(artifact_path).startswith(str(project_root_resolved)):
                    self.fail(f"backup: artifact выходит за корень проекта: '{artifact}'")
            if artifact_path.exists():
                if not artifact_path.is_file():
                    self.fail(f"backup: artifact не является обычным файлом: '{artifact}'")
                elif artifact_path.stat().st_size == 0:
                    self.fail(f"backup: artifact пуст (нулевой размер): '{artifact}'")
                # P0-4: проверка artifact_sha256
                artifact_sha = str(backup_meta.get("artifact_sha256", "")).strip()
                if not artifact_sha:
                    self.fail("backup: artifact_sha256 отсутствует")
                elif validate_hash_format and not validate_hash_format(artifact_sha):
                    self.fail(f"backup: artifact_sha256 неверный формат (ожидается 64 lowercase hex)")
                else:
                    actual_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                    if actual_sha != artifact_sha:
                        self.fail("backup: artifact_sha256 не совпадает с фактическим хешем артефакта")
            else:
                self.fail(f"backup: файл артефакта не существует: '{artifact}'")

        bstatus = str(backup_meta.get("status", "")).strip().lower()
        if bstatus != "success":
            self.fail(f"backup: status='{bstatus}' — требуется 'success'")

    def check_tools(self, op: str) -> None:
        if op not in ALL_DANGEROUS_OPS:
            return
        # Best-effort: проверяем только PATH (без shutil.which, который может зависать)
        path_env = os.environ.get("PATH", "")
        found = any(
            os.path.exists(os.path.join(d, exe))
            for d in path_env.split(os.pathsep)
            for exe in ("1cv8", "1cv8.exe", "ibcmd", "ibcmd.exe", "1cv8c", "1cv8c.exe")
            if d
        )
        if not found:
            self.warn("инструменты: 1cv8/ibcmd не найдены в PATH — best-effort")

    def run(self, task: str, db: str, op: str, mode: str) -> int:
        if op not in ALL_DANGEROUS_OPS and op:
            self.warn(f"op '{op}' не в списке опасных")
        cfg = self.load_config()
        self.check_environment(cfg, db)
        self.check_task_id(task)
        self.check_operation_class(op, mode)
        self.check_sdd(task)
        plan_files = self.check_plan_files(task, getattr(self, '_db_record', {}))
        self.check_cli_files_match(plan_files, op)
        self.check_backup(task, self._db_id, getattr(self, '_db_record', {}))
        self.check_tools(op)

        for w in self.warnings:
            print(f"WARN {w}")
        if self.failures:
            for fmsg in self.failures:
                print(f"FAIL {fmsg}")
            print("\nБЛОКИРОВКА: изменяющая операция запрещена guard-проверкой.")
            return 1
        print("OK: preflight guard пройден — операция разрешена.")
        return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="applier_guard.py",
        description="Preflight-guard для 1c-applier.",
    )
    parser.add_argument("--task", default="", help="TASK-ID (строгий формат TASK-<alnum>)")
    parser.add_argument("--db", required=True, help="id базы из .v8-project.json")
    parser.add_argument("--op", required=True, choices=sorted(ALL_DANGEROUS_OPS), help="класс операции")
    parser.add_argument("--mode", default="Partial", choices=["Full", "Partial"], help="режим загрузки")
    parser.add_argument("--files", default="", help="CLI --files (относительные пути через запятую)")
    parser.add_argument("--config", default=str(ROOT / ".v8-project.json"), help="путь к .v8-project.json")
    parser.add_argument("--specs-dir", default=str(ROOT / "specs"), help="каталог specs/")
    parser.add_argument("--control-dir", default="", help="каталог pilot-control/ (approval+backup)")
    parser.add_argument("--project-root", default="", help="явный корень проекта (для тестов)")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve() if args.project_root else ROOT
    specs_dir = Path(args.specs_dir).resolve()
    control_dir = Path(args.control_dir).resolve() if args.control_dir else (project_root / "pilot-control")
    config = Path(args.config).resolve()

    guard = Guard(config, specs_dir, control_dir, args.files.strip(), args.mode, project_root)
    try:
        return guard.run(args.task.strip(), args.db.strip(), args.op.strip(), args.mode)
    except Exception as e:
        print(f"ERROR guard: непредвиденная ошибка: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
