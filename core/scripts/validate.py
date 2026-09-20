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
from datetime import datetime, timezone
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
    "LICENSE",
    "AGENT-INSTALL.md",
    "examples/v8-project.example.json",
    "examples/project-context.example.md",
    "core/sdd/README.md",
    "core/scripts/applier_guard.py",
    "core/scripts/safe_apply.py",
    "core/scripts/safe_backup.py",
    "core/scripts/scope_hash.py",
    "core/scripts/plan_parser.py",
    "core/scripts/test_plan_parser.py",
    "core/scripts/bsl-check.py",
    "core/scripts/build_summaries.py",
    "core/scripts/validate.py",
    "core/scripts/doctor.py",
    "core/scripts/_root.py",
    "install/install.ps1",
    "core/context/.dev.env.example",
    ".gitlab-ci.yml",
    ".github/workflows/ci.yml",
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
    "project-sources.md",
]

REQUIRED_FILES_INSTALLED = [
    "examples/v8-project.example.json",
    "specs/README.md",
    "scripts/applier_guard.py",
    "scripts/safe_apply.py",
    "scripts/safe_backup.py",
    "scripts/scope_hash.py",
    "scripts/plan_parser.py",
    "scripts/test_plan_parser.py",
    "scripts/bsl-check.py",
    "scripts/build_summaries.py",
    "scripts/validate.py",
    "scripts/doctor.py",
    "scripts/_root.py",
    "scripts/test_mock_apply.py",
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
                        ("openworks", ".opencode/skills"), ("codex", "skills")]:
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
    if "opencode" in text and ".opencode" not in text:
        rep.error("install.ps1: содержит 'opencode' без .opencode/ (некорректное использование)")
    else:
        rep.ok("install.ps1: opencode usage (только .opencode/ пути для openworks)")
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
        "openworks": ".opencode/agents",
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

    # scope_hash.py CLI существует и работает
    sh = ROOT / "core" / "scripts" / "scope_hash.py" if IS_SOURCE_REPO else ROOT / "scripts" / "scope_hash.py"
    if sh.exists():
        r = subprocess.run([sys.executable, str(sh), "--help"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        if r.returncode == 0 and "--spec" in r.stdout:
            rep.ok("scope_hash.py: CLI --help работает")
        else:
            rep.error(f"scope_hash.py: CLI не работает (exit={r.returncode})")
    else:
        rep.error("scope_hash.py: отсутствует")


def check_kilo_tools_field(rep: Report) -> None:
    """Проверка: Kilo frontmatter НЕ содержит tools: field (не в схеме Kilo — вызывает ошибку)."""
    if not IS_SOURCE_REPO:
        return
    fm_dir = ROOT / "adapters" / "kilo" / "frontmatter"
    if not fm_dir.is_dir():
        return
    for agent in EXPECTED_AGENTS:
        yml = fm_dir / f"{agent}.yml"
        if not yml.exists():
            continue
        text = yml.read_text(encoding="utf-8", errors="replace")
        if "tools:" in text:
            rep.error(f"kilo-tools: {agent}.yml содержит tools: — Kilo schema не поддерживает это поле (Expected object | undefined)")
        else:
            rep.ok(f"kilo-tools: {agent}.yml без tools: (корректно для Kilo schema)")


def check_kilo_permission_order(rep: Report) -> None:
    """Проверка: в Kilo frontmatter общий deny ("*": deny) должен предшествовать
    специфичным allow в каждой карте permissions (bash, skill, edit, mcp).
    Правило Kilo: последнее совпавшее правило определяет результат.
    Если deny стоит после allow, он перекрывает исключения."""
    if not IS_SOURCE_REPO:
        return
    fm_dir = ROOT / "adapters" / "kilo" / "frontmatter"
    if not fm_dir.is_dir():
        return
    import re as _re

    # Простая модель сопоставления glob-паттернов для тестирования
    def glob_match(pattern: str, value: str) -> bool:
        """Простое сопоставление glob: * = любой символ, ? = один символ."""
        # Конвертируем glob в regex
        regex = _re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
        return bool(_re.match(f"^{regex}$", value))

    def evaluate_permission(rules: list[tuple[str, str]], command: str) -> str:
        """Вычислить результат permission по правилам Kilo: последнее совпадение побеждает.
        rules: [(pattern, action)] в порядке объявления.
        Возвращает: 'allow', 'deny', или 'default' если нет совпадений."""
        result = "default"
        for pattern, action in rules:
            if glob_match(pattern, command):
                result = action
        return result

    # Тестовые команды для каждой роли
    test_cases = {
        "1c-applier": [
            ("bash", "python scripts/safe_apply.py --task TASK-1 --db test", "allow"),
            ("bash", "python scripts/safe_backup.py --task TASK-1 --db test", "allow"),
            ("bash", "python scripts/applier_guard.py --help", "allow"),
            ("bash", "python scripts/bsl-check.py file.bsl", "allow"),
            ("bash", "python scripts/evil_script.py", "ask"),
            ("bash", "powershell.exe -NoProfile -File .kilo/skills/db-list/scripts/db-list.ps1", "allow"),
            ("bash", "powershell.exe -NoProfile -File .kilo/skills/db-load-xml/scripts/db-load-xml.ps1", "ask"),
            ("edit", ".v8-project.json", "deny"),
            ("edit", "pilot-control/TASK-1/review.md", "deny"),
            ("skill", "db-list", "allow"),
            ("skill", "db-load-xml", "deny"),
            ("skill", "meta-info", "deny"),
        ],
        "1c-reviewer": [
            ("bash", "python scripts/scope_hash.py --spec file.md", "allow"),
            ("bash", "python scripts/bsl-check.py file.bsl", "allow"),
            ("bash", "python scripts/safe_apply.py --help", "deny"),
            ("bash", "python scripts/evil_script.py", "deny"),
            ("edit", "projects/test/src/file.bsl", "deny"),
            ("edit", "pilot-control/TASK-1/review.md", "deny"),
            ("edit", ".kilo/logs/1c-reviewer/log.md", "allow"),
            ("edit", "specs/TASK-1/03_solution_spec.md", "deny"),
            ("mcp", "v8std_search", "allow"),
            ("mcp", "other_tool", "deny"),
            ("skill", "meta-info", "deny"),
        ],
        "1c-do": [
            ("bash", "python scripts/build_summaries.py --project test", "allow"),
            ("bash", "python scripts/safe_apply.py --help", "deny"),
            ("bash", "python scripts/evil_script.py", "deny"),
            ("edit", "specs/TASK-1/00_request.md", "allow"),
            ("edit", "pilot-control/TASK-1/review.md", "allow"),
            ("edit", "projects/test/src/file.bsl", "deny"),
            ("edit", ".v8-project.json", "deny"),
            ("edit", "INSTRUCTIONS.md", "deny"),
            ("skill", "meta-info", "deny"),
            ("mcp", "v8std_search", "allow"),
            ("mcp", "other_tool", "deny"),
            ("task", "", "allow"),
        ],
        "1c-developer": [
            ("mcp", "v8std_search", "allow"),
            ("mcp", "other_tool", "deny"),
            ("bash", "python scripts/bsl-check.py file.bsl", "allow"),
            ("bash", "python scripts/safe_apply.py --help", "default"),
            ("edit", "projects/test/src/file.bsl", "allow"),
            ("edit", ".v8-project.json", "default"),
        ],
        "1c-analyst": [
            ("bash", "python scripts/scope_hash.py --spec file.md", "allow"),
            ("bash", "python scripts/safe_apply.py --help", "default"),
            ("mcp", "anything", "deny"),
            ("edit", "specs/TASK-1/03_solution_spec.md", "allow"),
            ("edit", "projects/test/src/file.bsl", "deny"),
            ("skill", "meta-info", "allow"),
            ("skill", "db-list", "default"),
        ],
        "1c-tools": [
            ("bash", "python scripts/build_summaries.py --project test", "allow"),
            ("bash", "python scripts/build_summaries.py --project test --context-dir .opencode/context/projects", "allow"),
            ("bash", "python scripts/safe_apply.py --help", "deny"),
            ("skill", "meta-info", "deny"),
            ("mcp", "anything", "deny"),
            ("edit", "projects/test/src/file.bsl", "deny"),
        ],
    }

    for agent, cases in test_cases.items():
        yml_path = fm_dir / f"{agent}.yml"
        if not yml_path.exists():
            continue
        text = yml_path.read_text(encoding="utf-8", errors="replace")

        # Парсим permission блоки (простой парсер)
        perm_maps: dict[str, list[tuple[str, str]]] = {}
        current_map = None
        for line in text.splitlines():
            stripped = line.strip()
            # Заголовок карты (read:, bash:, edit:, skill:, mcp:, task:)
            m = _re.match(r"^(\w+):\s*$", stripped)
            if m and m.group(1) in ("read", "glob", "grep", "list", "edit", "bash", "skill", "mcp", "task"):
                current_map = m.group(1)
                perm_maps[current_map] = []
                continue
            # Правило: "pattern": action
            if current_map:
                m2 = _re.match(r'^"([^"]+)":\s*(allow|deny|ask)$', stripped)
                if m2:
                    perm_maps[current_map].append((m2.group(1), m2.group(2)))

        for perm_type, command, expected in cases:
            if perm_type not in perm_maps:
                continue
            result = evaluate_permission(perm_maps[perm_type], command)
            if result == expected:
                rep.ok(f"perm-order: {agent}/{perm_type} '{command[:40]}' → {result}")
            else:
                rep.error(f"perm-order: {agent}/{perm_type} '{command[:40]}' → {result} (ожидался {expected})")


def check_scope_hash_permissions(rep: Report) -> None:
    """Проверка: analyst и reviewer имеют scope_hash.py в bash whitelist."""
    if not IS_SOURCE_REPO:
        return
    for tool in ("kilo", "openworks"):
        fm_dir = ROOT / "adapters" / tool / "frontmatter"
        if not fm_dir.is_dir():
            continue
        for agent in ("1c-analyst", "1c-reviewer"):
            yml = fm_dir / f"{agent}.yml"
            if not yml.exists():
                continue
            text = yml.read_text(encoding="utf-8", errors="replace")
            if "scope_hash.py" in text:
                rep.ok(f"scope-hash-perm: {tool}/{agent}.yml разрешает scope_hash.py")
            else:
                rep.error(f"scope-hash-perm: {tool}/{agent}.yml НЕ разрешает scope_hash.py")


def check_bsl_comment_regression(rep: Report) -> None:
    """Проверка: bsl-check.py не ложно срабатывает на комментариях с ключевыми словами."""
    bsl_check = ROOT / "core" / "scripts" / "bsl-check.py" if IS_SOURCE_REPO else ROOT / "scripts" / "bsl-check.py"
    if not bsl_check.exists():
        rep.error("bsl-comment: bsl-check.py не найден")
        return
    # Создать тестовый BSL с "Цикл" в комментарии (сбалансированный)
    test_bsl_ok = """\ufeff// Тест: Цикл в комментарии
\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u0430 \u0422\u0435\u0441\u0442()\r
\r
\t// \u042d\u0442\u043e \u0426\u0438\u043a\u043b \u043f\u043e \u044d\u043b\u0435\u043c\u0435\u043d\u0442\u0430\u043c\r
\t\u0414\u043b\u044f \u041a\u0430\u0436\u0434\u043e\u0433\u043e \u042d\u043b\u0435\u043c\u0435\u043d\u0442 \u0418\u0437 \u041a\u043e\u043b\u043b\u0435\u043a\u0446\u0438\u0438 \u0426\u0438\u043a\u043b\r
\t\t\u0421\u043e\u043e\u0431\u0449\u0438\u0442\u044c(\u042d\u043b\u0435\u043c\u0435\u043d\u0442);\r
\t\u041a\u043e\u043d\u0435\u0446\u0426\u0438\u043a\u043b\u0430;\r
\t// \u041a\u043e\u043d\u0435\u0446\u0426\u0438\u043a\u043b\u0430 \u0432 \u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0438\r
\u041a\u043e\u043d\u0435\u0446\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u044b\r
"""
    # Создать тестовый BSL с несбалансированным циклом
    test_bsl_bad = """\ufeff// Тест: несбалансированный цикл
\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u0430 \u0422\u0435\u0441\u0442()\r
\r
\t\u0414\u043b\u044f \u041a\u0430\u0436\u0434\u043e\u0433\u043e \u042d\u043b\u0435\u043c\u0435\u043d\u0442 \u0418\u0437 \u041a\u043e\u043b\u043b\u0435\u043a\u0446\u0438\u0438 \u0426\u0438\u043a\u043b\r
\t\t\u0421\u043e\u043e\u0431\u0449\u0438\u0442\u044c(\u042d\u043b\u0435\u043c\u0435\u043d\u0442);\r
\t// \u041d\u0435\u0445\u0432\u0430\u0442\u0430\u0435\u0442 \u041a\u043e\u043d\u0435\u0446\u0426\u0438\u043a\u043b\u0430\r
\u041a\u043e\u043d\u0435\u0446\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u044b\r
"""
    with tempfile.TemporaryDirectory(prefix="bsl_reg_") as td:
        tdpath = Path(td)
        ok_file = tdpath / "test_ok.bsl"
        ok_file.write_text(test_bsl_ok, encoding="utf-8")
        bad_file = tdpath / "test_bad.bsl"
        bad_file.write_text(test_bsl_bad, encoding="utf-8")

        # Положительный: 0 ERROR (исключаем итоговую строку с ERROR=0)
        r_ok = subprocess.run([sys.executable, str(bsl_check), str(ok_file)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        ok_error_lines = [l for l in r_ok.stdout.splitlines() if l.startswith("ERROR ")]
        if r_ok.returncode == 0 and not ok_error_lines:
            rep.ok("bsl-comment: комментарий с Цикл не вызывает ложный ERROR")
        else:
            rep.error(f"bsl-comment: ложное срабатывание на комментарии с Цикл (exit={r_ok.returncode}, errors={ok_error_lines})")

        # Негативный: ERROR присутствует (исключаем итоговую строку)
        r_bad = subprocess.run([sys.executable, str(bsl_check), str(bad_file)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        bad_error_lines = [l for l in r_bad.stdout.splitlines() if l.startswith("ERROR ")]
        if r_bad.returncode != 0 and bad_error_lines:
            rep.ok("bsl-comment: несбалансированный цикл детектируется (ERROR)")
        else:
            rep.error(f"bsl-comment: несбалансированный цикл НЕ детектируется (exit={r_bad.returncode})")


def check_plan_parser(rep: Report) -> None:
    """Запуск регрессионных тестов парсера apply-плана."""
    test_path = ROOT / "core" / "scripts" / "test_plan_parser.py" if IS_SOURCE_REPO else ROOT / "scripts" / "test_plan_parser.py"
    if not test_path.exists():
        rep.error("plan-parser: test_plan_parser.py не найден")
        return
    r = subprocess.run([sys.executable, str(test_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if r.returncode == 0:
        rep.ok("plan-parser: test_plan_parser.py → exit 0")
    else:
        rep.error(f"plan-parser: test_plan_parser.py → exit {r.returncode}")


# ==================== ADVERSARIAL TESTS ====================

try:
    sys.path.insert(0, str(ROOT / "core" / "scripts") if IS_SOURCE_REPO else str(ROOT / "scripts"))
    from scope_hash import compute_scope_hash
except ImportError:
    compute_scope_hash = None

import hashlib as _hashlib

# Canary secrets для проверки redaction
CANARY_SECRET = "TEST_SECRET_MUST_NOT_APPEAR"
CANARY_USERNAME = "TEST_USERNAME_MUST_NOT_APPEAR"


def _run_guard(guard_path: Path, project_root: Path, cfg: dict,
               specs_dir: Path, control_dir: Path,
               task: str, db: str, op: str, mode: str = "Partial",
               files: str = "") -> int:
    """Запустить guard с явным project-root и control-dir."""
    with tempfile.TemporaryDirectory(prefix="adv_guard_") as td:
        cfg_path = Path(td) / "cfg.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        cmd = [sys.executable, str(guard_path), "--config", str(cfg_path),
               "--specs-dir", str(specs_dir), "--control-dir", str(control_dir),
               "--project-root", str(project_root), "--mode", mode]
        if task:
            cmd += ["--task", task]
        if files:
            cmd += ["--files", files]
        cmd += ["--db", db, "--op", op]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode


def _make_spec_file(specs_dir: Path, task: str, **yaml_fields) -> str:
    task_dir = specs_dir / task
    task_dir.mkdir(parents=True, exist_ok=True)
    # Вычислить hash из секций БЕЗ yaml-блока (scope_hash исключается из hash)
    # Сначала создаём контент БЕЗ scope_hash в yaml
    yaml_lines = []
    for k, v in yaml_fields.items():
        if v is None:
            yaml_lines.append(f"{k}: null")
        else:
            yaml_lines.append(f"{k}: {v}")
    yaml_block_no_hash = "\n".join(yaml_lines)
    content_no_hash = f"""# Solution Spec: {task}

```yaml
{yaml_block_no_hash}
```

## Границы изменения
- тестовый scope A
- тестовый scope B

## Затрагиваемые файлы
- projects/test/src/test.bsl

## Критерии приёмки
- тест
"""
    # Вычислить hash из контента (scope_hash.py исключает yaml-блок)
    if compute_scope_hash:
        h = compute_scope_hash(content_no_hash)
    else:
        h = "abc123"
    # Теперь записать spec с scope_hash в yaml-блоке
    if "scope_hash" not in yaml_fields:
        yaml_fields["scope_hash"] = h
    yaml_lines2 = []
    for k, v in yaml_fields.items():
        if v is None:
            yaml_lines2.append(f"{k}: null")
        else:
            yaml_lines2.append(f"{k}: {v}")
    yaml_block2 = "\n".join(yaml_lines2)
    content = f"""# Solution Spec: {task}

```yaml
{yaml_block2}
```

## Границы изменения
- тестовый scope A
- тестовый scope B

## Затрагиваемые файлы
- projects/test/src/test.bsl

## Критерии приёмки
- тест
"""
    (task_dir / "03_solution_spec.md").write_text(content, encoding="utf-8")
    return h


def _make_report_file(specs_dir: Path, task: str, scope_hash: str, spec_version: str = "1") -> Path:
    task_dir = specs_dir / task
    report = f"""# Отчёт об изменениях: {task}

```yaml
scope_hash: {scope_hash}
spec_version: {spec_version}
```

## Изменённые файлы
- projects/test/src/test.bsl

## Что сделано
Тестовая реализация
"""
    report_path = task_dir / "06_change_report.md"
    report_path.write_text(report, encoding="utf-8")
    return report_path


def _make_review_file(control_dir: Path, task: str, scope_hash: str,
                      spec_version: str = "1", verdict: str = "approved",
                      reviewed_by: str = "1c-reviewer",
                      reviewed_at: str = "2026-01-01T12:00:00+00:00") -> Path:
    task_dir = control_dir / task
    task_dir.mkdir(parents=True, exist_ok=True)
    review = f"""# Review: {task}

```yaml
verdict: {verdict}
reviewed_by: {reviewed_by}
reviewed_at: {reviewed_at}
spec_version: {spec_version}
scope_hash: {scope_hash}
```
"""
    review_path = task_dir / "review.md"
    review_path.write_text(review, encoding="utf-8")
    return review_path


def _make_backup_file(control_dir: Path, task: str, db_id: str, env: str,
                      project_root: Path, artifact_rel: str = "backups/test.dt",
                      status: str = "success",
                      created_at: str = "2026-01-01T12:00:00+00:00") -> Path:
    """Создать backup.md с artifact_sha256."""
    task_dir = control_dir / task
    task_dir.mkdir(parents=True, exist_ok=True)
    # Создать artifact файл
    artifact_path = project_root / artifact_rel
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_content = b"fake backup content for testing"
    artifact_path.write_bytes(artifact_content)
    artifact_sha = _hashlib.sha256(artifact_content).hexdigest()
    backup = f"""# Backup: {task}

```yaml
backup_version: 1
database_id: {db_id}
environment: {env}
created_at: {created_at}
artifact: {artifact_rel}
artifact_sha256: {artifact_sha}
status: {status}
```
"""
    backup_path = task_dir / "backup.md"
    backup_path.write_text(backup, encoding="utf-8")
    return backup_path


def _setup_positive_env(tdpath: Path, task: str = "TOK") -> tuple:
    """Создать полное окружение для позитивного теста. Возвращает (specs_dir, control_dir, hash, cfg)."""
    specs_dir = tdpath / "specs"
    control_dir = tdpath / "pilot-control"
    # Создать план-файл
    proj_dir = tdpath / "projects" / "test" / "src"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "test.bsl").write_text("// test\n", encoding="utf-8")
    # Создать spec с реальным hash
    h = _make_spec_file(specs_dir, task, status="approved", risk="low",
                        approved_by="user", approved_at="2026-01-01T12:00:00+00:00",
                        spec_version="1")
    _make_report_file(specs_dir, task, h, "1")
    _make_review_file(control_dir, task, h, "1", reviewed_by="1c-reviewer")
    _make_backup_file(control_dir, task, "local-demo", "local", tdpath)
    cfg = {
        "environment": "local", "v8path": "C:\\fake",
        "databases": [{"id": "local-demo", "type": "file", "path": ".\\base",
                       "environment": "local",
                       "username_env": "V8_USER", "password_env": "V8_PASS"}],
    }
    return specs_dir, control_dir, h, cfg


def check_adversarial_guard(rep: Report) -> None:
    """Все негативные + позитивные тесты guard."""
    g = ROOT / "core" / "scripts" / "applier_guard.py" if IS_SOURCE_REPO else ROOT / "scripts" / "applier_guard.py"
    if not g.exists():
        rep.error("adversarial: applier_guard.py не найден")
        return

    base_cfg = {
        "environment": "local", "v8path": "C:\\fake",
        "databases": [{"id": "local-demo", "type": "file", "path": ".\\base",
                       "environment": "local",
                       "username_env": "V8_USER", "password_mode": "none"}],
    }

    def _test(name: str, expected_fail: bool, func) -> None:
        with tempfile.TemporaryDirectory(prefix="adv_") as td:
            tdpath = Path(td)
            specs_dir = tdpath / "specs"
            control_dir = tdpath / "pilot-control"
            rc = func(tdpath, specs_dir, control_dir)
            if expected_fail:
                rep.ok(f"adversarial: {name} заблокирован") if rc != 0 else rep.error(f"adversarial: {name} НЕ заблокирован")
            else:
                rep.ok(f"adversarial: {name} → exit 0") if rc == 0 else rep.error(f"adversarial: {name} НЕ прошёл (exit {rc})")

    def _setup_and_guard(tdpath, specs_dir, control_dir, spec_kwargs, report_hash=None,
                         review=True, backup=True, op="update", mode="Partial", files="",
                         cfg=None, task_id="TASK-T"):
        """Создать SDD окружение и запустить guard. Возвращает exit code."""
        cfg = cfg or base_cfg
        # Создать план-файл (projects/test/src/test.bsl) относительно tdpath
        proj_dir = tdpath / "projects" / "test" / "src"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "test.bsl").write_text("// test\n", encoding="utf-8")
        h = _make_spec_file(specs_dir, task_id, **spec_kwargs)
        sv = spec_kwargs.get("spec_version", "1")
        _make_report_file(specs_dir, task_id, report_hash or h, sv)
        if review:
            _make_review_file(control_dir, task_id, h, sv)
        if backup:
            # Использовать свежий timestamp (текущее время)
            now_ts = datetime.now(timezone.utc).isoformat()
            _make_backup_file(control_dir, task_id, "local-demo", "local", tdpath, created_at=now_ts)
        return _run_guard(g, tdpath, cfg, specs_dir, control_dir, task_id, "local-demo", op, mode, files)

    # --- Негативные тесты ---

    _test("direct-bypass без --task", True,
          lambda td, sd, cd: _run_guard(g, td, base_cfg, sd, cd, "", "local-demo", "update"))

    _test("review-missing", True,
          lambda td, sd, cd: _setup_and_guard(td, sd, cd,
              {"status": "approved", "risk": "low", "approved_by": "user",
               "approved_at": "2026-01-01T12:00:00+00:00", "spec_version": "1"}, review=False))

    _test("empty approved_at", True,
          lambda td, sd, cd: _setup_and_guard(td, sd, cd,
              {"status": "approved", "risk": "low", "approved_by": "user",
               "approved_at": "", "spec_version": "1"}))

    _test("self-approval high-risk", True,
          lambda td, sd, cd: _setup_and_guard(td, sd, cd,
              {"status": "approved", "risk": "high", "approved_by": "1c-developer",
               "approved_at": "2026-01-01T12:00:00+00:00", "spec_version": "1"}))

    _test("scope-hash drift", True,
          lambda td, sd, cd: _setup_and_guard(td, sd, cd,
              {"status": "approved", "risk": "low", "approved_by": "user",
               "approved_at": "2026-01-01T12:00:00+00:00", "spec_version": "1"},
              report_hash="FAKE_HASH"))

    _test("independence violation", True,
          lambda td, sd, cd: _setup_and_guard(td, sd, cd,
              {"status": "approved", "risk": "low", "approved_by": "1c-reviewer",
               "approved_at": "2026-01-01T12:00:00+00:00", "spec_version": "1"}))

    _test("server DB без per-db env", True,
          lambda td, sd, cd: _run_guard(g, td,
              {"environment": "local", "v8path": "C:\\fake",
               "databases": [{"id": "srv1", "type": "server", "server": "srv", "ref": "db",
               "username_env": "V8_USER", "password_env": "V8_PASS"}]},
              sd, cd, "TASK-T", "srv1", "update"))

    _test("production", True,
          lambda td, sd, cd: _run_guard(g, td,
              {"environment": "production", "v8path": "C:\\fake",
               "databases": [{"id": "prod1", "type": "file", "path": ".\\prod",
               "environment": "production"}]}, sd, cd, "TASK-T", "prod1", "update"))

    _test("unknown DB", True,
          lambda td, sd, cd: _run_guard(g, td, base_cfg, sd, cd, "TASK-T", "nonexistent", "update"))

    _test("load-dt заблокирована", True,
          lambda td, sd, cd: _run_guard(g, td, base_cfg, sd, cd, "TASK-T", "local-demo", "load-dt"))

    _test("create заблокирована", True,
          lambda td, sd, cd: _run_guard(g, td, base_cfg, sd, cd, "TASK-T", "local-demo", "create"))

    _test("load-cf заблокирована", True,
          lambda td, sd, cd: _run_guard(g, td, base_cfg, sd, cd, "TASK-T", "local-demo", "load-cf"))

    _test("Full mode заблокирован", True,
          lambda td, sd, cd: _run_guard(g, td, base_cfg, sd, cd, "TASK-T", "local-demo", "load-xml", "Full"))

    _test("web-publish заблокирована", True,
          lambda td, sd, cd: _run_guard(g, td, base_cfg, sd, cd, "TASK-T", "local-demo", "web-publish"))

    _test("path traversal TASK-ID", True,
          lambda td, sd, cd: _run_guard(g, td, base_cfg, sd, cd, "../etc/passwd", "local-demo", "update"))

    _test("backup-missing", True,
          lambda td, sd, cd: _setup_and_guard(td, sd, cd,
              {"status": "approved", "risk": "low", "approved_by": "user",
               "approved_at": "2026-01-01T12:00:00+00:00", "spec_version": "1"}, backup=False))

    _test("invalid timestamp (no timezone)", True,
          lambda td, sd, cd: _setup_and_guard(td, sd, cd,
              {"status": "approved", "risk": "low", "approved_by": "user",
               "approved_at": "2026-01-01", "spec_version": "1"}))

    _test("unknown risk", True,
          lambda td, sd, cd: _setup_and_guard(td, sd, cd,
              {"status": "approved", "risk": "critical", "approved_by": "user",
               "approved_at": "2026-01-01T12:00:00+00:00", "spec_version": "1"}))

    _test("missing spec_version", True,
          lambda td, sd, cd: _setup_and_guard(td, sd, cd,
              {"status": "approved", "risk": "low", "approved_by": "user",
               "approved_at": "2026-01-01T12:00:00+00:00"}))

    # --- Позитивные тесты (MUST pass with exit 0) ---

    _test("позитивный load-xml Partial", False,
          lambda td, sd, cd: _setup_and_guard(td, sd, cd,
              {"status": "approved", "risk": "low", "approved_by": "user",
               "approved_at": "2026-01-01T12:00:00+00:00", "spec_version": "1"},
              op="load-xml", mode="Partial", files="projects/test/src/test.bsl"))

    _test("позитивный update", False,
          lambda td, sd, cd: _setup_and_guard(td, sd, cd,
              {"status": "approved", "risk": "low", "approved_by": "user",
               "approved_at": "2026-01-01T12:00:00+00:00", "spec_version": "1"},
              op="update"))


def check_applier_self_path(rep: Report) -> None:
    """8.7: парсить frontmatter каждого агента, сверить self-path с путём установки."""
    if not IS_SOURCE_REPO:
        return  # только для исходного репо
    adapter_dirs = {
        "kilo": (".kilo/agent", "adapters/kilo/frontmatter"),
        "claude": (".claude/agents", "adapters/claude/frontmatter"),
        "openworks": (".opencode/agents", "adapters/openworks/frontmatter"),
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
            # Для openworks: .openworks/ — устаревший путь, должен быть .opencode/agents/
            if tool == "openworks":
                if f".openworks/agents/{agent}.md" in text:
                    wrong_patterns.append(f".openworks/agents/{agent}.md (ожидалось .opencode/agents/)")
                if f".openworks/agent/{agent}.md" in text:
                    wrong_patterns.append(f".openworks/agent/{agent}.md (ожидалось .opencode/agents/)")
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
    """Проверка: нет устаревших упоминаний OpenCode, КРОМЕ легитимных путей .opencode/ для адаптера openworks.
    OpenWork построен на OpenCode primitives и читает .opencode/agents/*.md.
    Разрешены: .opencode/agents, .opencode/skills, .opencode/context, .opencode/logs.
    Запрещены: adapters/opencode (каталог адаптера), OpenCode как название продукта (вместо OpenWork)."""
    offenders = []
    # Легитимные пути .opencode/ для openworks адаптера
    legit_opencode = re.compile(r"\.opencode/(agents|skills|context|logs)")
    # Запрещённые паттерны: каталог adapters/opencode, "OpenCode" как название
    forbidden = re.compile(r"adapters[/\\]opencode")
    search_paths = [ROOT / "README.md", ROOT / "docs", ROOT / "NOTICE.md",
                    ROOT / "install", ROOT / "adapters"]
    if IS_SOURCE_REPO:
        search_paths += [ROOT / "core" / "agents", ROOT / "core" / "context"]
    for p in search_paths:
        if not p.exists():
            continue
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"opencode", text, re.IGNORECASE):
                start = max(0, m.start() - 5)
                context = text[start:m.end()+20]
                if not legit_opencode.search(context) and forbidden.search(context):
                    offenders.append(f"{p.relative_to(ROOT)}: ...{context}...")
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and f.suffix in (".md", ".ps1", ".py", ".tpl", ".yml", ".json", ".toml"):
                    text = f.read_text(encoding="utf-8", errors="replace")
                    for m in re.finditer(r"opencode", text, re.IGNORECASE):
                        start = max(0, m.start() - 5)
                        context = text[start:m.end()+20]
                        if not legit_opencode.search(context) and forbidden.search(context):
                            offenders.append(f"{f.relative_to(ROOT)}: ...{context}...")
    if offenders:
        for item in offenders[:5]:
            rep.error(f"OpenCode-упоминание (запрещённое): {item}")
    else:
        rep.ok("opencode: только легитимные .opencode/ пути (openworks адаптер)")


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
                 ROOT / ".opencode" / "context" / "projects",
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
                reviewer = tdp / ".opencode" / "agents" / "1c-reviewer.md"
                if not reviewer.exists():
                    issues.append("нет .opencode/agents/1c-reviewer.md")
                else:
                    content = reviewer.read_text(encoding="utf-8-sig", errors="replace")
                    if not _parse_frontmatter(content):
                        issues.append("frontmatter .opencode/agents/1c-reviewer.md пустой/двойной (8.9)")
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
        for d in [".kilo/context/rules", ".claude/context/rules", ".opencode/context/rules", "context/rules"]:
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
        for d in [".kilo/agent", ".claude/agents", ".opencode/agents", "agents"]:
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
    for d in ["core/rules", ".kilo/context/rules", ".claude/context/rules", ".opencode/context/rules", "context/rules"]:
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
    for d in ["core/agents", ".kilo/agent", ".claude/agents", ".opencode/agents", "agents"]:
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


def check_license_and_copyright(rep: Report) -> None:
    """Проверка: LICENSE существует и содержит MIT + Kirill Pulyavin."""
    license_path = ROOT / "LICENSE"
    if not license_path.exists():
        rep.error("license: LICENSE не найден")
        return
    text = license_path.read_text(encoding="utf-8", errors="replace")
    if "MIT License" in text:
        rep.ok("license: LICENSE содержит MIT License")
    else:
        rep.error("license: LICENSE не содержит 'MIT License'")
    if "Kirill Pulyavin" in text:
        rep.ok("license: LICENSE содержит Copyright Kirill Pulyavin")
    else:
        rep.error("license: LICENSE не содержит 'Kirill Pulyavin'")


def check_agent_install(rep: Report) -> None:
    """Проверка: AGENT-INSTALL.md существует и содержит протокол + {GITHUB_URL}."""
    ai_path = ROOT / "AGENT-INSTALL.md"
    if not ai_path.exists():
        rep.error("agent-install: AGENT-INSTALL.md не найден")
        return
    text = ai_path.read_text(encoding="utf-8", errors="replace")
    checks = {
        "GITHUB_URL or https": "{GITHUB_URL}" in text or "https://github.com/" in text,
        "протокол установки": "Протокол установки" in text,
        "install.ps1": "install.ps1" in text,
        "doctor.py": "doctor.py" in text,
        "validate.py": "validate.py" in text,
        "Что НЕ делать": "Что НЕ делать" in text,
        "no OpenCode": "OpenCode" not in text or "OpenCode primitives" in text,
        "OpenWork": "OpenWork" in text,
    }
    for label, ok in checks.items():
        if ok:
            rep.ok(f"agent-install: содержит '{label}'")
        else:
            rep.error(f"agent-install: AGENT-INSTALL.md не содержит '{label}'")
    # README agent-first blockquote
    readme = ROOT / "README.md"
    if readme.exists():
        rm = readme.read_text(encoding="utf-8", errors="replace")
        if "Если ты ИИ-агент" in rm or "AGENT-INSTALL.md" in rm:
            rep.ok("agent-install: README содержит ссылку на AGENT-INSTALL.md")
        else:
            rep.error("agent-install: README не содержит ссылку на AGENT-INSTALL.md")


def check_no_update_db(rep: Report) -> None:
    """Проверка: --update-db не должен присутствовать в действующих инструкциях."""
    import re as _re
    scan_dirs = []
    if IS_SOURCE_REPO:
        scan_dirs = [ROOT / "core" / "agents", ROOT / "core" / "rules", ROOT / "core" / "sdd",
                     ROOT / "adapters", ROOT / "docs"]
    else:
        for d in [".kilo/agent", ".kilo/context", ".claude/agents", ".claude/context",
                   ".opencode/agents", ".opencode/context", "agents", "context"]:
            sp = ROOT / d
            if sp.is_dir():
                scan_dirs.append(sp)
    found = []
    for sd in scan_dirs:
        if not sd.is_dir():
            continue
        for f in sd.rglob("*"):
            if not f.is_file() or f.suffix not in (".md", ".py", ".ps1", ".yml"):
                continue
            if f.name == "validate.py":
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if _re.search(r"--update-db", text):
                found.append(str(f.relative_to(ROOT)))
    if found:
        for item in found[:5]:
            rep.error(f"update-db: '{item}' содержит --update-db")
    else:
        rep.ok("update-db: --update-db отсутствует в действующих инструкциях")


def check_no_old_review_path(rep: Report) -> None:
    """Проверка: specs/<TASK-ID>/review.md не должен использоваться как действующий путь."""
    import re as _re
    scan_dirs = []
    if IS_SOURCE_REPO:
        scan_dirs = [ROOT / "core" / "agents", ROOT / "core" / "rules", ROOT / "core" / "sdd",
                     ROOT / "core" / "context", ROOT / "adapters", ROOT / "docs"]
    else:
        for d in [".kilo/agent", ".kilo/context", ".claude/agents", ".claude/context",
                   ".opencode/agents", ".opencode/context", "agents", "context"]:
            sp = ROOT / d
            if sp.is_dir():
                scan_dirs.append(sp)
    found = []
    for sd in scan_dirs:
        if not sd.is_dir():
            continue
        for f in sd.rglob("*"):
            if not f.is_file() or f.suffix not in (".md", ".py", ".yml"):
                continue
            if f.name == "validate.py":
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            matches = _re.findall(r"specs/<TASK-ID>/review\.md", text)
            if matches:
                found.append(str(f.relative_to(ROOT)))
    if found:
        for item in found[:5]:
            rep.error(f"old-review-path: '{item}' использует specs/<TASK-ID>/review.md вместо pilot-control/")
    else:
        rep.ok("old-review-path: specs/<TASK-ID>/review.md не используется в действующих инструкциях")


def check_mock_apply(rep: Report) -> None:
    """Запуск mock apply end-to-end теста."""
    test_path = ROOT / "core" / "scripts" / "test_mock_apply.py" if IS_SOURCE_REPO else ROOT / "scripts" / "test_mock_apply.py"
    if not test_path.exists():
        rep.error("mock-apply: test_mock_apply.py не найден")
        return
    r = subprocess.run([sys.executable, str(test_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0:
        rep.ok("mock-apply: test_mock_apply.py → exit 0")
    else:
        rep.error(f"mock-apply: test_mock_apply.py → exit {r.returncode}")
        # Print last few lines for context
        lines = (r.stdout or "").strip().split("\n")
        for line in lines[-5:]:
            if line.strip():
                rep.error(f"  mock-apply: {line.strip()}")


def check_no_corporate_markers(rep: Report) -> None:
    """P1-11: проверка отсутствия корпоративных маркеров в публичной части core/."""
    import re as _re
    markers = [
        r"лвл_",
        r"бит_",
        r"БИТ\.",
        r"БИТ:",
        r"level-standards\.md",
    ]
    scan_dirs = []
    if IS_SOURCE_REPO:
        scan_dirs = [ROOT / "core" / "agents", ROOT / "core" / "context", ROOT / "core" / "sdd",
                     ROOT / "core" / "rules", ROOT / "core" / "scripts"]
    else:
        for d in [".kilo/agent", ".kilo/context", ".claude/agents", ".claude/context",
                   ".opencode/agents", ".opencode/context", "agents", "context", "scripts"]:
            sp = ROOT / d
            if sp.is_dir():
                scan_dirs.append(sp)
    found = []
    for sd in scan_dirs:
        if not sd.is_dir():
            continue
        for f in sd.rglob("*"):
            if not f.is_file() or f.suffix not in (".md", ".py", ".ps1", ".yml", ".json"):
                continue
            # validate.py сам содержит паттерны проверки — исключаем
            if f.name == "validate.py":
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            for pat in markers:
                if _re.search(pat, text):
                    found.append(f"{f.relative_to(ROOT)}: /{pat}/")
    if found:
        for item in found[:10]:
            rep.error(f"corporate-marker: {item}")
        if len(found) > 10:
            rep.error(f"corporate-marker: ... и ещё {len(found) - 10} совпадений")
    else:
        rep.ok("corporate-markers: публичный core не содержит корпоративных маркеров")


def check_project_sources_example(rep: Report) -> None:
    """Проверка examples/project-context.example.md: универсальная схема sources без корпоративных параметров.

    Гарантирует: (1) файл существует; (2) содержит maшиночитаемый yaml-блок sources с типами
    local/metadata/code/platform_help/standards; (3) НЕ содержит корпоративных URL/IP/UUID/credentials —
    только placeholders и env-переменные; (4) MCP sources по умолчанию enabled: false (публичный репо).
    """
    if not IS_SOURCE_REPO:
        return
    ex = ROOT / "examples" / "project-context.example.md"
    if not ex.exists():
        rep.error("project-sources-example: examples/project-context.example.md не найден")
        return
    text = ex.read_text(encoding="utf-8", errors="replace")
    # (2) yaml-блок sources с типами
    import re as _re
    yaml_match = _re.search(r"```yaml\s*\r?\n(.*?)```", text, _re.S)
    if not yaml_match:
        rep.error("project-sources-example: нет maшиночитаемого yaml-блока sources")
        return
    yaml_block = yaml_match.group(1)
    for src_type in ("local", "metadata", "code", "platform_help", "standards"):
        if f"{src_type}:" not in yaml_block:
            rep.error(f"project-sources-example: в yaml-блоке отсутствует тип источника '{src_type}'")
        else:
            rep.ok(f"project-sources-example: тип источника '{src_type}' присутствует")
    # (3) отсутствие корпоративных параметров: реальные URL/IP/UUID/credentials
    # Запрещённые паттерны: http(s):// с конкретным хостом (не placeholder), IP, UUID, password/token/secret
    forbidden_patterns = [
        (r"https?://[a-zA-Z0-9][a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "конкретный URL (не placeholder/env)"),
        (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "IP-адрес"),
        (r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "UUID"),
        (r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*['\"]?[^\s${'\"]+", "plaintext credential"),
    ]
    offenders = []
    for pat, label in forbidden_patterns:
        for m in _re.finditer(pat, text):
            offenders.append(f"{label}: ...{m.group(0)[:40]}...")
    if offenders:
        for item in offenders[:5]:
            rep.error(f"project-sources-example: корпоративный параметр в public файле — {item}")
    else:
        rep.ok("project-sources-example: нет корпоративных URL/IP/UUID/credentials (только placeholders/env)")
    # (4) MCP sources enabled: false по умолчанию (публичный репо)
    # Проверяем, что нет mcp-источников с enabled: true
    mcp_enabled_true = _re.findall(r"(metadata|code|platform_help|standards):\s*\r?\n\s*type:\s*mcp\s*\r?\n\s*enabled:\s*true", yaml_block)
    if mcp_enabled_true:
        rep.error(f"project-sources-example: MCP sources включены в public файле ({mcp_enabled_true}) — должны быть enabled: false")
    else:
        rep.ok("project-sources-example: MCP sources disabled в public файле (enabled: false)")
    # Проверка: есть указание на политику project-sources.md
    if "project-sources.md" in text:
        rep.ok("project-sources-example: ссылается на rules/project-sources.md")
    else:
        rep.error("project-sources-example: нет ссылки на rules/project-sources.md (политика приоритета)")


def check_source_policy_consistency(rep: Report) -> None:
    """Regression assertions: конфликтные старые формулировки, противоречащие project-sources.md.

    Проверяет отсутствие конкретных конфликтных фраз (не NLP-анализ — точные маркеры регрессии):
    - analyst: абсолютный запрет MCP при наличии project-sources support;
    - developer: «только local» / абсолютный запрет project MCP;
    - reviewer: «исходники не нужны для сверки» / approved без реализации в MCP-only;
    - INSTRUCTIONS: «единственный источник анализа — local» рядом с project MCP support;
    - BslChecklists: старая фраза «запросы ... на больших объёмах».
    """
    if not IS_SOURCE_REPO:
        return
    import re as _re

    # Конфликтные фразы (точные подстроки), которые НЕ должны присутствовать
    # после согласования с project-sources.md.
    conflict_checks = [
        # analyst: абсолютный запрет MCP / «только локальные файлы»
        ("core/agents/1c-analyst.md", r"MCP, интернет, `lsp`, `semantic_search` — запрещены",
         "analyst: абсолютный запрет MCP (заменён на разрешение объявленных project MCP)"),
        ("core/agents/1c-analyst.md", r"Выводы только по прочитанным файлам",
         "analyst: абсолютное «только по прочитанным файлам» (заменено на «по подтверждённым данным»)"),
        ("core/agents/1c-analyst.md", r"Не могу подтвердить по текущей локальной конфигурации",
         "analyst: старая фраза unconfirmed (заменена на «по доступным источникам»)"),
        ("core/agents/1c-analyst.md", r"unconfirmed-by-local-config",
         "analyst: старый slug лога unconfirmed-by-local-config"),
        # developer: «работаешь только с локальными» / «сторонние MCP запрещены; разрешён только v8std»
        ("core/agents/1c-developer.md", r"Работаешь только с локальными исходниками",
         "developer: «только локальные» в инвариантах (заменено на source of truth + project MCP)"),
        ("core/agents/1c-developer.md", r"сторонние MCP — запрещены\. Разрешён только MCP `v8std_\*`",
         "developer: абсолютный запрет project MCP (заменён на разрешение объявленных)"),
        # reviewer: «исходники не нужны для сверки»
        ("core/rules/project-sources.md", r"исходники не нужны для сверки",
         "project-sources: «исходники не нужны для сверки» (заменено на no-approved-without-implementation)"),
        # INSTRUCTIONS: «единственный источник анализа — локальные исходники»
        ("core/context/INSTRUCTIONS.md", r"Единственный источник анализа конфигурации — локальные исходники",
         "INSTRUCTIONS: «единственный источник — local» (устранено противоречие с project MCP)"),
        # BslChecklists: старая фраза «запросы на больших объёмах»
        ("core/context/BslChecklists.md", r"запросы на больших объёмах",
         "BslChecklists: старая фраза «запросы на больших объёмах» (заменена на архитектурный выбор)"),
    ]

    for rel_path, pattern, label in conflict_checks:
        fp = ROOT / rel_path
        if not fp.exists():
            rep.error(f"source-policy-consistency: файл не найден — {rel_path}")
            continue
        text = fp.read_text(encoding="utf-8", errors="replace")
        if _re.search(pattern, text):
            rep.error(f"source-policy-consistency: КОНФЛИКТ — {label} (в {rel_path})")
        else:
            rep.ok(f"source-policy-consistency: {rel_path} — конфликтная фраза отсутствует ({label[:50]})")

    # Обязательные маркеры (должны присутствовать — подтверждение новой политики)
    required_markers = [
        ("core/agents/1c-analyst.md", "project-sources.md", "analyst ссылается на project-sources.md"),
        ("core/agents/1c-developer.md", "project-sources.md", "developer ссылается на project-sources.md"),
        ("core/agents/1c-reviewer.md", "project-sources.md", "reviewer ссылается на project-sources.md"),
        ("core/rules/project-sources.md", "не выдавать `approved`", "project-sources: no-approved-without-implementation"),
    ]
    for rel_path, marker, label in required_markers:
        fp = ROOT / rel_path
        if not fp.exists():
            continue
        text = fp.read_text(encoding="utf-8", errors="replace")
        if marker in text:
            rep.ok(f"source-policy-consistency: {label} присутствует")
        else:
            rep.error(f"source-policy-consistency: обязательный маркер отсутствует — {label} (в {rel_path})")


def check_task5_regression(rep: Report) -> None:
    """Regression checks для polishing-итерации (task_5.md).

    Проверяет конкретные утверждения (не NLP):
    1. reviewer нигде не обязан писать review.md (возвращает текст через Task);
    2. canonical путь review — pilot-control/<TASK-ID>/review.md;
    3. 1c-do имеет транспортную роль (сохраняет review.md дословно);
    4. frontmatter descriptions не содержат «только локальными исходниками»;
    5. MCP profile example не содержит ${ENV} переменных;
    6. spec approval workflow содержит draft → user approval → approved_by → timezone-aware approved_at;
    7. MCP-only developer имеет explicit blocked status;
    8. .gitignore содержит per-project context rules;
    9. .gitignore не содержит .openworks/logs/ (устаревший путь);
    10. 1c-do.md содержит шаг 5.5 (human approval flow).
    """
    if not IS_SOURCE_REPO:
        return
    import re as _re

    # 1. reviewer не пишет review.md — проверяем frontmatter descriptions
    for adapter in ("kilo", "claude", "openworks"):
        for agent in ("1c-reviewer",):
            fp = ROOT / "adapters" / adapter / "frontmatter" / f"{agent}.yml"
            if not fp.exists():
                continue
            text = fp.read_text(encoding="utf-8", errors="replace")
            # Ищем "пишет pilot-control" или "пишет review.md" в description
            if _re.search(r"пишет\s+(pilot-control|review\.md)", text):
                rep.error(f"task5-regression: {adapter}/{agent}.yml description говорит «пишет review.md» — должен «возвращает через Task»")
            else:
                rep.ok(f"task5-regression: {adapter}/{agent}.yml — reviewer не пишет review.md")

    # 2. canonical путь review — pilot-control/
    do_md = ROOT / "core" / "agents" / "1c-do.md"
    if do_md.exists():
        text = do_md.read_text(encoding="utf-8", errors="replace")
        if "pilot-control/<TASK-ID>/review.md" in text:
            rep.ok("task5-regression: canonical review path — pilot-control/<TASK-ID>/review.md")
        else:
            rep.error("task5-regression: 1c-do.md не содержит pilot-control/<TASK-ID>/review.md")

    # 3. 1c-do имеет транспортную роль
    if do_md.exists():
        if "транспорт" in text and "review.md" in text:
            rep.ok("task5-regression: 1c-do имеет транспортную роль для review.md")
        else:
            rep.error("task5-regression: 1c-do.md не описывает транспортную роль для review.md")

    # 4. frontmatter descriptions не содержат «только локальными исходниками»
    for adapter in ("kilo", "claude", "openworks"):
        for agent in ("1c-analyst", "1c-developer"):
            fp = ROOT / "adapters" / adapter / "frontmatter" / f"{agent}.yml"
            if not fp.exists():
                continue
            text = fp.read_text(encoding="utf-8", errors="replace")
            if "только с локальными исходниками" in text or "только локальными исходниками" in text:
                rep.error(f"task5-regression: {adapter}/{agent}.yml содержит «только локальными исходниками»")
            else:
                rep.ok(f"task5-regression: {adapter}/{agent}.yml — нет «только локальными исходниками»")

    # 5. MCP profile example не содержит ${ENV}
    ex = ROOT / "examples" / "project-context.example.md"
    if ex.exists():
        text = ex.read_text(encoding="utf-8", errors="replace")
        env_vars = _re.findall(r"\$\{[A-Z_]+\}", text)
        if env_vars:
            rep.error(f"task5-regression: project-context.example.md содержит ${{ENV}} переменные: {env_vars}")
        else:
            rep.ok("task5-regression: project-context.example.md не содержит ${ENV} переменных")

    # 6. spec approval workflow (draft → user approval → approved_by → timezone-aware approved_at)
    if do_md.exists():
        text = do_md.read_text(encoding="utf-8", errors="replace")
        checks = [
            ("Human approval flow", "5.5. **Human approval flow"),
            ("approved_by: user", "approved_by: user"),
            ("timezone", "timezone-aware"),
            ("draft сохраняется", "статус остаётся `draft`"),
        ]
        for label, marker in checks:
            if marker in text:
                rep.ok(f"task5-regression: 1c-do.md содержит «{label}»")
            else:
                rep.error(f"task5-regression: 1c-do.md не содержит «{label}» (marker: {marker[:40]})")

    # 7. MCP-only developer explicit blocked status
    dev_md = ROOT / "core" / "agents" / "1c-developer.md"
    if dev_md.exists():
        text = dev_md.read_text(encoding="utf-8", errors="replace")
        if "blocked: writable local workspace отсутствует" in text:
            rep.ok("task5-regression: developer MCP-only explicit blocked status")
        else:
            rep.error("task5-regression: 1c-developer.md не содержит MCP-only blocked status")

    # 8. .gitignore содержит per-project context rules
    gi = ROOT / ".gitignore"
    if gi.exists():
        text = gi.read_text(encoding="utf-8", errors="replace")
        if ".kilo/context/projects/" in text and ".opencode/context/projects/" in text:
            rep.ok("task5-regression: .gitignore содержит per-project context rules")
        else:
            rep.error("task5-regression: .gitignore не содержит per-project context rules")

    # 9. .gitignore не содержит .openworks/logs/
    if gi.exists():
        if ".openworks/logs/" in text:
            rep.error("task5-regression: .gitignore содержит устаревший .openworks/logs/ (должен быть .opencode/logs/)")
        else:
            rep.ok("task5-regression: .gitignore не содержит .openworks/logs/")

    # 10. no .openworks/ in Python scripts (excluding validate.py self-checks)
    for py_file in (ROOT / "core" / "scripts").glob("*.py"):
        if py_file.name == "validate.py":
            continue  # validate.py contains .openworks/ in self-check patterns
        text = py_file.read_text(encoding="utf-8", errors="replace")
        if ".openworks/" in text:
            rep.error(f"task5-regression: {py_file.name} содержит устаревший .openworks/ путь")


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
    if IS_SOURCE_REPO:
        check_install_paths(rep)
        check_reviewer_in_adapters(rep)
    check_agents(rep)
    check_sdd_risk_gates(rep)
    check_applier_guards(rep)
    check_example_security(rep)
    if IS_SOURCE_REPO:
        check_no_opencode_refs(rep)
    # Adversarial tests
    check_adversarial_guard(rep)
    if IS_SOURCE_REPO:
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
    # Source-only checks (не требуются от установленного проекта)
    if IS_SOURCE_REPO:
        check_license_and_copyright(rep)
        check_agent_install(rep)
        check_no_corporate_markers(rep)
        check_project_sources_example(rep)
        check_source_policy_consistency(rep)
        check_task5_regression(rep)
    else:
        rep.ok("license/corporate: пропущено (установленный проект — не требуется)")
    check_no_update_db(rep)
    check_no_old_review_path(rep)
    check_mock_apply(rep)
    check_plan_parser(rep)
    check_kilo_tools_field(rep)
    check_kilo_permission_order(rep)
    check_scope_hash_permissions(rep)
    check_bsl_comment_regression(rep)
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
