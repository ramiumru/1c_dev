#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
applier_guard.py — проверяемый preflight-guard для агента 1c-applier.

Не подключается к 1С, не изменяет данные. Только локальные read-only проверки.
Выполняется ДО любой изменяющей операции. Ненулевой exit = операция заблокирована.

Проверки:
  1. environment: per-db (local|test|staging); production/отсутствие/неизвестное → блок.
  2. Выбор базы: --db явно задан, ровно одно совпадение; иначе блок.
  3. TASK-ID: строгий формат (TASK-<цифры|буквы-цифры>), без path traversal.
  4. SDD spec: status==approved; approved_by/approved_at непустые; не само-подтверждение.
  5. Review обязателен для ВСЕХ опасных ops: verdict==approved; независимость.
  6. scope_hash: ВСЕГДА заново вычисляется из spec; совпадает с report и review.
     Идентичные произвольные строки НЕ проходят — hash вычисляется из фактического текста.
  7. План файлов: раздел «Изменённые файлы» обязателен; не пуст; все пути — внутри
     projects/<источник>/src/; абсолютные пути, path traversal, дубликаты → блок.
  8. CLI --files точно совпадает с планом из 06_change_report.md.
  9. Backup-gate: машиночитаемая запись backup.md; свежий, соответствующий базе, успешный.
 10. External approval для особо опасных операций (load-dt, create, Full, load-cf, web-*).
 11. Инструменты: 1cv8/ibcmd в PATH (warn-only).

Операции:
  Разрешаемые с preflight: load-xml (Partial), update (если в плане).
  Особо опасные (заблокированы без внешнего approval):
    load-dt, create, load-cf, web-publish, web-unpublish, Full-режим, авто-восстановление.

Exit codes:
  0 — все проверки пройдены
  1 — хотя бы одна проверка не пройдена
  2 — ошибка аргументов / конфигурации
"""

from __future__ import annotations

import argparse
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

# Разрешаемые с полным preflight (без отдельного external approval)
PREFLIGHT_OPS = {"load-xml", "update"}

# Особо опасные — заблокированы по умолчанию, требуют внешний approval
EXTRA_DANGEROUS_OPS = {"load-dt", "create", "load-cf", "web-publish", "web-unpublish"}
# Full-режим — всегда особо опасный, даже для load-xml

DANGEROUS_OPS = PREFLIGHT_OPS | EXTRA_DANGEROUS_OPS

HIGH_RISK_MARKERS = (
    "проведени", "движени", "транзакци", "блокиров", "RLS", "права",
    "регламентн", "фонов", "экспортн", "метаданны", "интеграционн",
    "структур", "массовое", "обмен", "финансов",
)

SELF_APPROVAL_DENY = {"1c-developer"}
HIGH_RISK_SELF_APPROVAL_DENY = {"1c-developer", "1c-analyst"}

# Строгий формат TASK-ID: TASK-123, TASK-ABC-123, TASK-20260910-143000
_TASK_ID_RE = re.compile(r"^TASK-[A-Za-z0-9]+(-[A-Za-z0-9]+)*$")

# Резервная копия: максимальный возраст в часах
BACKUP_MAX_AGE_HOURS = 24


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _parse_yaml_block(text: str, block_name: str = "yaml") -> dict:
    """Извлекает первый fenced ```yaml блок и парсит плоские key: value строки."""
    blocks = re.findall(r"```" + block_name + r"\s*\r?\n(.*?)```", text, re.S)
    if not blocks:
        blocks = re.findall(r"```\s*\r?\n(.*?)```", text, re.S)
    result: dict = {}
    if not blocks:
        return result
    body = blocks[0]
    for line in body.splitlines():
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


def validate_task_id(task: str) -> bool:
    """Строгая валидация TASK-ID: только TASK-<alnum(-alnum)*>."""
    if not task:
        return False
    if "\\" in task or "/" in task or ".." in task:
        return False
    if len(task) > 100:
        return False
    return bool(_TASK_ID_RE.match(task))


def validate_path_safe(path_str: str, base: Path) -> bool:
    """Проверить, что путь безопасен: относительный, без traversal, внутри base."""
    if not path_str:
        return False
    path_str = path_str.replace("\\", "/").strip()
    # Абсолютный путь
    if path_str.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", path_str):
        return False
    # Path traversal
    if ".." in path_str.split("/"):
        return False
    # Управляющие символы, shell-метасимволы
    if re.search(r"[\x00-\x1f<>|`$;]", path_str):
        return False
    # Проверка resolve не выходит за base
    try:
        resolved = (base / path_str).resolve()
        base_resolved = base.resolve()
        if not str(resolved).startswith(str(base_resolved)):
            return False
    except Exception:
        return False
    return True


