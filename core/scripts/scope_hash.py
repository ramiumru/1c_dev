#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scope_hash.py — единый канонический алгоритм вычисления scope_hash.

Используется guard, validation, reviewer-процессом, документацией и шаблонами.
Единая реализация — не создавать несколько несовместимых.

Алгоритм:
  1. Из 03_solution_spec.md извлекаются секции «Границы изменения» и «Затрагиваемые файлы».
  2. Текст нормализуется детерминированно:
     - одинаковые переводы строк (LF);
     - обрезка концевых пробелов;
     - стабильный порядок файлов (сортировка);
     - исключение служебных полей (yaml-блок status/risk/scope_hash/...).
  3. Вычисляется SHA-256.
  4. Результат — 64 шестнадцатеричных символа.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def _extract_section(text: str, header: str) -> str:
    """Извлечь содержимое секции по заголовку ## <header> до следующего ## или конца."""
    pattern = rf"^##\s*{re.escape(header)}\s*\r?\n(.*?)(?=^##\s|\Z)"
    m = re.search(pattern, text, re.S | re.M)
    if m:
        return m.group(1)
    return ""


def _normalize(text: str) -> str:
    """Нормализация текста: LF, обрезка пробелов, удаление пустых строк, сортировка строк."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Обрезка концевых пробелов + удаление markdown-разметки списков
    stripped = []
    for line in lines:
        line = line.rstrip()
        if line.strip():
            # Убираем маркеры списков для канонизации
            line = re.sub(r"^\s*[-*]\s+", "", line)
            stripped.append(line.strip())
    # Стабильный порядок (сортировка)
    stripped.sort()
    return "\n".join(stripped)


def compute_scope_hash(spec_text: str) -> str:
    """Вычислить SHA-256 от канонизированных секций «Границы изменения» + «Затрагиваемые файлы».

    Аргумент: полный текст 03_solution_spec.md.
    Возвращает: 64 hex-символа (пустая строка при ошибке).
    """
    if not spec_text:
        return ""
    # Исключить yaml-блок (служебные поля) из текста перед извлечением секций
    clean_text = re.sub(r"```yaml\s*\r?\n.*?```", "", spec_text, flags=re.S)

    boundaries = _extract_section(clean_text, "Границы изменения")
    affected = _extract_section(clean_text, "Затрагиваемые файлы")

    if not boundaries and not affected:
        return ""

    normalized = _normalize(boundaries) + "\n" + _normalize(affected)
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def compute_scope_hash_from_file(spec_path: Path) -> str:
    """Вычислить scope_hash из файла 03_solution_spec.md."""
    try:
        text = spec_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return compute_scope_hash(text)


def validate_hash_format(value: str) -> bool:
    """Проверить, что значение — 64 hex-символа."""
    if not value or len(value) != 64:
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False
