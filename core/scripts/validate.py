#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate.py — единая переносимая локальная проверка агентской схемы 1С Dev.

Не требует платформы 1С, подключения к базе, корпоративной сети или секретов.
Только локальные read-only проверки.

Проверки:
  1. Синтаксис Python через compileall (core/scripts, core/skills/**/*.py).
  2. Наличие обязательных файлов (README, NOTICE, THIRD_PARTY_LICENSES, SECURITY,
     CONTRIBUTING, examples/v8-project.example.json, core/sdd/README.md).
  3. Внутренние Markdown-ссылки в README.md и docs/.
  4. Пути в документации и шаблонах адаптеров не ссылаются на отсутствующие файлы
     (базовая проверка ключевых путей install.ps1).
  5. Наличие всех агентов (core/agents/*.md): 1c-do, 1c-analyst, 1c-developer,
     1c-reviewer, 1c-applier, 1c-tools.
  6. Наличие 1c-reviewer во всех адаптерах (kilo, claude, codex, openworks).
  7. Smoke-тест установщика во временном каталоге (install.ps1 -Tool для каждого).
  8. SDD-статусы и risk gates (шаблон spec содержит машиночитаемый блок status/risk).
  9. Guards для 1c-applier (applier_guard.py существует, блокирует production/нет env).
 10. Безопасность .v8-project.example.json (environment задано, нет plaintext user/password,
     есть username_env/password_env).
 11. Отсутствие секретов в примерах (нет паролей-литералов в examples/).
 12. Отсутствие ошибочных упоминаний OpenCode, относящихся к адаптеру Open Works.

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

def _find_root(start: Path) -> Path:
    """Автоопределение корня проекта: первый родитель (включая текущий) с маркером README.md.
    Работает и в исходном репо (core/scripts/validate.py -> ai-environment/), и в установленной
    раскладке (scripts/validate.py -> корень установки)."""
    cur = start.resolve()
    for cand in [cur, *cur.parents]:
        if (cand / "README.md").exists():
            return cand
    return cur.parent if cur.parents else cur


ROOT = _find_root(Path(__file__).resolve())
EXPECTED_AGENTS = ["1c-do", "1c-analyst", "1c-developer", "1c-reviewer", "1c-applier", "1c-tools"]
ADAPTERS = ["kilo", "claude", "codex", "openworks"]
REQUIRED_FILES = [
    "README.md",
    "NOTICE.md",
    "THIRD_PARTY_LICENSES.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "examples/v8-project.example.json",
    "core/sdd/README.md",
    "core/scripts/applier_guard.py",
    "core/scripts/bsl-check.py",
    "core/scripts/build_summaries.py",
    "core/scripts/validate.py",
    "core/scripts/doctor.py",
    "install/install.ps1",
]


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
    targets = [ROOT / "core" / "scripts"]
    # Также компилируем .py из skills, если py_compile поддерживает (skip не-BCP-скрипты)
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
    # skills py — best-effort (некоторые содержат UI-вставки, но компилироваться должны)
    skills_py = list((ROOT / "core" / "skills").rglob("*.py"))
    for py in skills_py:
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
    for f in REQUIRED_FILES:
        p = ROOT / f
        if p.exists() and p.stat().st_size > 0:
            rep.ok(f"required: {f}")
        else:
            rep.error(f"required: отсутствует/пуст: {f}")


def check_md_links(rep: Report) -> None:
    md_files = [ROOT / "README.md"] + list((ROOT / "docs").rglob("*.md"))
    link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    for md in md_files:
        if not md.exists():
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for m in link_re.finditer(text):
            label, target = m.group(1), m.group(2)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            # strip anchor
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            resolved = (md.parent / path_part).resolve()
            if not resolved.exists():
                rep.warn(f"md-link: {md.relative_to(ROOT)} -> {target} (не найден)")


def check_install_paths(rep: Report) -> None:
    ip = ROOT / "install" / "install.ps1"
    if not ip.exists():
        rep.error("install.ps1 отсутствует")
        return
    text = ip.read_text(encoding="utf-8", errors="replace")
    # ключевые элементы должны присутствовать
    for needle in ["openworks", "ValidateSet", "adapters\\$Tool", "examples\\v8-project.example.json"]:
        if needle not in text:
            rep.error(f"install.ps1: отсутствует фрагмент '{needle}'")
    if "opencode" in text:
        rep.error("install.ps1: содержит устаревшее 'opencode' (ожидается openworks)")
    else:
        rep.ok("install.ps1: no opencode references")


def check_agents(rep: Report) -> None:
    agents_dir = ROOT / "core" / "agents"
    for a in EXPECTED_AGENTS:
        p = agents_dir / f"{a}.md"
        if p.exists() and p.stat().st_size > 0:
            rep.ok(f"agent: {a}.md")
        else:
            rep.error(f"agent: отсутствует/пуст: core/agents/{a}.md")


def check_reviewer_in_adapters(rep: Report) -> None:
    for ad in ADAPTERS:
        ad_dir = ROOT / "adapters" / ad
        if not ad_dir.exists():
            rep.error(f"adapter: отсутствует каталог adapters/{ad}")
            continue
        found = False
        # multi-agent: frontmatter/1c-reviewer.yml
        fm = ad_dir / "frontmatter" / "1c-reviewer.yml"
        if fm.exists():
            found = True
        # codex: AGENTS.md.tpl упоминает 1c-reviewer
        tpl = ad_dir / "AGENTS.md.tpl"
        if tpl.exists() and "1c-reviewer" in tpl.read_text(encoding="utf-8", errors="replace"):
            found = True
        if found:
            rep.ok(f"adapter {ad}: 1c-reviewer присутствует")
        else:
            rep.error(f"adapter {ad}: 1c-reviewer отсутствует")


def check_sdd_risk_gates(rep: Report) -> None:
    spec_readme = ROOT / "core" / "sdd" / "README.md"
    text = spec_readme.read_text(encoding="utf-8", errors="replace")
    for needle in ["status:", "risk:", "approved_by", "scope_hash", "1c-reviewer", "review.md"]:
        if needle not in text:
            rep.error(f"SDD README: отсутствует риск-gate элемент '{needle}'")
    # Шаблон spec должен содержать yaml-блок
    if "```yaml" not in text or "approved" not in text:
        rep.error("SDD README: шаблон spec не содержит машиночитаемый yaml-блок status/risk")
    rep.ok("SDD README: risk gates присутствуют")


def check_applier_guards(rep: Report) -> None:
    g = ROOT / "core" / "scripts" / "applier_guard.py"
    if not g.exists():
        rep.error("applier_guard.py отсутствует")
        return
    # Проверка блокировки production — через запуск на синтетическом конфиге
    import json as _json
    with tempfile.TemporaryDirectory(prefix="applier_guard_") as td:
        tdpath = Path(td)
        cfg = {"environment": "production", "databases": [{"id": "db1", "type": "file", "path": "x"}]}
        (tdpath / "cfg.json").write_text(_json.dumps(cfg), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(g), "--db", "db1", "--op", "update", "--config", str(tdpath / "cfg.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0 and "production" in (r.stdout + r.stderr).lower():
            rep.ok("applier_guard: блокирует production")
        else:
            rep.error(f"applier_guard: НЕ блокирует production (exit={r.returncode})")
        # Отсутствие environment -> блок
        cfg2 = {"databases": [{"id": "db1", "type": "file", "path": "x"}]}
        (tdpath / "cfg2.json").write_text(_json.dumps(cfg2), encoding="utf-8")
        r2 = subprocess.run(
            [sys.executable, str(g), "--db", "db1", "--op", "update", "--config", str(tdpath / "cfg2.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r2.returncode != 0:
            rep.ok("applier_guard: блокирует отсутствие environment")
        else:
            rep.error("applier_guard: НЕ блокирует отсутствие environment")


def check_example_security(rep: Report) -> None:
    ex = ROOT / "examples" / "v8-project.example.json"
    try:
        data = json.loads(ex.read_text(encoding="utf-8-sig"))
    except Exception as e:
        rep.error(f"example json не парсится: {e}")
        return
    env = str(data.get("environment", "")).strip()
    if env not in ("local", "test", "staging"):
        rep.error(f"example: environment недопустимо/отсутствует: '{env}'")
    else:
        rep.ok(f"example: environment={env}")
    dbs = data.get("databases") or []
    for d in dbs:
        if "user" in d or "password" in d:
            rep.error(f"example: база {d.get('id')} содержит plaintext user/password")
        if not d.get("username_env") or not d.get("password_env"):
            rep.warn(f"example: база {d.get('id')} без username_env/password_env")
    # явные пароли-литералы
    raw = ex.read_text(encoding="utf-8-sig")
    if re.search(r'"password"\s*:\s*"[^"]+"', raw) or re.search(r'"password"\s*:\s*"[^"]*"', raw):
        # password_env содержит 'password_env' не 'password' — уточняем
        if re.search(r'"password"\s*:\s*"', raw):
            rep.error("example: найден plaintext 'password' (не password_env)")


def check_no_opencode_refs(rep: Report) -> None:
    # Ищем "OpenCode" (как название адаптера) и "opencode" в путях .opencode/ / opencode.json / adapters/opencode
    offenders = []
    for p in [ROOT / "README.md", ROOT / "docs" / "adding-skills.md", ROOT / "NOTICE.md",
              ROOT / "install" / "install.ps1", ROOT / "adapters"]:
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


def check_installer_smoke(rep: Report, skip_smoke: bool) -> None:
    if skip_smoke:
        rep.warn("smoke: пропущен (--skip-smoke)")
        return
    pwsh = shutil.which("powershell") or shutil.which("pwsh")
    if not pwsh:
        rep.warn("smoke: PowerShell не найден — пропуск smoke-теста")
        return
    for tool in ADAPTERS:
        with tempfile.TemporaryDirectory(prefix=f"install_smoke_{tool}_") as td:
            r = subprocess.run(
                [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "install" / "install.ps1"), "-Tool", tool, "-Target", td],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
            )
            if r.returncode != 0:
                rep.error(f"smoke install -Tool {tool}: exit={r.returncode}; stderr={r.stderr[:500]}")
                continue
            # проверим ключевые артефакты
            tdp = Path(td)
            issues = []
            if tool == "kilo":
                if not (tdp / ".kilo" / "agent" / "1c-reviewer.md").exists():
                    issues.append("нет .kilo/agent/1c-reviewer.md")
                if not (tdp / "INSTRUCTIONS.md").exists():
                    issues.append("нет INSTRUCTIONS.md в корне")
            elif tool == "claude":
                if not (tdp / ".claude" / "agents" / "1c-reviewer.md").exists():
                    issues.append("нет .claude/agents/1c-reviewer.md")
            elif tool == "openworks":
                if not (tdp / ".openworks" / "agents" / "1c-reviewer.md").exists():
                    issues.append("нет .openworks/agents/1c-reviewer.md")
            elif tool == "codex":
                if not (tdp / "agents" / "1c-reviewer.md").exists():
                    issues.append("нет agents/1c-reviewer.md")
            if issues:
                rep.error(f"smoke {tool}: " + "; ".join(issues))
            else:
                rep.ok(f"smoke install -Tool {tool}: OK")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="validate.py", description="Единая локальная проверка агентской схемы 1С Dev.")
    parser.add_argument("--skip-smoke", action="store_true", help="Пропустить smoke-тест установщика")
    args = parser.parse_args()

    rep = Report()
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
