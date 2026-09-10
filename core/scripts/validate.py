#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate.py — единая переносимая локальная проверка агентской схемы 1С Dev.

Не требует платформы 1С, подключения к базе, корпоративной сети или секретов.
Только локальные read-only проверки.

Проверки:
  1. Синтаксис Python через compileall (core/scripts, core/skills/**/*.py).
  2. Наличие обязательных файлов (адаптируется: исходный репо или установленная раскладка).
  3. Внутренние Markdown-ссылки в README.md и docs/.
  4. Пути в документации и шаблонах адаптеров (базовая проверка install.ps1).
  5. Наличие всех агентов (core/agents/*.md или установленная раскладка).
  6. Наличие 1c-reviewer во всех адаптерах.
  7. Smoke-тест установщика (парсит frontmatter установленных агентов, не existence).
  8. SDD-статусы и risk gates.
  9. Guards для 1c-applier (applier_guard.py + safe_apply.py).
  10. Безопасность .v8-project.example.json.
  11. Отсутствие секретов в примерах.
  12. Отсутствие ошибочных упоминаний OpenCode.
  13. Adversarial-тесты guard (direct-bypass, review-missing, formal-approval,
      scope-hash-drift, self-approval, env-per-db) — должны падать на сломанных данных.
  14. Консистентность путей frontmatter (self-path каждого агента).
  15. Отсутствие избыточных прав 1c-do (нет meta-*/form-*/cf-*/cfe-* в bash/skill).

Запуск:
  python scripts/validate.py
  python scripts/validate.py --skip-smoke   (пропустить smoke-тест установщика)

Exit codes:
  0 — все проверки пройдены
  1 — есть ошибки (ERROR)
  2 — ошибка запуска/аргументов
