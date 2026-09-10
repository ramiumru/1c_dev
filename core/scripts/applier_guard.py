#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
applier_guard.py — проверяемый preflight-guard для агента 1c-applier.

Не подключается к 1С, не изменяет данные. Только локальные read-only проверки.
Выполняется ДО любой изменяющей операции (db-load-*, db-update, db-create,
web-publish, web-unpublish). Ненулевой exit = операция заблокирована.

Проверки:
  1. environment: per-db значение в записи базы (local|test|staging);
     production / отсутствие / неизвестное -> блок.
     Для серверных баз дополнительно требуется allow_apply: true.
     Верхнеуровневый environment — только fallback для файловых баз.
  2. Выбор базы: --db явно задан и сопоставлен ровно одной записи; иначе блок
     (запрет авто-выбора, default-only, неоднозначность).
  3. --task обязателен для опасных ops (direct-режим без --task блокирует).
  4. SDD spec: status == approved; approved_by/approved_at непустые;
     approved_by не равен 1c-developer (само-подтверждение запрещено);
     для high-risk — approved_by не равен 1c-analyst.
  5. Review обязателен для ВСЕХ опасных ops (не только high-risk):
     review.md с verdict == approved; reviewed_by/reviewed_at непустые;
     для high-risk — reviewed_by != approved_by (независимость).
  6. spec_version: совпадает между 03_solution_spec.md и review.md.
  7. scope_hash: непустой в spec; совпадает между spec, 06_change_report.md и review.md.
  8. Файлы плана: все изменённые файлы из 06_change_report.md существуют в
     projects/<источник>/src/ (пропуск отсутствующих запрещён).
  9. Безопасные инструменты: 1cv8/ibcmd доступны для опасных ops (best-effort, warn-only).

Запуск:
  python scripts/applier_guard.py --task <TASK-ID> --db <id> --op <load-xml|load-cf|load-dt|update|create|web-publish|web-unpublish>
  python scripts/applier_guard.py --help

Exit codes:
  0 — все проверки пройдены (операция разрешена)
  1 — хотя бы одна проверка не пройдена (операция заблокирована)
  2 — ошибка аргументов / файла конфигурации / корень не найден
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import hashlib
except Exception:  # pragma: no cover
    hashlib = None

# Импорт общего модуля детекции корня
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _root import find_root as _find_root_shared
except ImportError:
    _find_root_shared = None


def _find_root(start: Path) -> Path:
    """Автоопределение корня через общий модуль _root или fallback."""
    if _find_root_shared is not None:
        root = _find_root_shared(start)
        if root is not None:
            return root
    # Fallback: исходимный репо core/scripts -> родитель+родитель; установка scripts -> родитель
    cur = start.resolve()
    return cur.parent.parent


ROOT = _find_root(Path(__file__).resolve())

ALLOWED_ENVS = {"local", "test", "staging"}
DANGEROUS_OPS = {"load-xml", "load-cf", "load-dt", "update", "create", "web-publish", "web-unpublish"}
HIGH_RISK_MARKERS = (
    "проведени", "движени", "транзакци", "блокиров", "RLS", "права",
    "регламентн", "фонов", "экспортн", "метаданны", "интеграционн",
    "структур", "массовое", "обмен", "финансов",
)

# Субъекты, которым запрещено утверждать собственную работу
SELF_APPROVAL_DENY = {"1c-developer"}
# Для high-risk — аналитик также не может утверждать собственную spec
HIGH_RISK_SELF_APPROVAL_DENY = {"1c-developer", "1c-analyst"}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _parse_yaml_block(text: str, block_name: str = "yaml") -> dict:
    """Извлекает первый fenced ```block блок и парсит плоские key: value строки."""
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


def _hash_scope(text: str) -> str:
    """sha256 от канонизированного текста 'Границы изменения' + 'Затрагиваемые файлы'."""
    if hashlib is None:
        return ""
    parts = []
    for header in ("Границы изменения", "Затрагиваемые файлы"):
        m = re.search(rf"^##\s*{re.escape(header)}\s*\r?\n(.*?)(?=^##\s|\Z)", text, re.S | re.M)
        if m:
            chunk = m.group(1)
            chunk = re.sub(r"\s+", " ", chunk).strip()
            parts.append(chunk)
    return hashlib.sha256("\n".join(parts).encode("utf-8", "ignore")).hexdigest()


