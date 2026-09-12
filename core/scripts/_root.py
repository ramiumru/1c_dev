#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_root.py — общая утилита автоопределения корня.

Две функции с разной семантикой:
  - find_root(start) — корень HARNESS (где core/agents, install/, specs/README.md).
    Используется validate.py, doctor.py, applier_guard.py для проверки схемной разработки.
  - find_project_root(start) — корень РАБОЧЕГО ПРОЕКТА (где projects/ + .kilo/).
    Используется build_summaries.py для поиска исходников 1С и per-project контекста.

Разделение необходимо, т.к. harness (ai-environment) и рабочий проект (C:\\Ramium\\1c-vibe)
— разные каталоги: harness содержит схему разработки, рабочий проект — исходники 1С.

Маркеры harness (find_root):
  1. .v8-project.json              — реестр баз (в корне рабочего проекта ИЛИ harness)
  2. install/install.ps1           — только harness (не в рабочем проекте)
  3. specs/README.md               — SDD-шаблоны (в корне harness или установленной раскладки)
  4. core/agents/                  — harness (канонический источник)
  5. .kilo/agent/ | .claude/agents/ | .openworks/agents/  — установленная раскладка
  6. scripts/ + examples/          — установленная раскладка

Маркеры рабочего проекта (find_project_root):
  1. projects/ + .kilo/            — рабочий проект (исходники 1С + per-project контекст)
  2. .v8-project.json + projects/  — рабочий проект с реестром баз

Если ни один маркер не найден — возвращается None; вызывающий код должен
требовать --root и завершаться с ошибкой.
"""

from __future__ import annotations

import os
from pathlib import Path


_ADAPTER_AGENT_DIRS = (
    ".kilo/agent",
    ".claude/agents",
    ".openworks/agents",
)


def _resolve_safe(p: Path) -> Path:
    """Безопасный resolve — использует os.path.abspath если resolve зависает."""
    try:
        return Path(os.path.abspath(str(p)))
    except Exception:
        return p


def find_root(start: Path) -> Path | None:
    """Автоопределение корня HARNESS: первый родитель с маркером схемной разработки.

    Ограничивает поиск 10 уровнями вверх для производительности.
    """
    cur = _resolve_safe(start)
    cand = cur
    count = 0
    while cand is not None and count <= 10:
        count += 1
        # .v8-project.json — высший приоритет (может быть в harness или рабочем проекте)
        if (cand / ".v8-project.json").exists():
            return cand
        if (cand / "install" / "install.ps1").exists():
            return cand
        if (cand / "specs" / "README.md").exists():
            return cand
        if (cand / "core" / "agents").is_dir():
            return cand
        for ad in _ADAPTER_AGENT_DIRS:
            adp = cand / ad
            if adp.is_dir() and (adp / "1c-do.md").exists():
                return cand
        if (cand / "scripts").is_dir() and (cand / "examples").is_dir():
            return cand
        if (cand / "scripts").is_dir() and (cand / "specs").is_dir():
            return cand
        parent = cand.parent
        if parent == cand:
            break
        cand = parent
    return None


def find_project_root(start: Path) -> Path | None:
    """Автоопределение корня РАБОЧЕГО ПРОЕКТА: первый родитель с projects/ + .kilo/.

    Ограничивает поиск 10 уровнями вверх для производительности.
    """
    cur = _resolve_safe(start)
    cand = cur
    count = 0
    while cand is not None and count <= 10:
        count += 1
        if (cand / "projects").is_dir() and (cand / ".kilo").is_dir():
            return cand
        if (cand / ".v8-project.json").exists() and (cand / "projects").is_dir():
            return cand
        parent = cand.parent
        if parent == cand:
            break
        cand = parent
    return None