"""

from __future__ import annotations

import argparse
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Импорт общего модуля детекции корня
sys.path.insert(0, str(SCRIPT_DIR))
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
    cur = start.resolve()
    return cur.parent.parent


ROOT = _find_root(SCRIPT_DIR)
# Определить режим: исходный репо (есть core/) или установленная раскладка (есть .kilo/agent или scripts/)
IS_SOURCE_REPO = (ROOT / "core" / "agents").is_dir()

EXPECTED_AGENTS = ["1c-do", "1c-analyst", "1c-developer", "1c-reviewer", "1c-applier", "1c-tools"]
ADAPTERS = ["kilo", "claude", "codex", "openworks"]

REQUIRED_FILES_SOURCE = [
    "README.md",
    "NOTICE.md",
    "THIRD_PARTY_LICENSES.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "examples/v8-project.example.json",
    "core/sdd/README.md",
    "core/scripts/applier_guard.py",
    "core/scripts/safe_apply.py",
    "core/scripts/bsl-check.py",
    "core/scripts/build_summaries.py",
    "core/scripts/validate.py",
    "core/scripts/doctor.py",
    "core/scripts/_root.py",
    "install/install.ps1",
    "core/context/.dev.env.example",
]

EXPECTED_RULES = [
    "bsl-standards.md",
    "sdd-implementation.md",
    "skill-reference.md",
    "developer-verification.md",
    "sdd-orchestration.md",
    "summaries-auto-update.md",
    "task-brief.md",
    "sdd-spec-authoring.md",
    "review-checklist.md",
    "apply-procedure.md",
    "triage.md",
]

REQUIRED_FILES_INSTALLED = [
    "examples/v8-project.example.json",
    "specs/README.md",
    "scripts/applier_guard.py",
    "scripts/safe_apply.py",
    "scripts/bsl-check.py",
    "scripts/build_summaries.py",
    "scripts/validate.py",
    "scripts/doctor.py",
    "scripts/_root.py",
]

ALLOWED_ENVS = {"local", "test", "staging"}


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.oks: list[str] = []

    def error(self, m: str) -> None:
        self.errors.append(m)

    def warn(self, m: str) -> None:
        self.warnings.append(m)

    def ok(self, m: str) -> None:
        self.oks.append(m)


def check_python_syntax(rep: Report) -> None:
    targets = []
    if (ROOT / "core" / "scripts").is_dir():
        targets.append(ROOT / "core" / "scripts")
    if (ROOT / "scripts").is_dir():
        targets.append(ROOT / "scripts")
    total = 0
    failed = 0
    for t in targets:
        for py in t.rglob("*.py"):
            total += 1
            try:
                py_compile.compile(str(py), doraise=True)
            except py_compile.PyCompileError as e:
                failed += 1
                rep.error(f"py-compile FAIL {py.relative_to(ROOT)}: {e}")
    skills_dirs = []
    if (ROOT / "core" / "skills").is_dir():
        skills_dirs.append(ROOT / "core" / "skills")
    for tool, sdir in [("kilo", ".kilo/skills"), ("claude", ".claude/skills"),
                        ("openworks", ".openworks/skills"), ("codex", "skills")]:
        sp = ROOT / sdir
        if sp.is_dir():
            skills_dirs.append(sp)
    for sd in skills_dirs:
        for py in sd.rglob("*.py"):
            total += 1
            try:
                py_compile.compile(str(py), doraise=True)
            except py_compile.PyCompileError as e:
                failed += 1
                rep.error(f"py-compile FAIL {py.relative_to(ROOT)}: {e}")
    if failed == 0:
        rep.ok(f"Python compileall: {total} файлов OK")
    else:
        rep.error(f"Python compileall: {failed}/{total} файлов с ошибками")


def check_required_files(rep: Report) -> None:
    req = REQUIRED_FILES_SOURCE if IS_SOURCE_REPO else REQUIRED_FILES_INSTALLED
    for f in req:
        p = ROOT / f
        if p.exists() and p.stat().st_size > 0:
            rep.ok(f"required: {f}")
        else:
            rep.error(f"required: отсутствует/пуст: {f}")


def check_md_links(rep: Report) -> None:
    md_files = []
    if (ROOT / "README.md").exists():
        md_files.append(ROOT / "README.md")
    docs = ROOT / "docs"
    if docs.is_dir():
        md_files += list(docs.rglob("*.md"))
    link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    for md in md_files:
        if not md.exists():
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for m in link_re.finditer(text):
            label, target = m.group(1), m.group(2)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            resolved = (md.parent / path_part).resolve()
            if not resolved.exists():
                rep.warn(f"md-link: {md.relative_to(ROOT)} -> {target} (не найден)")


def check_install_paths(rep: Report) -> None:
    ip = ROOT / "install" / "install.ps1"
    if not ip.exists():
        if not IS_SOURCE_REPO:
            return  # установленная раскладка без install/
        rep.error("install.ps1 отсутствует")
        return
    text = ip.read_text(encoding="utf-8", errors="replace")
    for needle in ["openworks", "ValidateSet", "adapters\\$Tool", "examples\\v8-project.example.json"]:
        if needle not in text:
            rep.error(f"install.ps1: отсутствует фрагмент '{needle}'")
    if "opencode" in text:
        rep.error("install.ps1: содержит устаревшее 'opencode' (ожидается openworks)")
    else:
        rep.ok("install.ps1: no opencode references")
    # 8.9: install.ps1 должен удалять существующие --- delimiters из frontmatter
    if "---`r`n$fm" in text or "$fm -replace" in text:
        rep.ok("install.ps1: defense-in-depth для frontmatter delimiters")
    else:
        rep.error("install.ps1: нет защиты от двойного frontmatter (8.9)")


def check_agents(rep: Report) -> None:
    agents_dir = ROOT / "core" / "agents" if IS_SOURCE_REPO else None
    # Установленная раскладка: ищем по путям адаптеров
    adapter_dirs = {
        "kilo": ".kilo/agent",
        "claude": ".claude/agents",
        "openworks": ".openworks/agents",
        "codex": "agents",
    }
    if IS_SOURCE_REPO:
        for a in EXPECTED_AGENTS:
            p = agents_dir / f"{a}.md"
            if p.exists() and p.stat().st_size > 0:
                rep.ok(f"agent: {a}.md")
            else:
                rep.error(f"agent: отсутствует/пуст: core/agents/{a}.md")
    else:
        for tool, adir in adapter_dirs.items():
            ap = ROOT / adir
            if ap.is_dir():
                missing = [a for a in EXPECTED_AGENTS if not (ap / f"{a}.md").exists()]
                if missing:
                    rep.error(f"{tool} ({adir}): отсутствуют: {', '.join(missing)}")
                else:
                    rep.ok(f"{tool} ({adir}): все {len(EXPECTED_AGENTS)} агентов")


def check_reviewer_in_adapters(rep: Report) -> None:
    for ad in ADAPTERS:
        ad_dir = ROOT / "adapters" / ad if IS_SOURCE_REPO else None
        if not ad_dir or not ad_dir.is_dir():
            continue
        found = False
        fm = ad_dir / "frontmatter" / "1c-reviewer.yml"
        if fm.exists():
            found = True
        tpl = ad_dir / "AGENTS.md.tpl"
        if tpl.exists() and "1c-reviewer" in tpl.read_text(encoding="utf-8", errors="replace"):
            found = True
        if found:
            rep.ok(f"adapter {ad}: 1c-reviewer присутствует")
        else:
            rep.error(f"adapter {ad}: 1c-reviewer отсутствует")


def check_sdd_risk_gates(rep: Report) -> None:
    spec_readme = ROOT / "core" / "sdd" / "README.md" if IS_SOURCE_REPO else ROOT / "specs" / "README.md"
    if not spec_readme.exists():
        rep.error("SDD README не найден")
        return
    text = spec_readme.read_text(encoding="utf-8", errors="replace")
    for needle in ["status:", "risk:", "approved_by", "approved_at", "scope_hash", "1c-reviewer", "review.md"]:
        if needle not in text:
            rep.error(f"SDD README: отсутствует риск-gate элемент '{needle}'")
    if "```yaml" not in text or "approved" not in text:
        rep.error("SDD README: шаблон spec не содержит машиночитаемый yaml-блок status/risk")
    rep.ok("SDD README: risk gates присутствуют")


def check_applier_guards(rep: Report) -> None:
    g = ROOT / "core" / "scripts" / "applier_guard.py" if IS_SOURCE_REPO else ROOT / "scripts" / "applier_guard.py"
    if not g.exists():
        rep.error("applier_guard.py отсутствует")
        return
    # Проверка блокировки production
    with tempfile.TemporaryDirectory(prefix="applier_guard_") as td:
        tdpath = Path(td)
        cfg = {"environment": "production", "databases": [{"id": "db1", "type": "file", "path": "x", "environment": "production"}]}
        (tdpath / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(g), "--task", "T1", "--db", "db1", "--op", "update", "--config", str(tdpath / "cfg.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0 and "production" in (r.stdout + r.stderr).lower():
            rep.ok("applier_guard: блокирует production")
        else:
            rep.error(f"applier_guard: НЕ блокирует production (exit={r.returncode})")
        # Отсутствие environment -> блок
        cfg2 = {"databases": [{"id": "db1", "type": "file", "path": "x"}]}
        (tdpath / "cfg2.json").write_text(json.dumps(cfg2), encoding="utf-8")
        r2 = subprocess.run(
            [sys.executable, str(g), "--task", "T1", "--db", "db1", "--op", "update", "--config", str(tdpath / "cfg2.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r2.returncode != 0:
            rep.ok("applier_guard: блокирует отсутствие environment")
        else:
            rep.error("applier_guard: НЕ блокирует отсутствие environment")
    # safe_apply.py существует
    sa = ROOT / "core" / "scripts" / "safe_apply.py" if IS_SOURCE_REPO else ROOT / "scripts" / "safe_apply.py"
    if sa.exists():
        rep.ok("safe_apply.py: существует (wrapper для опасных операций)")
    else:
        rep.error("safe_apply.py: отсутствует (8.5 — единый wrapper не найден)")


# ==================== ADVERSARIAL TESTS (8.11) ====================

def _run_guard(guard_path: Path, cfg: dict, specs_dir: Path, task: str, db: str, op: str) -> int:
    """Запустить guard с заданным конфигом и specs, вернуть exit code."""
    with tempfile.TemporaryDirectory(prefix="adv_guard_") as td:
        tdpath = Path(td)
        cfg_path = tdpath / "cfg.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        cmd = [sys.executable, str(guard_path), "--config", str(cfg_path),
               "--specs-dir", str(specs_dir)]
        if task:
            cmd += ["--task", task]
        cmd += ["--db", db, "--op", op]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode


def _make_spec_file(specs_dir: Path, task: str, **yaml_fields) -> Path:
    """Создать синтетический spec с заданным yaml-блоком."""
    task_dir = specs_dir / task
    task_dir.mkdir(parents=True, exist_ok=True)
    yaml_lines = []
    for k, v in yaml_fields.items():
        if v is None:
            yaml_lines.append(f"{k}: null")
        else:
            yaml_lines.append(f"{k}: {v}")
    yaml_block = "\n".join(yaml_lines)
    content = f"""# Solution Spec: {task}

