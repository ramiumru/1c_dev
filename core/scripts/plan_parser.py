#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan_parser.py — единый строгий парсер apply-плана из 06_change_report.md.

Используется safe_apply.py и applier_guard.py. Не создавать независимых копий.

Алгоритм:
  1. Найти заголовок второго уровня: ## Изменённые файлы
  2. Читать только содержимое этого раздела до следующего заголовка второго уровня ##
  3. Извлекать пути только из элементов Markdown-списка раздела (строки, начинающиеся с - или *)
  4. Поддерживать путь в обратных кавычках: - `projects/.../file.bsl` — описание
  5. Игнорировать пути в других разделах (описание, риски, команды и т.д.)
  6. Нормализовать разделители пути для сравнения
  7. Сохранять порядок
  8. Обнаруживать дубликаты
  9. Требовать непустой список конкретных файлов
  10. Блокировать: *, **, ?, абсолютные пути, .., пустые пути, каталоги, файлы вне configSrc

Не использует исключение * после извлечения путей из всего документа.
Источник плана ограничен конкретным Markdown-разделом.
"""

from __future__ import annotations

import re
from pathlib import Path


def extract_section(text: str, header: str = "Изменённые файлы") -> str:
    """Извлечь содержимое раздела ## <header> до следующего заголовка второго уровня ##.

    Возвращает пустую строку, если раздел не найден.
    """
    pattern = rf"^##\s*{re.escape(header)}\s*\r?\n(.*?)(?=^##\s|\Z)"
    m = re.search(pattern, text, re.S | re.M)
    if m:
        return m.group(1)
    return ""


def parse_plan_from_report(report_text: str, config_src: str = "",
                           project_root: Path = None) -> tuple[list[str], list[str]]:
    """Разобрать apply-план из текста 06_change_report.md.

    Аргументы:
      report_text — полный текст 06_change_report.md
      config_src — configSrc из .v8-project.json (для проверки scope)
      project_root — корень проекта (для проверки существования файлов)

    Возвращает: (plan_files, errors)
      plan_files — список нормализованных путей (может быть пустым при ошибках)
      errors — список понятных ошибок (пустой при успехе)
    """
    errors: list[str] = []

    # 1. Найти раздел
    section = extract_section(report_text, "Изменённые файлы")
    if not section.strip():
        errors.append("план: раздел «Изменённые файлы» отсутствует или пуст — apply заблокирован")
        return [], errors

    # 2. Извлечь пути только из элементов Markdown-списка
    # Поддерживаем: - path, * path, - `path`, * `path`
    # Извлекаем путь после маркера списка и необязательных backticks
    list_item_re = re.compile(r"^\s*[-*]\s+`?(projects/[^\s`]+)`?\s*", re.IGNORECASE)

    raw_paths: list[str] = []
    for line in section.splitlines():
        line = line.strip()
        if not line:
            continue
        m = list_item_re.match(line)
        if m:
            raw_paths.append(m.group(1))
        # Строки без маркера списка в разделе игнорируем (описательный текст внутри раздела)

    # 3. Проверить непустой список
    if not raw_paths:
        errors.append("план: раздел «Изменённые файлы» не содержит элементов списка — apply заблокирован")
        return [], errors

    # 4. Нормализация и валидация каждого пути
    config_src_norm = config_src.replace("\\", "/").rstrip("/") if config_src else ""
    seen: set[str] = set()
    plan_files: list[str] = []

    for raw in raw_paths:
        path_str = raw.rstrip(",.;:—-")
        path_norm = path_str.replace("\\", "/")

        # Дубликат
        if path_norm in seen:
            errors.append(f"план: дубликат пути '{path_str}'")
            continue
        seen.add(path_norm)

        # Wildcards
        if "*" in path_norm or "?" in path_norm:
            errors.append(f"план: wildcard в пути заблокирован: '{path_str}'")
            continue

        # Абсолютный путь (Windows или POSIX)
        if path_norm.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", path_norm):
            errors.append(f"план: абсолютный путь заблокирован: '{path_str}'")
            continue

        # Path traversal
        if ".." in path_norm.split("/"):
            errors.append(f"план: path traversal заблокирован: '{path_str}'")
            continue

        # Пустой путь
        if not path_norm.strip():
            errors.append("план: пустой путь заблокирован")
            continue

        # Каталог вместо файла (нет расширения или заканчивается на /)
        if path_norm.endswith("/"):
            errors.append(f"план: каталог вместо файла заблокирован: '{path_str}'")
            continue

        # Файл вне configSrc
        if config_src_norm and not path_norm.startswith(config_src_norm + "/"):
            errors.append(f"план: файл вне configSrc '{config_src_norm}' заблокирован: '{path_str}'")
            continue

        # Существование файла (если project_root задан)
        if project_root is not None:
            if not (project_root / path_norm).exists():
                errors.append(f"план: файл отсутствует: '{path_str}'")
                continue

        plan_files.append(path_norm)

    if not plan_files and not errors:
        errors.append("план: не удалось извлечь ни одного валидного файла — apply заблокирован")

    return plan_files, errors


def parse_plan_from_file(report_path: Path, config_src: str = "",
                         project_root: Path = None) -> tuple[list[str], list[str]]:
    """Разобрать apply-план из файла 06_change_report.md.

    Возвращает: (plan_files, errors)
    """
    if not report_path.exists():
        return [], [f"план: файл не найден: {report_path}"]
    try:
        text = report_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return [], [f"план: ошибка чтения файла: {e}"]
    return parse_plan_from_report(text, config_src, project_root)


def normalize_files_for_powershell(config_src: str, plan_files: list[str]) -> list[str]:
    """Убрать префикс configSrc из путей для передачи как -Files в db-load-xml.ps1.

    Путь в change report: projects/collector/src/Documents/X/Ext/ObjectModule.bsl
    configSrc:             projects/collector/src
    Files для PowerShell:  Documents/X/Ext/ObjectModule.bsl
    """
    prefix = config_src.replace("\\", "/").rstrip("/") + "/"
    normalized = []
    for p in plan_files:
        p_norm = p.replace("\\", "/")
        if p_norm.startswith(prefix):
            normalized.append(p_norm[len(prefix):])
        else:
            normalized.append(p_norm)
    return normalized
