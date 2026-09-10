#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doctor.py — безопасная диагностика локальной установки агентской схемы 1С Dev.

Не подключается к 1С, не изменяет пользовательские данные, не выводит секреты.

Проверяет:
  - версию Python;
  - обязательные каталоги (core/agents, core/skills, core/context, core/scripts, adapters, install);
  - наличие всех агентов (включая 1c-reviewer) в core/agents и в установленной раскладке;
  - согласованность адаптеров (kilo, claude, codex, openworks: frontmatter/шаблоны, 1c-reviewer);
  - доступность skills (счётчик core/skills/*/SKILL.md);
  - корректность ключевых путей (плейсхолдеры {{CONTEXT_DIR}} и др. подставляются установщиком);
  - конфигурацию .v8-project.json без вывода секретов (environment, env-переменные секретов);
  - допустимость environment (local|test|staging — ok; production/отсутствие — блокирует изменяющие операции);
  - SDD-статусы (specs/*/03_solution_spec.md содержат машиночитаемый блок status/risk);
  - готовность локальной установки (наличие скриптов: bsl-check.py, build_summaries.py,
    applier_guard.py, validate.py, doctor.py; наличие PowerShell — опционально).

Запуск:
  python scripts/doctor.py
  python scripts/doctor.py --root <путь к установленной раскладке>  (диагностика целевого каталога)

Exit codes:
  0 — проверка пройдена (или только предупреждения)
  1 — есть ошибки
  2 — ошибка запуска/аргументов
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Импорт общего модуля детекции корня
import sys as _sys
_sys.path.insert(0, str(SCRIPT_DIR))
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
    return SCRIPT_DIR.parent.parent


DEFAULT_ROOT = _find_root(SCRIPT_DIR)

AGENTS = ["1c-do", "1c-analyst", "1c-developer", "1c-reviewer", "1c-applier", "1c-tools"]
ADAPTERS = {
    "kilo": {"agents_dir": ".kilo/agent"},
    "claude": {"agents_dir": ".claude/agents"},
    "openworks": {"agents_dir": ".openworks/agents"},
    "codex": {"agents_dir": "agents"},
}
ALLOWED_ENVS = {"local", "test", "staging"}


class Doc:
    def __init__(self):
        self.errors = 0
        self.warns = 0
        self.lines: list[str] = []

    def ok(self, m: str) -> None:
        self.lines.append(f"  [OK]   {m}")

    def warn(self, m: str) -> None:
        self.warns += 1
        self.lines.append(f"  [WARN] {m}")

    def err(self, m: str) -> None:
        self.errors += 1
        self.lines.append(f"  [FAIL] {m}")

    def info(self, m: str) -> None:
        self.lines.append(f"  [..]   {m}")

    def section(self, m: str) -> None:
        self.lines.append(f"\n{m}")