class Guard:
    def __init__(self, config: Path, specs_dir: Path):
        self.config = config
        self.specs_dir = specs_dir
        self.failures: list[str] = []
        self.warnings: list[str] = []

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

    def check_environment(self, cfg: dict, db_id: str) -> None:
        """Проверка environment per-db (8.4): среда ищется в записи базы, не только глобально."""
        if not db_id:
            self.fail("выбор базы: --db не задан — авто-выбор запрещён")
            return
        dbs = cfg.get("databases") or []
        if not isinstance(dbs, list) or not dbs:
            self.fail("выбор базы: реестр баз пуст")
            return
        matches = [d for d in dbs if isinstance(d, dict) and d.get("id") == db_id]
        if not matches:
            self.fail(f"выбор базы: база '{db_id}' не зарегистрирована (авто-выбор запрещён)")
            return
        if len(matches) > 1:
            self.fail(f"выбор базы: несколько записей с id '{db_id}' — неоднозначно")
            return
        db = matches[0]

        # plaintext credentials — небезопасно
        if "user" in db or "password" in db:
            self.fail(
                "безопасность: в записи базы найдены plaintext user/password — мигрируйте на "
                "username_env/password_env; изменяющие операции не выполнять"
            )

        # Environment per-db (8.4): ищем в записи базы, fallback на глобальный только для файловых
        db_env = str(db.get("environment", "")).strip()
        global_env = str(cfg.get("environment", "")).strip()
        db_type = str(db.get("type", "")).strip().lower()
        is_server = db_type == "server"

        if not db_env:
            if is_server:
                # Серверная база без per-db environment — блок, даже если глобальный env задан
                self.fail(
                    f"environment: серверная база '{db_id}' без per-db environment — "
                    f"глобальный environment='{global_env or '(отсутствует)'}' недостаточен для серверных баз"
                )
            elif global_env:
                # Файловая база: fallback на глобальный
                db_env = global_env
            else:
                self.fail(f"environment: база '{db_id}' — per-db и глобальный environment отсутствуют")
                return

        if db_env not in ALLOWED_ENVS and db_env != "production":
            self.fail(f"environment: неизвестное значение '{db_env}' для базы '{db_id}' — изменяющие операции заблокированы")
            return
        if db_env == "production":
            self.fail(f"environment: production (база '{db_id}') — изменяющие операции запрещены всегда")
            return

        # Для серверных баз — дополнительный явный признак allow_apply (8.4)
        if is_server:
            allow_apply = db.get("allow_apply")
            if not (allow_apply is True or str(allow_apply).strip().lower() == "true"):
                self.fail(
                    f"environment: серверная база '{db_id}' без allow_apply: true — "
                    f"применение к серверным базам требует явного разрешения"
                )

    def check_task_required(self, task: str, op: str) -> None:
        """8.1: --task обязателен для опасных ops; direct-режим без --task блокирует."""
        if not task:
            self.fail(
                f"direct-режим: --task не задан для опасной операции '{op}' — "
                f"опасные операции требуют --task с утверждённой spec и review (direct-обход заблокирован)"
            )

    def check_sdd(self, task: str, op: str) -> None:
        if not task:
            return  # уже залогировано в check_task_required

        spec_path = self.specs_dir / task / "03_solution_spec.md"
        if not spec_path.exists():
            self.fail(f"SDD: спецификация отсутствует: {spec_path}")
            return
        spec_text = _read_text(spec_path)
        meta = _parse_yaml_block(spec_text)

        # --- status ---
        status = str(meta.get("status", "")).strip()
        if status != "approved":
            self.fail(f"SDD: status='{status or '(отсутствует)'}' — требуется 'approved' для изменяющих операций")

        # --- risk ---
        risk = str(meta.get("risk", "")).strip().lower()

        # --- approved_by (8.3) ---
        approved_by = str(meta.get("approved_by", "")).strip()
        if not approved_by:
            self.fail("SDD: approved_by пуст — подтверждение отсутствует (само-подтверждение запрещено)")
        else:
            # Само-подтверждение запрещено (8.3)
            deny_set = HIGH_RISK_SELF_APPROVAL_DENY if risk == "high" else SELF_APPROVAL_DENY
            # Эвристика high-risk: риск high ИЛИ маркеры в тексте spec
            is_high = risk == "high" or any(m.lower() in spec_text.lower() for m in HIGH_RISK_MARKERS)
            if is_high:
                deny_set = HIGH_RISK_SELF_APPROVAL_DENY
            for denied in deny_set:
                if approved_by.lower() == denied.lower():
                    self.fail(
                        f"SDD: approved_by='{approved_by}' — само-подтверждение запрещено "
                        f"(субъект '{denied}' не может утверждать {'собственную high-risk spec' if is_high else 'собственную работу'})"
                    )

        # --- approved_at (8.3) ---
        approved_at = str(meta.get("approved_at", "")).strip()
        if not approved_at:
            self.fail("SDD: approved_at пуст — timestamp подтверждения отсутствует")

        # --- spec_version (8.3) ---
        spec_version = str(meta.get("spec_version", "")).strip()

        # --- scope_hash в spec (8.3) ---
        spec_scope_hash = str(meta.get("scope_hash", "")).strip()
        if not spec_scope_hash:
            self.fail("SDD: scope_hash в spec пуст/null — требуется вычислить и заполнить перед apply")

        # --- review.md обязателен для ВСЕХ опасных ops (8.2) ---
        review_path = self.specs_dir / task / "review.md"
        if not review_path.exists():
            self.fail(
                f"SDD: review.md отсутствует: {review_path} — "
                f"review обязателен перед любым применением в базу (не только для high-risk)"
            )
            return
        review_text = _read_text(review_path)
        review_meta = _parse_yaml_block(review_text)

        # --- verdict (8.2) ---
        verdict = str(review_meta.get("verdict", "")).strip().lower()
        if verdict != "approved":
            self.fail(
                f"SDD: review verdict='{verdict or '(отсутствует)'}' — требуется 'approved' в {review_path}"
            )

        # --- reviewed_by / reviewed_at (8.3) ---
        reviewed_by = str(review_meta.get("reviewed_by", "")).strip()
        reviewed_at = str(review_meta.get("reviewed_at", "")).strip()
        if not reviewed_by:
            self.fail("SDD: reviewed_by в review.md пуст — рецензент не указан")
        if not reviewed_at:
            self.fail("SDD: reviewed_at в review.md пуст — timestamp review отсутствует")

        # --- Независимость review (8.3): reviewed_by != approved_by ---
        if approved_by and reviewed_by and approved_by.lower() == reviewed_by.lower():
            self.fail(
                f"SDD: нарушение независимости — reviewed_by='{reviewed_by}' совпадает с approved_by='{approved_by}' "
                f"(рецензент не может быть тем же субъектом, что утверждающий)"
            )

        # --- spec_version в review (8.3) ---
        review_spec_version = str(review_meta.get("spec_version", "")).strip()
        if spec_version and review_spec_version and spec_version != review_spec_version:
            self.fail(
                f"SDD: spec_version расходится — spec={spec_version}, review={review_spec_version} "
                f"(review мог быть проведён по устаревшей версии spec)"
            )

        # --- scope_hash в review (8.3) ---
        review_scope_hash = str(review_meta.get("scope_hash", "")).strip()
        if spec_scope_hash and review_scope_hash and spec_scope_hash != review_scope_hash:
            self.fail(
                f"SDD: scope_hash расходится — spec vs review (review проведён по другому scope)"
            )

        # --- 06_change_report.md ---
        report_path = self.specs_dir / task / "06_change_report.md"
        if not report_path.exists():
            self.fail(f"SDD: 06_change_report.md отсутствует: {report_path}")
            return
        report_text = _read_text(report_path)
        report_meta = _parse_yaml_block(report_text)
        report_scope_hash = str(report_meta.get("scope_hash", "")).strip()

        # scope_hash сверка spec vs report (8.3)
        if spec_scope_hash and report_scope_hash:
            if spec_scope_hash != report_scope_hash:
                self.fail("SDD: scope_hash не совпал (spec vs 06_change_report) — выход за scope (scope drift)")
        elif report_scope_hash:
            # spec hash задан, report hash — вычислить и сверить
            recomputed = _hash_scope(spec_text)
            if recomputed and report_scope_hash != recomputed:
                self.fail("SDD: scope_hash из 06_change_report не совпал с вычисленным из spec — scope drift")
        else:
            self.fail("SDD: scope_hash в 06_change_report.md пуст — требуется заполнить перед apply")

    def check_plan_files(self, task: str) -> None:
        if not task:
            return
        report_path = self.specs_dir / task / "06_change_report.md"
        if not report_path.exists():
            return  # уже залогировано в check_sdd
        text = _read_text(report_path)
        paths = re.findall(r"(projects/[^\s`]+?/src/[^\s`]+)", text)
        if not paths:
            self.warn("план: в 06_change_report.md не найдено путей projects/<источник>/src/...")
            return
        missing = []
        for p in paths:
            p = p.rstrip(",.;:—-")
            if not (ROOT / p).exists():
                missing.append(p)
        if missing:
            self.fail(f"план: отсутствуют файлы плана (пропуск запрещён): {missing}")

    def check_tools(self, op: str) -> None:
        if op not in DANGEROUS_OPS:
            return
        import shutil
        found = shutil.which("1cv8") or shutil.which("ibcmd") or shutil.which("1cv8c")
        if not found:
            self.warn("инструменты: 1cv8/ibcmd не найдены в PATH — best-effort (платформа может быть вне PATH)")

    def run(self, task: str, db: str, op: str) -> int:
        if op not in DANGEROUS_OPS and op:
            self.warn(f"op '{op}' не в списке опасных — guard применил только проверки environment/базы")
        cfg = self.load_config()
        self.check_environment(cfg, db)
        self.check_task_required(task, op)
        self.check_sdd(task, op)
        self.check_plan_files(task)
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
    parser.add_argument("--task", default="", help="TASK-ID (обязателен для опасных ops; direct-режим заблокирован)")
    parser.add_argument("--db", required=True, help="id базы из .v8-project.json (явный, авто-выбор запрещён)")
    parser.add_argument("--op", required=True, choices=sorted(DANGEROUS_OPS), help="класс операции")
    parser.add_argument("--config", default=str(ROOT / ".v8-project.json"), help="путь к .v8-project.json")
    parser.add_argument("--specs-dir", default=str(ROOT / "specs"), help="каталог specs/")
    args = parser.parse_args()

    guard = Guard(Path(args.config).resolve(), Path(args.specs_dir).resolve())
    try:
        return guard.run(args.task.strip(), args.db.strip(), args.op.strip())
    except Exception as e:
        print(f"ERROR guard: непредвиденная ошибка: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