```yaml
{yaml_block}
```

## Границы изменения
- тестовый scope

## Затрагиваемые файлы
- projects/test/src/test.bsl

## Критерии приёмки
- тест
"""
    spec_path = task_dir / "03_solution_spec.md"
    spec_path.write_text(content, encoding="utf-8")
    return spec_path


def _make_report_file(specs_dir: Path, task: str, scope_hash: str = "abc123") -> Path:
    """Создать синтетический 06_change_report.md (без project-путей, чтобы plan-files не блокировал)."""
    task_dir = specs_dir / task
    report = f"""# Отчёт об изменениях: {task}

```yaml
scope_hash: {scope_hash}
```

## Что сделано
Тестовая реализация
"""
    report_path = task_dir / "06_change_report.md"
    report_path.write_text(report, encoding="utf-8")
    return report_path


def _make_review_file(specs_dir: Path, task: str, verdict: str = "approved",
                      reviewed_by: str = "1c-reviewer", reviewed_at: str = "2026-01-01",
                      spec_version: str = "1", scope_hash: str = "abc123") -> Path:
    """Создать синтетический review.md."""
    task_dir = specs_dir / task
    review = f"""# Review: {task}

```yaml
verdict: {verdict}
reviewed_by: {reviewed_by}
reviewed_at: {reviewed_at}
spec_version: {spec_version}
scope_hash: {scope_hash}
```