def doctor(root: Path, d: Doc) -> None:
    # --- Python ---
    d.section("Python")
    d.info(f"python {platform.python_version()} ({sys.executable})")
    v = sys.version_info
    if v < (3, 8):
        d.err("требуется Python 3.8+")
    else:
        d.ok("версия Python поддерживается")

    # --- Обязательные каталоги ---
    d.section("Обязательные каталоги")
    for sub in ["core/agents", "core/skills", "core/context", "core/scripts", "adapters", "install"]:
        p = root / sub
        if p.is_dir():
            d.ok(f"{sub}/")
        else:
            # В целевой установке core/ может не быть (раскладка по .kilo/...) — предупреждаем
            d.warn(f"{sub}/ не найден (для исходного репо — ошибка; для целевой установки может быть нормой)")

    # Установленная раскладка: ищем агентов по известным путям адаптеров
    d.section("Установленная раскладка (агенты)")
    installed_layout_found = False
    for tool, meta in ADAPTERS.items():
        adir = root / meta["agents_dir"]
        if adir.is_dir():
            installed_layout_found = True
            missing = [a for a in AGENTS if not (adir / f"{a}.md").exists()]
            if missing:
                d.err(f"{tool}: отсутствуют агенты: {', '.join(missing)}")
            else:
                d.ok(f"{tool}: все {len(AGENTS)} агентов (включая 1c-reviewer)")
    # канонический core/agents (исходный репо)
    core_agents = root / "core" / "agents"
    if core_agents.is_dir():
        missing = [a for a in AGENTS if not (core_agents / f"{a}.md").exists()]
        if missing:
            d.err(f"core/agents: отсутствуют: {', '.join(missing)}")
        else:
            d.ok("core/agents: все агенты (включая 1c-reviewer)")
    if not installed_layout_found and not core_agents.is_dir():
        d.err("не найдено ни core/agents, ни установленной раскладки адаптеров")

    # --- Согласованность адаптеров ---
    d.section("Согласованность адаптеров")
    adapters_dir = root / "adapters"
    if adapters_dir.is_dir():
        for tool in ADAPTERS:
            tdir = adapters_dir / tool
            if not tdir.is_dir():
                d.err(f"adapters/{tool}: каталог отсутствует")
                continue
            fm_dir = tdir / "frontmatter"
            if tool == "codex":
                tpl = tdir / "AGENTS.md.tpl"
                if tpl.exists() and "1c-reviewer" in tpl.read_text(encoding="utf-8", errors="replace"):
                    d.ok("codex: AGENTS.md.tpl упоминает 1c-reviewer")
                else:
                    d.err("codex: AGENTS.md.tpl без 1c-reviewer")
            else:
                if fm_dir.is_dir() and (fm_dir / "1c-reviewer.yml").exists():
                    d.ok(f"{tool}: frontmatter/1c-reviewer.yml")
                else:
                    d.err(f"{tool}: frontmatter/1c-reviewer.yml отсутствует")
        if (adapters_dir / "opencode").exists():
            d.err("adapters/opencode: устаревший каталог (переименован в openworks)")
        else:
            d.ok("нет устаревшего adapters/opencode")
    else:
        d.info("adapters/ не найден (целевая установка — норма)")

    # --- Skills ---
    d.section("Skills")
    skills = root / "core" / "skills"
    if skills.is_dir():
        n = len([p for p in skills.iterdir() if (p / "SKILL.md").exists()])
        d.ok(f"core/skills: {n} скиллов с SKILL.md")
    else:
        # целевая установка
        for tool, meta in {"kilo": ".kilo/skills", "claude": ".claude/skills", "openworks": ".openworks/skills", "codex": "skills"}.items():
            sp = root / meta
            if sp.is_dir():
                n = len([p for p in sp.iterdir() if (p / "SKILL.md").exists()])
                d.ok(f"{meta}: {n} скиллов")

    # --- Скрипты ---
    d.section("Скрипты")
    for s in ["bsl-check.py", "build_summaries.py", "applier_guard.py", "safe_apply.py", "validate.py", "doctor.py", "_root.py"]:
        p = SCRIPT_DIR / s
        if p.exists():
            d.ok(f"scripts/{s}")
        else:
            d.err(f"scripts/{s} отсутствует")
    if shutil.which("powershell") or shutil.which("pwsh"):
        d.ok("PowerShell доступен (скиллы .ps1)")
    else:
        d.warn("PowerShell не найден в PATH (скиллы .ps1 не запустятся)")

    # --- .v8-project.json ---
    d.section("Конфигурация .v8-project.json")
    cfg_path = root / ".v8-project.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig", errors="replace"))
            env = str(cfg.get("environment", "")).strip()
            if env in ALLOWED_ENVS:
                d.ok(f"environment = '{env}' (изменяющие операции разрешены guard-ом при прочих условиях)")
            elif env == "production":
                d.err("environment = 'production' — изменяющие операции ЗАБЛОКИРОВАНЫ (всегда)")
            elif not env:
                d.err("environment отсутствует/пуст — изменяющие операции ЗАБЛОКИРОВАНЫ")
            else:
                d.err(f"environment = '{env}' неизвестно — изменяющие операции ЗАБЛОКИРОВАНЫ")
            dbs = cfg.get("databases") or []
            d.info(f"зарегистрировано баз: {len(dbs)} (детали не выводятся — безопасность)")
            for db in dbs:
                if isinstance(db, dict) and ("user" in db or "password" in db):
                    d.err(f"база '{db.get('id', '?')}': plaintext user/password — мигрируйте на username_env/password_env")
        except Exception as e:
            d.err(f".v8-project.json не читается: {e}")
    else:
        d.warn(".v8-project.json не найден (создайте из examples/v8-project.example.json)")

    # --- .dev.env ---
    d.section("Параметры проекта (.dev.env)")
    dev_env_path = root / ".dev.env"
    if dev_env_path.exists():
        try:
            env_text = dev_env_path.read_text(encoding="utf-8", errors="replace")
            platform_path = ""
            for line in env_text.splitlines():
                if line.startswith("PLATFORM_PATH="):
                    platform_path = line.split("=", 1)[1].strip()
            if platform_path:
                if Path(platform_path).exists():
                    d.ok(f"PLATFORM_PATH={platform_path} (1cv8.exe найден)")
                else:
                    d.warn(f"PLATFORM_PATH={platform_path} (1cv8.exe не найден по указанному пути)")
            else:
                d.info("PLATFORM_PATH пуст — скиллы db-* используют автопоиск")
            d.ok(".dev.env найден")
        except Exception as e:
            d.err(f".dev.env не читается: {e}")
    else:
        # В исходном репо проверяем шаблон
        env_example = root / "core" / "context" / ".dev.env.example"
        if env_example.exists():
            d.ok("core/context/.dev.env.example (шаблон; .dev.env создаётся при установке)")
        else:
            d.warn(".dev.env не найден (создаётся install.ps1 при установке)")

    # --- .ai-rules.json ---
    d.section("Манифест установки (.ai-rules.json)")
    manifest_path = root / ".ai-rules.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig", errors="replace"))
            files = manifest.get("files") or []
            user_mod = sum(1 for f in files if f.get("userModified"))
            d.ok(f".ai-rules.json: {len(files)} файлов, {user_mod} user-modified")
        except Exception as e:
            d.err(f".ai-rules.json не читается: {e}")
    else:
        d.info(".ai-rules.json не найден (создаётся install.ps1 при установке)")

    # --- On-demand правила ---
    d.section("On-demand правила (rules/)")
    rules_dirs = [
        root / "core" / "rules",
        root / ".kilo" / "context" / "rules",
        root / ".claude" / "context" / "rules",
        root / ".openworks" / "context" / "rules",
        root / "context" / "rules",
    ]
    rules_found = False
    for rd in rules_dirs:
        if rd.is_dir():
            rules_found = True
            n = len(list(rd.glob("*.md")))
            d.ok(f"{rd.relative_to(root) if rd.is_relative_to(root) else rd}: {n} правил")
    if not rules_found:
        d.warn("rules/ не найден (core/rules/ в исходном репо или {{CONTEXT_DIR}}/rules/ после установки)")

    # --- SDD ---
    d.section("SDD-статусы (specs/)")
    specs = root / "specs"
    if specs.is_dir():
        task_dirs = [p for p in specs.iterdir() if p.is_dir()]
        if not task_dirs:
            d.info("specs/: задач нет")
        for td in task_dirs:
            spec = td / "03_solution_spec.md"
            if not spec.exists():
                d.warn(f"{td.name}: нет 03_solution_spec.md")
                continue
            text = spec.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"```yaml\s*\r?\n(.*?)```", text, re.S)
            if not m:
                d.warn(f"{td.name}: spec без машиночитаемого yaml-блока (считается неподтверждённой)")
                continue
            status = ""
            risk = ""
            for line in m.group(1).splitlines():
                if line.startswith("status:"):
                    status = line.split(":", 1)[1].strip()
                if line.startswith("risk:"):
                    risk = line.split(":", 1)[1].strip()
            d.info(f"{td.name}: status={status or '?'}, risk={risk or '?'}")
            if status == "approved" and risk == "high":
                rv = td / "review.md"
                if rv.exists() and "verdict: approved" in rv.read_text(encoding="utf-8", errors="replace"):
                    d.ok(f"{td.name}: high-risk подтверждён review verdict approved")
                else:
                    d.warn(f"{td.name}: high-risk без review verdict approved — apply будет заблокирован")
    else:
        d.info("specs/ не найден (задач нет)")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="doctor.py", description="Безопасная диагностика локальной установки 1C Dev (без 1С, без секретов).")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Корень раскладки (по умолчанию — рядом со скриптом)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    d = Doc()
    print(f"=== doctor: {root} ===")
    doctor(root, d)
    print("\n".join(d.lines))
    print(f"\n=== Итог: FAIL={d.errors}, WARN={d.warns} ===")
    if d.errors:
        print("РЕЗУЛЬТАТ: ЕСТЬ ОШИБКИ (exit 1)")
        return 1
    print("РЕЗУЛЬТАТ: OK (exit 0)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        raise SystemExit(2)