class Guard:
    def __init__(self, config: Path, specs_dir: Path, cli_files: str = "", cli_mode: str = "Partial"):
        self.config = config
        self.specs_dir = specs_dir
        self.cli_files = cli_files
        self.cli_mode = cli_mode
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self._db_id = ""
        self._db_env = ""
        self._is_extra_dangerous = False

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
            self.fail(f"реестр баз не читается ({self.config}): {e}")
            return {}

    def check_environment(self, cfg: dict, db_id: str) -> dict:
        if not db_id:
            self.fail("выбор базы: --db не задан — авто-выбор запрещён")
            return {}
        dbs = cfg.get("databases") or []
        if not isinstance(dbs, list) or not dbs:
            self.fail("выбор базы: реестр баз пуст")
            return {}
        matches = [d for d in dbs if isinstance(d, dict) and d.get("id") == db_id]
        if not matches:
            self.fail(f"выбор базы: база '{db_id}' не зарегистрирована")
            return {}
        if len(matches) > 1:
            self.fail(f"выбор базы: несколько записей с id '{db_id}' — неоднозначно")
            return {}
        db = matches[0]

        if "user" in db or "password" in db:
            self.fail("безопасность: plaintext user/password в записи базы — операции заблокированы")

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
                self.fail(f"environment: база '{db_id}' — per-db и глобальный environment отсутствуют")
                return {}
        if db_env not in ALLOWED_ENVS and db_env != "production":
            self.fail(f"environment: неизвестное значение '{db_env}' для базы '{db_id}'")
            return {}
        if db_env == "production":
            self.fail(f"environment: production (база '{db_id}') — операции запрещены")
            return {}
        if is_server:
            allow_apply = db.get("allow_apply")
            if not (allow_apply is True or str(allow_apply).strip().lower() == "true"):
                self.fail(f"environment: серверная база '{db_id}' без allow_apply: true")

        self._db_id = db_id
        self._db_env = db_env
        return db

    def check_task_id(self, task: str) -> None:
        """P0-8: TASK-ID строгий формат, без path traversal."""
        if not task:
            self.fail("TASK-ID: не задан — обязателен для опасных операций")
            return
        if not validate_task_id(task):
            self.fail(f"TASK-ID: некорректный формат '{task}' — ожидается TASK-<цифры|буквы-цифры>")
            return
        # Проверка, что путь внутри specs/
        try:
            spec_path = (self.specs_dir / task).resolve()
            specs_resolved = self.specs_dir.resolve()
            if not str(spec_path).startswith(str(specs_resolved)):
                self.fail(f"TASK-ID: путь '{task}' выходит за пределы specs/ — path traversal заблокирован")
        except Exception:
            self.fail(f"TASK-ID: ошибка разрешения пути '{task}'")

    def check_operation_class(self, op: str, mode: str) -> None:
        """P0-6: разделение разрешённых и особо опасных операций."""
        if mode == "Full":
            self._is_extra_dangerous = True
            self.fail("особо опасная операция: Full-режим заблокирован по умолчанию — требуется внешний approval")
        if op in EXTRA_DANGEROUS_OPS:
            self._is_extra_dangerous = True
            self.fail(
                f"особо опасная операция: '{op}' заблокирована по умолчанию — "
                f"требуется внешний approval (specs/<TASK-ID>/approval.md с approval_version, "
                f"task_id, database_id, environment, operation, mode, scope_hash, "
                f"approved_by, approved_at, expires_at)"
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

        status = str(meta.get("status", "")).strip()
        if status != "approved":
            self.fail(f"SDD: status='{status or '(отсутствует)'}' — требуется 'approved'")

        risk = str(meta.get("risk", "")).strip().lower()
        approved_by = str(meta.get("approved_by", "")).strip()
        if not approved_by:
            self.fail("SDD: approved_by пуст — само-подтверждение запрещено")
        else:
            is_high = risk == "high" or any(m.lower() in spec_text.lower() for m in HIGH_RISK_MARKERS)
            deny_set = HIGH_RISK_SELF_APPROVAL_DENY if is_high else SELF_APPROVAL_DENY
            for denied in deny_set:
                if approved_by.lower() == denied.lower():
                    self.fail(f"SDD: approved_by='{approved_by}' — само-подтверждение запрещено")

        approved_at = str(meta.get("approved_at", "")).strip()
        if not approved_at:
            self.fail("SDD: approved_at пуст — timestamp подтверждения отсутствует")

        spec_version = str(meta.get("spec_version", "")).strip()

        # --- scope_hash: ВСЕГДА заново вычисляется из spec (P0-4) ---
        if compute_scope_hash_from_file is not None:
            computed_hash = compute_scope_hash_from_file(spec_path)
        else:
            computed_hash = ""
        if not computed_hash:
            self.fail("SDD: не удалось вычислить scope_hash из spec (нет секций «Границы изменения»/«Затрагиваемые файлы»)")
        else:
            if validate_hash_format and not validate_hash_format(computed_hash):
                self.fail(f"SDD: вычисленный scope_hash имеет неверный формат (ожидается 64 hex)")

        # Сверка с yaml-блоком spec
        spec_stored_hash = str(meta.get("scope_hash", "")).strip()
        if spec_stored_hash and computed_hash and spec_stored_hash != computed_hash:
            self.fail(
                f"SDD: scope_hash в yaml-блоке spec не совпадает с заново вычисленным — "
                f"spec изменение scope после approval аннулирует подтверждение"
            )

        # --- review.md обязателен (P0-6) ---
        review_path = self.specs_dir / task / "review.md"
        if not review_path.exists():
            self.fail("SDD: review.md отсутствует — review обязателен перед любым применением")
            return
        review_text = _read_text(review_path)
        review_meta = _parse_yaml_block(review_text)

        verdict = str(review_meta.get("verdict", "")).strip().lower()
        if verdict != "approved":
            self.fail(f"SDD: review verdict='{verdict}' — требуется 'approved'")

        reviewed_by = str(review_meta.get("reviewed_by", "")).strip()
        reviewed_at = str(review_meta.get("reviewed_at", "")).strip()
        if not reviewed_by:
            self.fail("SDD: reviewed_by в review.md пуст")
        if not reviewed_at:
            self.fail("SDD: reviewed_at в review.md пуст")
        if approved_by and reviewed_by and approved_by.lower() == reviewed_by.lower():
            self.fail(f"SDD: нарушение независимости — reviewed_by='{reviewed_by}' совпадает с approved_by")

        review_spec_version = str(review_meta.get("spec_version", "")).strip()
        if spec_version and review_spec_version and spec_version != review_spec_version:
            self.fail(f"SDD: spec_version расходится (spec={spec_version}, review={review_spec_version})")

        # scope_hash в review: должен совпадать с вычисленным
        review_stored_hash = str(review_meta.get("scope_hash", "")).strip()
        if computed_hash and review_stored_hash and review_stored_hash != computed_hash:
            self.fail("SDD: scope_hash в review.md не совпадает с заново вычисленным — scope изменён после review")

        # --- 06_change_report.md ---
        report_path = self.specs_dir / task / "06_change_report.md"
        if not report_path.exists():
            self.fail(f"SDD: 06_change_report.md отсутствует: {report_path}")
            return
        report_text = _read_text(report_path)
        report_meta = _parse_yaml_block(report_text)
        report_stored_hash = str(report_meta.get("scope_hash", "")).strip()
        if computed_hash and report_stored_hash and report_stored_hash != computed_hash:
            self.fail("SDD: scope_hash в 06_change_report.md не совпадает с заново вычисленным — scope drift")

    def check_plan_files(self, task: str) -> list[str]:
        """P0-5: строгая проверка плана файлов. Возвращает список плановых путей."""
        if not task:
            return []
        report_path = self.specs_dir / task / "06_change_report.md"
        if not report_path.exists():
            return []
        text = _read_text(report_path)

        # Проверка наличия раздела «Изменённые файлы»
        if "Изменённые файлы" not in text and "## Изменённые" not in text:
            self.fail("план: раздел «Изменённые файлы» отсутствует в 06_change_report.md — apply заблокирован")
            return []

        raw_paths = re.findall(r"(projects/[^\s`]+?/src/[^\s`]+)", text)
        if not raw_paths:
            self.fail("план: пустой список файлов в 06_change_report.md — apply заблокирован")
            return []

        seen = set()
        plan_files = []
        for p in raw_paths:
            p = p.rstrip(",.;:—-")
            if p in seen:
                self.fail(f"план: дубликат пути '{p}' в 06_change_report.md")
                continue
            seen.add(p)
            # Абсолютный путь
            if p.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", p):
                self.fail(f"план: абсолютный путь заблокирован: '{p}'")
                continue
            # Path traversal
            if ".." in p.split("/"):
                self.fail(f"план: path traversal заблокирован: '{p}'")
                continue
            # Существование файла
            if not (ROOT / p).exists():
                self.fail(f"план: файл отсутствует (пропуск запрещён): '{p}'")
            plan_files.append(p)

        return plan_files

    def check_cli_files_match(self, plan_files: list[str]) -> None:
        """P0-5: CLI --files должен точно совпадать с планом из 06_change_report.md."""
        if not plan_files and not self.cli_files:
            return
        cli_set = set()
        if self.cli_files:
            cli_set = {f.strip().replace("\\", "/") for f in self.cli_files.split(",") if f.strip()}
        plan_set = {f.replace("\\", "/") for f in plan_files}

        extra = cli_set - plan_set
        missing = plan_set - cli_set
        if extra:
            self.fail(f"план: лишние файлы в --files (отсутствуют в плане): {sorted(extra)}")
        if missing:
            self.fail(f"план: отсутствуют файлы из плана в --files: {sorted(missing)}")

    def check_backup(self, task: str, db_id: str) -> None:
        """P0-7: обязательный backup-gate — машиночитаемая запись backup.md."""
        if not task or not db_id:
            return
        backup_path = self.specs_dir / task / "backup.md"
        if not backup_path.exists():
            self.fail(f"backup: backup.md отсутствует: {backup_path} — apply заблокирован (требуется свежий backup)")
            return
        backup_text = _read_text(backup_path)
        backup_meta = _parse_yaml_block(backup_text)

        status = str(backup_meta.get("status", "")).strip().lower()
        if status != "success":
            self.fail(f"backup: status='{status}' — требуется 'success'")
            return

        backup_db = str(backup_meta.get("database_id", "")).strip()
        if backup_db != db_id:
            self.fail(f"backup: database_id='{backup_db}' не совпадает с целевой базой '{db_id}'")

        backup_env = str(backup_meta.get("environment", "")).strip()
        if backup_env == "production":
            self.fail("backup: production backup не разрешает apply к production")
        if self._db_env and backup_env and backup_env != self._db_env:
            self.fail(f"backup: environment='{backup_env}' не совпадает с целевой средой '{self._db_env}'")

        artifact = str(backup_meta.get("artifact", "")).strip()
        if not artifact:
            self.fail("backup: artifact пуст — путь к файлу backup отсутствует")
        elif not validate_path_safe(artifact, ROOT):
            self.fail(f"backup: artifact путь небезопасен: '{artifact}'")
        elif not (ROOT / artifact).exists():
            self.fail(f"backup: файл backup не существует: '{artifact}'")

        created_at = str(backup_meta.get("created_at", "")).strip()
        if created_at:
            try:
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                age_hours = (now - created).total_seconds() / 3600
                if age_hours > BACKUP_MAX_AGE_HOURS:
                    self.fail(f"backup: просрочен (возраст {age_hours:.1f}ч > {BACKUP_MAX_AGE_HOURS}ч)")
                if age_hours < -1:
                    self.fail(f"backup: created_at в будущем — подозрительно")
            except Exception:
                self.warn(f"backup: created_at='{created_at}' не распознан как ISO timestamp")

    def check_external_approval(self, task: str, db_id: str, op: str, mode: str) -> None:
        """P0-6: внешний approval для особо опасных операций."""
        if not self._is_extra_dangerous:
            return
        if not task:
            return
        approval_path = self.specs_dir / task / "approval.md"
        if not approval_path.exists():
            self.fail(
                f"approval: approval.md отсутствует — особо опасная операция '{op}' "
                f"требует отдельного внешнего approval"
            )
            return
        approval_text = _read_text(approval_path)
        approval_meta = _parse_yaml_block(approval_text)

        ap_version = str(approval_meta.get("approval_version", "")).strip()
        if not ap_version:
            self.fail("approval: approval_version пуст")
        ap_task = str(approval_meta.get("task_id", "")).strip()
        if ap_task != task:
            self.fail(f"approval: task_id='{ap_task}' не совпадает с '{task}'")
        ap_db = str(approval_meta.get("database_id", "")).strip()
        if ap_db != db_id:
            self.fail(f"approval: database_id='{ap_db}' не совпадает с '{db_id}'")
        ap_env = str(approval_meta.get("environment", "")).strip()
        if ap_env == "production":
            self.fail("approval: production — операции запрещены всегда")
        if self._db_env and ap_env and ap_env != self._db_env:
            self.fail(f"approval: environment='{ap_env}' не совпадает с '{self._db_env}'")
        ap_op = str(approval_meta.get("operation", "")).strip()
        if ap_op and ap_op != op:
            self.fail(f"approval: operation='{ap_op}' не совпадает с '{op}'")
        ap_mode = str(approval_meta.get("mode", "")).strip()
        if ap_mode and ap_mode != mode:
            self.fail(f"approval: mode='{ap_mode}' не совпадает с '{mode}'")
        ap_by = str(approval_meta.get("approved_by", "")).strip()
        if not ap_by:
            self.fail("approval: approved_by пуст")
        ap_at = str(approval_meta.get("approved_at", "")).strip()
        if not ap_at:
            self.fail("approval: approved_at пуст")
        ap_expires = str(approval_meta.get("expires_at", "")).strip()
        if ap_expires:
            try:
                expires = datetime.fromisoformat(ap_expires.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if now > expires:
                    self.fail(f"approval: истёк (expires_at={ap_expires})")
            except Exception:
                self.warn(f"approval: expires_at='{ap_expires}' не распознан")

        # scope_hash в approval должен совпадать
        if task:
            spec_path = self.specs_dir / task / "03_solution_spec.md"
            if spec_path.exists() and compute_scope_hash_from_file is not None:
                computed = compute_scope_hash_from_file(spec_path)
                ap_hash = str(approval_meta.get("scope_hash", "")).strip()
                if computed and ap_hash and ap_hash != computed:
                    self.fail("approval: scope_hash не совпадает с заново вычисленным")

    def check_tools(self, op: str) -> None:
        if op not in DANGEROUS_OPS:
            return
        import shutil
        found = shutil.which("1cv8") or shutil.which("ibcmd") or shutil.which("1cv8c")
        if not found:
            self.warn("инструменты: 1cv8/ibcmd не найдены в PATH — best-effort")

    def run(self, task: str, db: str, op: str, mode: str) -> int:
        if op not in DANGEROUS_OPS and op:
            self.warn(f"op '{op}' не в списке опасных — guard применил только проверки environment/базы")
        cfg = self.load_config()
        self.check_environment(cfg, db)
        self.check_task_id(task)
        self.check_operation_class(op, mode)
        self.check_sdd(task)
        plan_files = self.check_plan_files(task)
        self.check_cli_files_match(plan_files)
        self.check_backup(task, self._db_id)
        self.check_external_approval(task, self._db_id, op, mode)
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
        description="Preflight-guard для 1c-applier (read-only проверки перед опасными операциями).",
    )
    parser.add_argument("--task", default="", help="TASK-ID (строгий формат TASK-<alnum>)")
    parser.add_argument("--db", required=True, help="id базы из .v8-project.json")
    parser.add_argument("--op", required=True, choices=sorted(DANGEROUS_OPS), help="класс операции")
    parser.add_argument("--mode", default="Partial", choices=["Full", "Partial"], help="режим загрузки")
    parser.add_argument("--files", default="", help="CLI --files (относительные пути через запятую)")
    parser.add_argument("--config", default=str(ROOT / ".v8-project.json"), help="путь к .v8-project.json")
    parser.add_argument("--specs-dir", default=str(ROOT / "specs"), help="каталог specs/")
    args = parser.parse_args()

    guard = Guard(Path(args.config).resolve(), Path(args.specs_dir).resolve(),
                 args.files.strip(), args.mode)
    try:
        return guard.run(args.task.strip(), args.db.strip(), args.op.strip(), args.mode)
    except Exception as e:
        print(f"ERROR guard: непредвиденная ошибка: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
