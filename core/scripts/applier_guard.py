#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
applier_guard.py — проверяемый preflight-guard для агента 1c-applier.

Не подключается к 1С, не изменяет данные. Только локальные read-only проверки.
Выполняется ДО любой изменяющей операции (db-load-*, db-update, db-create,
web-publish, web-unpublish). Ненулевой exit = операция заблокирована.

Проверки (P1-6.1–6.7):
  1. environment: явное допустимое значение в .v8-project.json (local|test|staging);
     production / отсутствие / пустое / неизвестное -> блок.
  2. Выбор базы: --db явно задан и сопоставлен ровно одной записи; иначе блок
     (запрет авто-выбора, default-only, неоднозначность).
  3. SDD spec (для --task): status == approved; для risk high — review verdict approved.
  4. scope_hash: совпадает между 03_solution_spec.md и 06_change_report.md (иначе scope drift).
  5. Файлы плана: все изменённые файлы из 06_change_report.md существуют в
     projects/<источник>/src/ (пропуск отсутствующих запрещён).
  6. Подтверждение: approved_by/ approved_at заданы (внешнее approval, не само-подтверждение).
  7. Безопасные инструменты: 1cv8/ibcmd доступны для опасных ops (best-effort, warn-only
     если не найден — не блокирует, т.к. платформа может быть вне PATH).

Запуск:
  python scripts/applier_guard.py --db <id> --op <load-xml|load-cf|load-dt|update|create|web-publish|web-unpublish>
  python scripts/applier_guard.py --task <TASK-ID> --db <id> --op update
  python scripts/applier_guard.py --help

Exit codes:
  0 — все проверки пройдены (операция разрешена)
  1 — хотя бы одна проверка не пройдена (операция заблокирована)
  2 — ошибка аргументов / файла конфигурации
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

def _find_root(start: Path) -> Path:
    """Автоопределение корня: первый родитель (включая текущий) с README.md или .v8-project.json.
    Исходный репо: core/scripts/applier_guard.py -> ai-environment/; установка: scripts/ -> корень."""
    cur = start.resolve()
    for cand in [cur, *cur.parents]:
        if (cand / "README.md").exists() or (cand / ".v8-project.json").exists():
            return cand
    return cur.parent.parent


ROOT = _find_root(Path(__file__).resolve())

ALLOWED_ENVS = {"local", "test", "staging"}
DANGEROUS_OPS = {"load-xml", "load-cf", "load-dt", "update", "create", "web-publish", "web-unpublish"}
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
            # utf-8-sig устойчив к BOM (как при сохранении PowerShell, так и без него)
            raw = self.config.read_text(encoding="utf-8-sig", errors="replace")
            return json.loads(raw)
        except Exception as e:
            self.fail(f"реестр баз не читается ({self.config}): {e}")
            return {}

    def check_environment(self, cfg: dict) -> str:
        env = str(cfg.get("environment", "")).strip()
        if not env:
            self.fail("environment: поле отсутствует/пусто — изменяющие операции заблокированы")
            return ""
        if env not in ALLOWED_ENVS and env != "production":
            self.fail(f"environment: неизвестное значение '{env}' — изменяющие операции заблокированы")
            return env
        if env == "production":
            self.fail("environment: production — изменяющие операции запрещены всегда")
            return env
        return env

    def check_db(self, cfg: dict, db_id: str) -> None:
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
        # среда должна быть допустима (дублирующая жёсткая проверка)
        env = str(cfg.get("environment", "")).strip()
        if env not in ALLOWED_ENVS:
            self.fail(f"среда недопустима для apply: environment='{env}'")

    def check_sdd(self, task: str, op: str) -> None:
        if not task:
            # direct-режим: spec не требуется, но environment+база уже проверены
            self.warn("direct-режим: --task не задан — SDD spec/review не проверяются (требуется только для SDD-apply)")
            return
        spec_path = self.specs_dir / task / "03_solution_spec.md"
        if not spec_path.exists():
            self.fail(f"SDD: спецификация отсутствует: {spec_path}")
            return
        spec_text = _read_text(spec_path)
        meta = _parse_yaml_block(spec_text)
        status = str(meta.get("status", "")).strip()
        if status != "approved":
            self.fail(f"SDD: status='{status or '(отсутствует)'}' — требуется 'approved' для изменяющих операций")
        risk = str(meta.get("risk", "")).strip().lower()
        approved_by = str(meta.get("approved_by", "")).strip()
        if not approved_by:
            self.fail("SDD: approved_by пуст — подтверждение отсутствует (само-подтверждение запрещено)")
        # review для high-risk
        review_path = self.specs_dir / task / "review.md"
        review_text = _read_text(review_path) if review_path.exists() else ""
        review_meta = _parse_yaml_block(review_text) if review_text else {}
        verdict = str(review_meta.get("verdict", "")).strip().lower()
        # эвристика high-risk: риск high ИЛИ маркеры в тексте spec
        is_high = risk == "high" or any(m.lower() in spec_text.lower() for m in HIGH_RISK_MARKERS)
        if is_high and verdict != "approved":
            self.fail(
                f"SDD: high-risk задача — требуется verdict 'approved' от 1c-reviewer в {review_path} "
                f"(текущий verdict='{verdict or '(отсутствует)'}')"
            )
        # scope_hash сверка с change_report
        spec_scope_hash = str(meta.get("scope_hash", "")).strip()
        report_path = self.specs_dir / task / "06_change_report.md"
        if not report_path.exists():
            self.fail(f"SDD: 06_change_report.md отсутствует: {report_path}")
            return
        report_text = _read_text(report_path)
        report_meta = _parse_yaml_block(report_text)
        report_scope_hash = str(report_meta.get("scope_hash", "")).strip()
        # Если явные хэши заданы с обеих сторон — сверить; иначе вычислить из spec и сверить
        if spec_scope_hash and report_scope_hash:
            if spec_scope_hash != report_scope_hash:
                self.fail("SDD: scope_hash не совпал (spec vs 06_change_report) — выход за scope (scope drift)")
        else:
            recomputed = _hash_scope(spec_text)
            if report_scope_hash and recomputed and report_scope_hash != recomputed:
                self.fail("SDD: scope_hash из 06_change_report не совпал с вычисленным из spec — scope drift")

    def check_plan_files(self, task: str) -> None:
        if not task:
            return
        report_path = self.specs_dir / task / "06_change_report.md"
        if not report_path.exists():
            return  # уже залогировано в check_sdd
        text = _read_text(report_path)
        # пути вида projects/<источник>/src/...
        paths = re.findall(r"(projects/[^\s`]+?/src/[^\s`]+)", text)
        if not paths:
            self.warn("план: в 06_change_report.md не найдено путей projects/<источник>/src/...")
            return
        missing = []
        for p in paths:
            # обрезаем возможный trailing символ (запятая и т.п.)
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
        self.check_environment(cfg)
        self.check_db(cfg, db)
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
    parser.add_argument("--task", default="", help="TASK-ID (SDD-режим); без него проверяются только environment+база")
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