## Сверка со спецификацией
ОК
"""
    review_path = task_dir / "review.md"
    review_path.write_text(review, encoding="utf-8")
    return review_path


def check_adversarial_guard(rep: Report) -> None:
    """8.11: негативные тесты guard — должны падать на сломанных данных."""
    g = ROOT / "core" / "scripts" / "applier_guard.py" if IS_SOURCE_REPO else ROOT / "scripts" / "applier_guard.py"
    if not g.exists():
        rep.error("adversarial: applier_guard.py не найден")
        return

    # Базовый валидный конфиг
    base_cfg = {
        "environment": "local",
        "v8path": "C:\\fake",
        "databases": [
            {"id": "local-demo", "type": "file", "path": ".\\base", "environment": "local",
             "username_env": "V8_USER", "password_env": "V8_PASS"},
        ],
    }

    # --- 8.1: direct-bypass — guard без --task для опасной op → exit 1 ---
    with tempfile.TemporaryDirectory(prefix="adv_") as td:
        tdpath = Path(td)
        cfg_path = tdpath / "cfg.json"
        cfg_path.write_text(json.dumps(base_cfg), encoding="utf-8")
        specs_dir = tdpath / "specs"
        specs_dir.mkdir()
        r = subprocess.run(
            [sys.executable, str(g), "--config", str(cfg_path), "--specs-dir", str(specs_dir),
             "--db", "local-demo", "--op", "update"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            rep.ok("adversarial 8.1: direct-bypass без --task заблокирован")
        else:
            rep.error("adversarial 8.1: direct-bypass НЕ заблокирован (guard вернул exit 0 без --task)")

    # --- 8.2: review-missing — status approved, нет review.md → exit 1 ---
    with tempfile.TemporaryDirectory(prefix="adv_") as td:
        tdpath = Path(td)
        cfg_path = tdpath / "cfg.json"
        cfg_path.write_text(json.dumps(base_cfg), encoding="utf-8")
        specs_dir = tdpath / "specs"
        _make_spec_file(specs_dir, "T2", status="approved", risk="low",
                        approved_by="user", approved_at="2026-01-01",
                        spec_version="1", scope_hash="abc123")
        _make_report_file(specs_dir, "T2", scope_hash="abc123")
        # НЕТ review.md
        r = _run_guard(g, base_cfg, specs_dir, "T2", "local-demo", "update")
        if r != 0:
            rep.ok("adversarial 8.2: review-missing заблокирован (review обязателен для всех ops)")
        else:
            rep.error("adversarial 8.2: review-missing НЕ заблокирован (review необязателен)")

    # --- 8.3a: formal-approval — approved_by задан, approved_at пуст → exit 1 ---
    with tempfile.TemporaryDirectory(prefix="adv_") as td:
        tdpath = Path(td)
        cfg_path = tdpath / "cfg.json"
        cfg_path.write_text(json.dumps(base_cfg), encoding="utf-8")
        specs_dir = tdpath / "specs"
        _make_spec_file(specs_dir, "T3a", status="approved", risk="low",
                        approved_by="user", approved_at="",  # пустой!
                        spec_version="1", scope_hash="abc123")
        _make_report_file(specs_dir, "T3a", scope_hash="abc123")
        _make_review_file(specs_dir, "T3a", scope_hash="abc123")
        r = _run_guard(g, base_cfg, specs_dir, "T3a", "local-demo", "update")
        if r != 0:
            rep.ok("adversarial 8.3a: пустой approved_at заблокирован")
        else:
            rep.error("adversarial 8.3a: пустой approved_at НЕ заблокирован (формальная проверка)")

    # --- 8.3b: self-approval high-risk — approved_by=1c-developer для high-risk → exit 1 ---
    with tempfile.TemporaryDirectory(prefix="adv_") as td:
        tdpath = Path(td)
        cfg_path = tdpath / "cfg.json"
        cfg_path.write_text(json.dumps(base_cfg), encoding="utf-8")
        specs_dir = tdpath / "specs"
        _make_spec_file(specs_dir, "T3b", status="approved", risk="high",
                        approved_by="1c-developer", approved_at="2026-01-01",
                        spec_version="1", scope_hash="abc123")
        _make_report_file(specs_dir, "T3b", scope_hash="abc123")
        _make_review_file(specs_dir, "T3b", scope_hash="abc123",
                          reviewed_by="1c-reviewer")
        r = _run_guard(g, base_cfg, specs_dir, "T3b", "local-demo", "update")
        if r != 0:
            rep.ok("adversarial 8.3b: self-approval (1c-developer для high-risk) заблокирован")
        else:
            rep.error("adversarial 8.3b: self-approval НЕ заблокирован")

    # --- 8.3c: scope-hash drift — spec scope_hash ≠ report → exit 1 ---
    with tempfile.TemporaryDirectory(prefix="adv_") as td:
        tdpath = Path(td)
        cfg_path = tdpath / "cfg.json"
        cfg_path.write_text(json.dumps(base_cfg), encoding="utf-8")
        specs_dir = tdpath / "specs"
        _make_spec_file(specs_dir, "T3c", status="approved", risk="low",
                        approved_by="user", approved_at="2026-01-01",
                        spec_version="1", scope_hash="hash_from_spec")
        _make_report_file(specs_dir, "T3c", scope_hash="DIFFERENT_hash")  # не совпадает!
        _make_review_file(specs_dir, "T3c", scope_hash="hash_from_spec")
        r = _run_guard(g, base_cfg, specs_dir, "T3c", "local-demo", "update")
        if r != 0:
            rep.ok("adversarial 8.3c: scope-hash drift (spec vs report) заблокирован")
        else:
            rep.error("adversarial 8.3c: scope-hash drift НЕ заблокирован")

    # --- 8.3d: review independence — reviewed_by == approved_by → exit 1 ---
    with tempfile.TemporaryDirectory(prefix="adv_") as td:
        tdpath = Path(td)
        cfg_path = tdpath / "cfg.json"
        cfg_path.write_text(json.dumps(base_cfg), encoding="utf-8")
        specs_dir = tdpath / "specs"
        _make_spec_file(specs_dir, "T3d", status="approved", risk="low",
                        approved_by="1c-reviewer", approved_at="2026-01-01",
                        spec_version="1", scope_hash="abc123")
        _make_report_file(specs_dir, "T3d", scope_hash="abc123")
        _make_review_file(specs_dir, "T3d", scope_hash="abc123",
                          reviewed_by="1c-reviewer")  # тот же!
        r = _run_guard(g, base_cfg, specs_dir, "T3d", "local-demo", "update")
        if r != 0:
            rep.ok("adversarial 8.3d: нарушение независимости (reviewed_by==approved_by) заблокировано")
        else:
            rep.error("adversarial 8.3d: нарушение независимости НЕ заблокировано")

    # --- 8.4: env per-db — глобальный local + серверная БД без per-db env → exit 1 ---
    server_cfg = {
        "environment": "local",  # глобальный
        "v8path": "C:\\fake",
        "databases": [
            {"id": "srv1", "type": "server", "server": "srv", "ref": "db",
             "username_env": "V8_USER", "password_env": "V8_PASS"},
            # НЕТ per-db environment, НЕТ allow_apply
        ],
    }
    with tempfile.TemporaryDirectory(prefix="adv_") as td:
        tdpath = Path(td)
        cfg_path = tdpath / "cfg.json"
        cfg_path.write_text(json.dumps(server_cfg), encoding="utf-8")
        specs_dir = tdpath / "specs"
        _make_spec_file(specs_dir, "T4", status="approved", risk="low",
                        approved_by="user", approved_at="2026-01-01",
                        spec_version="1", scope_hash="abc123")
        _make_report_file(specs_dir, "T4", scope_hash="abc123")
        _make_review_file(specs_dir, "T4", scope_hash="abc123")
        r = _run_guard(g, server_cfg, specs_dir, "T4", "srv1", "update")
        if r != 0:
            rep.ok("adversarial 8.4: серверная БД без per-db env+allow_apply заблокирована")
        else:
            rep.error("adversarial 8.4: серверная БД без per-db env НЕ заблокирована")

    # --- Позитивный кейс: всё корректно → exit 0 ---
    with tempfile.TemporaryDirectory(prefix="adv_") as td:
        tdpath = Path(td)
        cfg_path = tdpath / "cfg.json"
        cfg_path.write_text(json.dumps(base_cfg), encoding="utf-8")
        specs_dir = tdpath / "specs"
        _make_spec_file(specs_dir, "TOK", status="approved", risk="low",
                        approved_by="user", approved_at="2026-01-01",
                        spec_version="1", scope_hash="abc123")
        _make_report_file(specs_dir, "TOK", scope_hash="abc123")
        _make_review_file(specs_dir, "TOK", scope_hash="abc123",
                          reviewed_by="1c-reviewer", reviewed_at="2026-01-01")
        r = _run_guard(g, base_cfg, specs_dir, "TOK", "local-demo", "update")
        if r == 0:
            rep.ok("adversarial: позитивный кейс (всё корректно) → exit 0")
        else:
            rep.error(f"adversarial: позитивный кейс НЕ прошёл (exit {r}) — guard слишком строгий?")


def check_applier_self_path(rep: Report) -> None:
    """8.7: парсить frontmatter каждого агента, сверить self-path с путём установки."""
    if not IS_SOURCE_REPO:
        return  # только для исходного репо
    adapter_dirs = {
        "kilo": (".kilo/agent", "adapters/kilo/frontmatter"),
        "claude": (".claude/agents", "adapters/claude/frontmatter"),
        "openworks": (".openworks/agents", "adapters/openworks/frontmatter"),
    }
    for tool, (install_dir, fm_dir) in adapter_dirs.items():
        fm_path = ROOT / fm_dir
        if not fm_path.is_dir():
            continue
        for agent in EXPECTED_AGENTS:
            yml = fm_path / f"{agent}.yml"
            if not yml.exists():
                continue
            text = yml.read_text(encoding="utf-8", errors="replace")
            # Ищем self-path: install_dir/agent.md
            expected = f"{install_dir}/{agent}.md"
            # Проверяем, что нет путей с другим регистром/числом каталога
            wrong_patterns = []
            # Для openworks: .openworks/agent/ (единственное) — должно быть .openworks/agents/
            if tool == "openworks":
                if f".openworks/agent/{agent}.md" in text:
                    wrong_patterns.append(f".openworks/agent/{agent}.md (ожидалось .openworks/agents/)")
            # Для kilo: .kilo/agents/ (множественное) — должно быть .kilo/agent/
            if tool == "kilo":
                if f".kilo/agents/{agent}.md" in text:
                    wrong_patterns.append(f".kilo/agents/{agent}.md (ожидалось .kilo/agent/)")
            if wrong_patterns:
                rep.error(f"self-path {tool}/{agent}: {'; '.join(wrong_patterns)}")
            else:
                rep.ok(f"self-path {tool}/{agent}: корректен")


def check_kilo_readme_path(rep: Report) -> None:
    """8.8: README не должен содержать .kilo/agents (множественное)."""
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    text = readme.read_text(encoding="utf-8", errors="replace")
    # .kilo/agents (множественное) — ошибка; .kilo/agent (единственное) — ок
    matches = re.findall(r"\.kilo/agents[/\s\"']", text)
    if matches:
        rep.error(f"kilo-path 8.8: README содержит '.kilo/agents' (ожидалось '.kilo/agent') — {len(matches)} вхождений")
    else:
        rep.ok("kilo-path 8.8: README использует '.kilo/agent' (единственное)")


def check_do_rights(rep: Report) -> None:
    """8.6: 1c-do не должен иметь изменяющие skills (meta-*/form-*/cf-*/cfe-*) в bash/skill."""
    if not IS_SOURCE_REPO:
        return
    changing_skill_patterns = [
        "meta-*", "form-*", "cf-*", "cfe-*", "mxl-*", "skd-*", "role-*",
        "subsystem-*", "xdto-*", "interface-*", "template-*", "help-*",
        "support-*", "epf-", "erf-",
    ]
    for tool in ["kilo", "openworks"]:
        fm = ROOT / "adapters" / tool / "frontmatter" / "1c-do.yml"
        if not fm.exists():
            continue
        text = fm.read_text(encoding="utf-8", errors="replace")
        offenders = []
        for pat in changing_skill_patterns:
            # Ищем в bash и skill секциях
            if f'"{pat}"' in text or f'"{pat.replace("*", "")}' in text:
                # Проверяем, что это не в deny списке
                lines = text.splitlines()
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if pat in stripped and "allow" in stripped:
                        offenders.append(f"'{pat}' in line: {stripped[:80]}")
        if offenders:
            rep.error(f"do-rights 8.6 ({tool}): 1c-do имеет изменяющие skills: {'; '.join(offenders[:3])}")
        else:
            rep.ok(f"do-rights 8.6 ({tool}): 1c-do не имеет изменяющих skills в bash/skill")


def check_example_security(rep: Report) -> None:
    ex = ROOT / "examples" / "v8-project.example.json"
    if not ex.exists():
        return
    try:
        data = json.loads(ex.read_text(encoding="utf-8-sig"))
    except Exception as e:
        rep.error(f"example json не парсится: {e}")
        return
    # Проверяем per-db environment (8.4)
    dbs = data.get("databases") or []
    for d in dbs:
        db_env = str(d.get("environment", "")).strip()
        db_type = str(d.get("type", "")).strip().lower()
        if not db_env:
            rep.error(f"example: база {d.get('id')} без per-db environment (8.4)")
        elif db_env not in ALLOWED_ENVS and db_env != "production":
            rep.error(f"example: база {d.get('id')} environment='{db_env}' недопустимо")
        # Серверная база должна иметь allow_apply
        if db_type == "server":
            allow = d.get("allow_apply")
            if not (allow is True or str(allow).strip().lower() == "true"):
                rep.warn(f"example: серверная база {d.get('id')} без allow_apply: true (рекомендуется)")
        if "user" in d or "password" in d:
            rep.error(f"example: база {d.get('id')} содержит plaintext user/password")
    env = str(data.get("environment", "")).strip()
    if env in ("local", "test", "staging"):
        rep.ok(f"example: global environment={env}")
    raw = ex.read_text(encoding="utf-8-sig")
    if re.search(r'"password"\s*:\s*"', raw):
        rep.error("example: найден plaintext 'password' (не password_env)")


def check_no_opencode_refs(rep: Report) -> None:
    offenders = []
    search_paths = [ROOT / "README.md", ROOT / "docs", ROOT / "NOTICE.md",
                    ROOT / "install", ROOT / "adapters"]
    if IS_SOURCE_REPO:
        search_paths += [ROOT / "core" / "agents", ROOT / "core" / "context"]
    for p in search_paths:
        if not p.exists():
            continue
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            if re.search(r"OpenCode", text) or re.search(r"\.opencode[/\s\"]|opencode\.json|adapters[/\\]opencode", text):
                offenders.append(str(p.relative_to(ROOT)))
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and f.suffix in (".md", ".ps1", ".py", ".tpl", ".yml", ".json", ".toml"):
                    text = f.read_text(encoding="utf-8", errors="replace")
                    if re.search(r"OpenCode", text) or re.search(r"\.opencode[/\s\"]|opencode\.json|adapters[/\\]opencode", text):
                        offenders.append(str(f.relative_to(ROOT)))
    if offenders:
        rep.error("OpenCode-упоминания: " + ", ".join(offenders))
    else:
        rep.ok("no OpenCode references")


def check_claude_frontmatter_parse(rep: Report) -> None:
    """8.9: Claude .yml не должны содержать --- delimiters (install.ps1 добавляет их)."""
    if not IS_SOURCE_REPO:
        return
    fm_dir = ROOT / "adapters" / "claude" / "frontmatter"
    if not fm_dir.is_dir():
        return
    for yml in fm_dir.glob("*.yml"):
        text = yml.read_text(encoding="utf-8", errors="replace")
        stripped = text.strip()
        if stripped.startswith("---"):
            rep.error(f"claude-fm 8.9: {yml.name} содержит --- delimiter (install.ps1 добавит второй)")
        else:
            rep.ok(f"claude-fm 8.9: {yml.name} без --- (install.ps1 добавит корректно)")


def check_summaries_line_numbers(rep: Report) -> None:
    """Проверка: summaries (если есть) должны содержать номера строк для процедур/функций.
    Формат: `- имя (путь:L<номер>)`. Проверяет рабочую раскладку (.kilo/context/projects/...)."""
    import re as _re
    checked = 0
    for sdir in [ROOT / ".kilo" / "context" / "projects",
                 ROOT / ".claude" / "context" / "projects",
                 ROOT / ".openworks" / "context" / "projects",
                 ROOT / "context" / "projects"]:
        if not sdir.is_dir():
            continue
        for proj in sdir.iterdir():
            sm = proj / "summaries"
            if not sm.is_dir():
                continue
            for sf in sm.glob("*.md"):
                if sf.name.startswith("."):
                    continue
                text = sf.read_text(encoding="utf-8", errors="replace")
                # Проверяем секции «Процедуры» и «Функции»
                for section in ("Процедуры", "Функции"):
                    sec_re = _re.search(rf"## {section}\s*\n(.*?)(?=^##\s|\Z)", text, _re.S | _re.M)
                    if not sec_re:
                        continue
                    body = sec_re.group(1)
                    items = _re.findall(r"^- `([^`]+)`", body, _re.M)
                    if not items:
                        continue  # пустая секция — ок
                    checked += 1
                    # Каждый элемент должен содержать :L<номер> в той же строке
                    lines = body.splitlines()
                    without_line = []
                    for line in lines:
                        m = _re.match(r"^- `([^`]+)`", line)
                        if m and ":L" not in line:
                            without_line.append(m.group(1))
                    if without_line:
                        rep.error(f"summaries 1: {sf.name} [{section}]: без номера строки: {without_line[:3]}")
                    else:
                        rep.ok(f"summaries 1: {sf.name} [{section}]: номера строк присутствуют")
    if checked == 0:
        rep.ok("summaries 1: нет summaries для проверки (ожидаемо в исходном репо)")


def check_installer_smoke(rep: Report, skip_smoke: bool) -> None:
    if skip_smoke:
        rep.warn("smoke: пропущен (--skip-smoke)")
        return
    # install.ps1 существует только в исходном репо, не в установленной раскладке
    install_ps1 = ROOT / "install" / "install.ps1"
    if not install_ps1.exists():
        rep.ok("smoke: пропущен (install.ps1 недоступен — установленная раскладка)")
        return
    pwsh = shutil.which("powershell") or shutil.which("pwsh")
    if not pwsh:
        # 8.11: отсутствие PowerShell = ERROR, а не warn+OK
        rep.error("smoke: PowerShell не найден — smoke-тест пропущен, но успех не объявляется (8.11)")
        return
    for tool in ADAPTERS:
        with tempfile.TemporaryDirectory(prefix=f"install_smoke_{tool}_") as td:
            r = subprocess.run(
                [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(ROOT / "install" / "install.ps1"), "-Tool", tool, "-Target", td],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
            )
            if r.returncode != 0:
                rep.error(f"smoke install -Tool {tool}: exit={r.returncode}; stderr={r.stderr[:500]}")
                continue
            tdp = Path(td)
            issues = []
            if tool == "kilo":
                reviewer = tdp / ".kilo" / "agent" / "1c-reviewer.md"
                if not reviewer.exists():
                    issues.append("нет .kilo/agent/1c-reviewer.md")
                else:
                    content = reviewer.read_text(encoding="utf-8-sig", errors="replace")
                    if not _parse_frontmatter(content):
                        issues.append("frontmatter .kilo/agent/1c-reviewer.md пустой/двойной (8.9)")
            elif tool == "claude":
                reviewer = tdp / ".claude" / "agents" / "1c-reviewer.md"
                if not reviewer.exists():
                    issues.append("нет .claude/agents/1c-reviewer.md")
                else:
                    content = reviewer.read_text(encoding="utf-8-sig", errors="replace")
                    if not _parse_frontmatter(content):
                        issues.append("frontmatter .claude/agents/1c-reviewer.md пустой/двойной (8.9)")
            elif tool == "openworks":
                reviewer = tdp / ".openworks" / "agents" / "1c-reviewer.md"
                if not reviewer.exists():
                    issues.append("нет .openworks/agents/1c-reviewer.md")
                else:
                    content = reviewer.read_text(encoding="utf-8-sig", errors="replace")
                    if not _parse_frontmatter(content):
                        issues.append("frontmatter .openworks/agents/1c-reviewer.md пустой/двойной (8.9)")
            elif tool == "codex":
                if not (tdp / "agents" / "1c-reviewer.md").exists():
                    issues.append("нет agents/1c-reviewer.md")
            if issues:
                rep.error(f"smoke {tool}: " + "; ".join(issues))
            else:
                rep.ok(f"smoke install -Tool {tool}: OK (frontmatter проверен)")


def _parse_frontmatter(content: str) -> dict:
    """Простой парсер Markdown frontmatter. Возвращает dict если валиден, {} если нет."""
    # Удалить BOM (install.ps1 пишет файлы с UTF-8 BOM)
    content = content.lstrip('\ufeff')
    if not content.startswith("---"):
        return {}
    lines = content.split("\n")
    # Первая строка: ---
    # Ищем закрывающий ---
    fm_lines = []
    found_close = False
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            found_close = True
            break
        fm_lines.append(line)
    if not found_close:
        return {}
    if not fm_lines:
        return {}  # пустой frontmatter (двойной ---)
    result = {}
    for line in fm_lines:
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
    return result


def check_rules_directory(rep: Report) -> None:
    """Проверка: core/rules/ существует и содержит ожидаемые файлы."""
    if IS_SOURCE_REPO:
        rules_dir = ROOT / "core" / "rules"
    else:
        # Установленная раскладка: ищем rules/ в context dirs
        rules_dir = None
        for d in [".kilo/context/rules", ".claude/context/rules", ".openworks/context/rules", "context/rules"]:
            rp = ROOT / d
            if rp.is_dir():
                rules_dir = rp
                break
    if rules_dir is None or not rules_dir.is_dir():
        rep.error("rules: каталог rules/ не найден (core/rules/ или {{CONTEXT_DIR}}/rules/)")
        return
    for rule in EXPECTED_RULES:
        rp = rules_dir / rule
        if rp.exists() and rp.stat().st_size > 0:
            rep.ok(f"rules: {rule}")
        else:
            rep.error(f"rules: отсутствует/пуст: {rule}")


def check_agent_trigger_tables(rep: Report) -> None:
    """Проверка: тела агентов (кроме 1c-tools) содержат секцию 'On-demand правила'."""
    if IS_SOURCE_REPO:
        agents_dir = ROOT / "core" / "agents"
    else:
        agents_dir = None
        for d in [".kilo/agent", ".claude/agents", ".openworks/agents", "agents"]:
            ap = ROOT / d
            if ap.is_dir():
                agents_dir = ap
                break
    if agents_dir is None or not agents_dir.is_dir():
        return
    for agent in EXPECTED_AGENTS:
        p = agents_dir / f"{agent}.md"
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if agent == "1c-tools":
            if "On-demand правила" in text:
                rep.warn(f"trigger-table: {agent} содержит on-demand секцию (ожидалось отсутствие)")
            else:
                rep.ok(f"trigger-table: {agent} без on-demand (корректно)")
        else:
            if "On-demand правила" in text:
                rep.ok(f"trigger-table: {agent} имеет секцию on-demand")
            else:
                rep.error(f"trigger-table: {agent} не имеет секции 'On-demand правила'")


def check_triage(rep: Report) -> None:
    """Проверка: triage.md существует и содержит ключевые слова; 1c-do routing содержит triage-уровни."""
    triage_found = False
    for d in ["core/rules", ".kilo/context/rules", ".claude/context/rules", ".openworks/context/rules", "context/rules"]:
        tp = ROOT / d / "triage.md"
        if tp.exists():
            triage_found = True
            text = tp.read_text(encoding="utf-8", errors="replace")
            for kw in ["quick-fix", "promotion", "QUICKFIX_MAX_LINES", "docs-fix"]:
                if kw not in text:
                    rep.error(f"triage: triage.md не содержит '{kw}'")
            else:
                rep.ok("triage: triage.md содержит ключевые слова")
            break
    if not triage_found:
        rep.error("triage: triage.md не найден")
    # 1c-do routing table
    do_path = None
    for d in ["core/agents", ".kilo/agent", ".claude/agents", ".openworks/agents", "agents"]:
        dp = ROOT / d / "1c-do.md"
        if dp.exists():
            do_path = dp
            break
    if do_path:
        text = do_path.read_text(encoding="utf-8", errors="replace")
        for kw in ["docs-fix", "quick-fix"]:
            if kw in text:
                rep.ok(f"triage: 1c-do.md содержит '{kw}'")
            else:
                rep.error(f"triage: 1c-do.md не содержит '{kw}'")


def check_dev_env(rep: Report) -> None:
    """Проверка: .dev.env.example (source) или .dev.env (installed) существует."""
    if IS_SOURCE_REPO:
        env_ex = ROOT / "core" / "context" / ".dev.env.example"
        if env_ex.exists() and env_ex.stat().st_size > 0:
            rep.ok("dev-env: core/context/.dev.env.example существует")
            text = env_ex.read_text(encoding="utf-8", errors="replace")
            for kw in ["PREFIX=", "COMPANY=", "PLATFORM_VERSION=", "PLATFORM_PATH=", "QUICKFIX_MAX_LINES="]:
                if kw in text:
                    rep.ok(f"dev-env: .dev.env.example содержит '{kw}'")
                else:
                    rep.error(f"dev-env: .dev.env.example не содержит '{kw}'")
        else:
            rep.error("dev-env: core/context/.dev.env.example отсутствует/пуст")
    else:
        env_path = ROOT / ".dev.env"
        if env_path.exists():
            rep.ok("dev-env: .dev.env найден (установленная раскладка)")
        else:
            rep.warn("dev-env: .dev.env не найден (создаётся install.ps1)")


def check_manifest(rep: Report) -> None:
    """Проверка: .ai-rules.json (если существует) имеет корректную структуру."""
    manifest_path = ROOT / ".ai-rules.json"
    if not manifest_path.exists():
        rep.ok("manifest: .ai-rules.json не найден (создаётся install.ps1 — норма для source repo)")
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig", errors="replace"))
        if "protocolVersion" not in manifest:
            rep.error("manifest: .ai-rules.json без 'protocolVersion'")
        else:
            rep.ok(f"manifest: protocolVersion={manifest['protocolVersion']}")
        files = manifest.get("files") or []
        if not files:
            rep.warn("manifest: .ai-rules.json с пустым списком files")
        else:
            missing = []
            for f in files:
                fp = f.get("path", "")
                if fp and not (ROOT / fp).exists():
                    missing.append(fp)
            if missing:
                rep.error(f"manifest: файлы из манифеста отсутствуют: {missing[:5]}")
            else:
                rep.ok(f"manifest: {len(files)} файлов, все существуют")
    except Exception as e:
        rep.error(f"manifest: .ai-rules.json не читается: {e}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="validate.py", description="Единая локальная проверка агентской схемы 1С Dev.")
    parser.add_argument("--skip-smoke", action="store_true", help="Пропустить smoke-тест установщика")
    args = parser.parse_args()

    rep = Report()
    print(f"=== validate: ROOT={ROOT} (mode: {'source' if IS_SOURCE_REPO else 'installed'}) ===")
    check_python_syntax(rep)
    check_required_files(rep)
    check_md_links(rep)
    check_install_paths(rep)
    check_agents(rep)
    check_reviewer_in_adapters(rep)
    check_sdd_risk_gates(rep)
    check_applier_guards(rep)
    check_example_security(rep)
    check_no_opencode_refs(rep)
    # Adversarial tests (8.11)
    check_adversarial_guard(rep)
    check_applier_self_path(rep)
    check_kilo_readme_path(rep)
    check_do_rights(rep)
    check_claude_frontmatter_parse(rep)
    check_summaries_line_numbers(rep)
    # Phase 1 checks
    check_rules_directory(rep)
    check_agent_trigger_tables(rep)
    check_triage(rep)
    check_dev_env(rep)
    check_manifest(rep)
    check_installer_smoke(rep, args.skip_smoke)

    print("\n=== VALIDATION REPORT ===")
    for o in rep.oks:
        print(f"OK   {o}")
    for w in rep.warnings:
        print(f"WARN {w}")
    for e in rep.errors:
        print(f"FAIL {e}")
    print(f"\nИтог: OK={len(rep.oks)}, WARN={len(rep.warnings)}, FAIL={len(rep.errors)}")
    if rep.errors:
        print("РЕЗУЛЬТАТ: НЕ ПРОЙДЕНО (есть ERROR)")
        return 1
    print("РЕЗУЛЬТАТ: ПРОЙДЕНО")
    return 0


if __name__ == "__main__":
    import traceback as _tb
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        raise SystemExit(130)
    except Exception:
        _tb.print_exc()
        raise SystemExit(1)
