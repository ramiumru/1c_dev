#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bsl-check.py — структурная проверка BSL-модулей после правок.
Запуск: python scripts/bsl-check.py <путь_к_файлу.bsl>
        python scripts/bsl-check.py <путь_к_каталогу>  (рекурсивно по *.bsl)

Проверки (без парсера BSL — простая эвристика по строкам):
  1. Кодировка UTF-8 BOM (первые 3 байта = EF BB BF)
  2. Переводы строк CRLF (отсутствие lone LF — \n без \r)
  3. Баланс парных конструкций:
     - Если ... КонецЕсли  (с учётом ИначеЕсли/Иначе)
     - Процедура ... КонецПроцедуры
     - Функция ... КонецФункции
     - Цикл ... КонецЦикла  (Для/Пока ... Цикл ... КонецЦикла)
     Перед сопоставлением ключевых слов удаляются inline-комментарии (// ...) и
     строковые литералы ("..."), чтобы предотвратить ложные срабатывания на ключевых
     словах внутри комментариев и строк. Ограничение: не учитываются экранированные
     кавычки (удвоенные двойные кавычки) внутри строковых литералов — это известное
     ограничение эвристики.
  4. Баланс маркеров изменений: // ++ #TASK-... / // -- #TASK-...
  5. Код вне процедур/функций (orphaned) — строки-утверждения вне области процедуры/функции,
     кроме областей (#Область/#КонецОбласти), комментариев (//), пустых строк и директив (&НаКлиенте и т.п.)

Ограничение: скрипт выполняет только эвристическую проверку по строкам, без полноценного
BSL-парсера. Возможны ложные срабатывания в сложных случаях (многострочные строки,
экранированные кавычки, препроцессорные директивы). Для точной проверки используйте
BSL Language Server.

Exit codes:
  0 — проверка пройдена (или warnings только)
  1 — найдены ERROR (критические нарушения баланса/кодировки)
  2 — файл не найден / не .bsl / ошибка чтения

Вывод: stdout — список найденных проблем (ERROR/WARN) + итоговый счётчик.
Формат: <LEVEL> <файл>:<строка>: <описание>
"""

import sys
import os
import re
import glob

# === Константы ===

BOM = b'\xef\xbb\xbf'

# Регэкспы (case-insensitive для BSL ключевых слов)
RE_PROC_START = re.compile(r'^\s*(Процедура|Procedure)\s+', re.IGNORECASE)
RE_PROC_END = re.compile(r'^\s*(КонецПроцедуры|EndProcedure)\s*;?\s*$', re.IGNORECASE)
RE_FUNC_START = re.compile(r'^\s*(Функция|Function)\s+', re.IGNORECASE)
RE_FUNC_END = re.compile(r'^\s*(КонецФункции|EndFunction)\s*;?\s*$', re.IGNORECASE)
RE_IF_START = re.compile(r'^\s*(Если|If)\s+', re.IGNORECASE)
RE_IF_END = re.compile(r'^\s*(КонецЕсли|EndIf)\s*;?\s*$', re.IGNORECASE)
RE_LOOP_START = re.compile(r'\s(Цикл|Loop)\s*$', re.IGNORECASE)
RE_LOOP_END = re.compile(r'^\s*(КонецЦикла|EndDo)\s*;?\s*$', re.IGNORECASE)
RE_REGION = re.compile(r'^\s*#Область\s+', re.IGNORECASE)
RE_REGION_END = re.compile(r'^\s*#КонецОбласти\s*$', re.IGNORECASE)
RE_DIRECTIVE = re.compile(r'^\s*&', re.IGNORECASE)
RE_COMMENT = re.compile(r'^\s*(//|$)')
RE_TASK_OPEN = re.compile(r'//\s*\+\+\s*#', re.IGNORECASE)
RE_TASK_CLOSE = re.compile(r'//\s*--\s*#', re.IGNORECASE)
RE_VAR_DECL = re.compile(r'^\s*(Перем|Var)\s+', re.IGNORECASE)

# Регэксп для удаления inline-комментариев: всё после // (не внутри строки)
RE_INLINE_COMMENT = re.compile(r'(?<!")//.*$')
# Регэксп для удаления строковых литералов: "..." (простая эвристика, не учитывает экранирование кавычек)
RE_STRING_LIT = re.compile(r'"[^"]*"')


def strip_comments_and_strings(line: str) -> str:
    """Удалить inline-комментарии и заменить строковые литералы пустыми строками.
    
    Предотвращает ложные срабатывания: ключевые слова BSL (Цикл, Если, Процедура и т.д.)
    внутри комментариев и строковых литералов не должны учитываться при проверке баланса.
    
    Ограничение: не учитывает экранированные кавычки внутри строк (удвоенные кавычки).
    Это известное ограничение эвристического подхода без полноценного BSL-парсера.
    """
    # Заменяем строковые литералы на пустые
    result = RE_STRING_LIT.sub('""', line)
    # Удаляем inline-комментарии
    result = RE_INLINE_COMMENT.sub('', result)
    return result


def check_file(filepath):
    """Проверка одного .bsl-файла. Возвращает список (level, line, msg)."""
    findings = []

    if not os.path.isfile(filepath):
        return [('ERROR', 0, 'файл не найден')]

    # Чтение в бинарном виде для проверки BOM
    with open(filepath, 'rb') as f:
        raw = f.read()

    # 1. BOM
    if not raw.startswith(BOM):
        findings.append(('ERROR', 1, 'отсутствует BOM (UTF-8 с BOM обязателен для BSL-модулей)'))

    # 2. CRLF — поиск lone LF (\n без предшествующего \r)
    # Нормализуем: считаем \r\n и \n без \r
    text = raw.decode('utf-8-sig', errors='replace')  # utf-8-sig съедает BOM
    lines = text.split('\n')
    lone_lf = 0
    for i, line in enumerate(lines):
        if line.endswith('\r'):
            # нормальный CRLF (после split по \n остался \r в конце)
            pass
        else:
            # нет \r — это lone LF (если строка не последняя пустая)
            if i < len(lines) - 1 or line.strip():
                lone_lf += 1
    if lone_lf > 0:
        findings.append(('WARN', 0, f'найдено {lone_lf} строк с lone LF (ожидается CRLF)'))

    # Подготовка строк без \r для дальнейших проверок
    clean_lines = [line.rstrip('\r') for line in lines]

    # 3. Баланс парных конструкций
    # Простой подход: считаем открывающие/закрывающие, не вкладываем (модель ошибается редко)
    depth_if = 0
    depth_proc = 0
    depth_func = 0
    depth_loop = 0
    depth_region = 0
    in_proc_or_func = 0  # для orphaned-детекта

    for i, line in enumerate(clean_lines, 1):
        stripped = line.strip()

        # Полные строки-комментарии пропускаем для баланса конструкций
        if RE_COMMENT.search(line):
            continue

        # Очищаем от inline-комментариев и строковых литералов для проверки ключевых слов
        code_line = strip_comments_and_strings(line)
        code_stripped = code_line.strip()

        # Области
        if RE_REGION.search(line):
            depth_region += 1
            continue
        if RE_REGION_END.search(line):
            depth_region = max(0, depth_region - 1)
            continue

        # Процедуры
        if RE_PROC_START.search(code_line):
            depth_proc += 1
            in_proc_or_func += 1
            continue
        if RE_PROC_END.search(code_line):
            depth_proc -= 1
            in_proc_or_func = max(0, in_proc_or_func - 1)
            if depth_proc < 0:
                findings.append(('ERROR', i, 'КонецПроцедуры без открывающей Процедура'))
            continue

        # Функции
        if RE_FUNC_START.search(code_line):
            depth_func += 1
            in_proc_or_func += 1
            continue
        if RE_FUNC_END.search(code_line):
            depth_func -= 1
            in_proc_or_func = max(0, in_proc_or_func - 1)
            if depth_func < 0:
                findings.append(('ERROR', i, 'КонецФункции без открывающей Функция'))
            continue

        # Если (внутри процедур/функций — но считаем глобально для простоты)
        if RE_IF_START.search(code_line):
            depth_if += 1
        if RE_IF_END.search(code_line):
            depth_if -= 1
            if depth_if < 0:
                findings.append(('ERROR', i, 'КонецЕсли без открывающей Если'))

        # Цикл (Для ... Цикл / Пока ... Цикл)
        if RE_LOOP_START.search(code_line):
            depth_loop += 1
        if RE_LOOP_END.search(code_line):
            depth_loop -= 1
            if depth_loop < 0:
                findings.append(('ERROR', i, 'КонецЦикла без открывающего Цикл'))

    # Итоговый баланс
    if depth_if != 0:
        findings.append(('ERROR', 0, f'дисбаланс Если/КонецЕсли: {depth_if}'))
    if depth_proc != 0:
        findings.append(('ERROR', 0, f'дисбаланс Процедура/КонецПроцедуры: {depth_proc}'))
    if depth_func != 0:
        findings.append(('ERROR', 0, f'дисбаланс Функция/КонецФункции: {depth_func}'))
    if depth_loop != 0:
        findings.append(('ERROR', 0, f'дисбаланс Цикл/КонецЦикла: {depth_loop}'))
    if depth_region != 0:
        findings.append(('ERROR', 0, f'дисбаланс #Область/#КонецОбласти: {depth_region}'))

    # 4. Баланс маркеров изменений
    task_open = 0
    task_close = 0
    for i, line in enumerate(clean_lines, 1):
        if RE_TASK_OPEN.search(line):
            task_open += 1
        if RE_TASK_CLOSE.search(line):
            task_close += 1
    if task_open != task_close:
        findings.append(('WARN', 0, f'дисбаланс маркеров // ++ #TASK (-- #TASK): open={task_open}, close={task_close}'))

    # 5. Orphaned-код (утверждения вне процедур/функций)
    # Проходим заново, отслеживая in_proc_or_func
    in_proc_or_func_local = 0
    depth_region_local = 0
    for i, line in enumerate(clean_lines, 1):
        stripped = line.strip()

        if RE_REGION.search(line):
            depth_region_local += 1
            continue
        if RE_REGION_END.search(line):
            depth_region_local = max(0, depth_region_local - 1)
            continue

        if RE_PROC_START.search(line) or RE_FUNC_START.search(line):
            in_proc_or_func_local += 1
            continue
        if RE_PROC_END.search(line) or RE_FUNC_END.search(line):
            in_proc_or_func_local = max(0, in_proc_or_func_local - 1)
            continue

        # Если не внутри процедуры/функции
        if in_proc_or_func_local == 0:
            # Разрешённые строки вне процедур: комментарии, пустые, директивы, Перем, области
            if not stripped:
                continue
            if RE_COMMENT.search(line):
                continue
            if RE_DIRECTIVE.search(line):
                continue
            if RE_VAR_DECL.search(line):
                continue
            # Всё остальное — подозрительный orphaned-код
            findings.append(('WARN', i, f'код вне процедуры/функции (orphaned): {stripped[:60]}'))

    return findings


def main():
    # Обработка --help/-h до интерпретации аргумента как пути.
    args = sys.argv[1:]
    if not args or any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return 0

    if len(args) > 1:
        print(f"Ошибка: лишние аргументы. Ожидается один путь (файл или каталог).", file=sys.stderr)
        print("Использование: python scripts/bsl-check.py <файл.bsl | каталог>", file=sys.stderr)
        return 2

    path = args[0]
    files = []

    if os.path.isfile(path):
        if path.lower().endswith('.bsl'):
            files = [path]
        else:
            print(f"ERROR: файл не .bsl: {path}")
            return 2
    elif os.path.isdir(path):
        for root, _, names in os.walk(path):
            for name in names:
                if name.lower().endswith('.bsl'):
                    files.append(os.path.join(root, name))
        if not files:
            print(f"ERROR: в каталоге нет .bsl-файлов: {path}")
            return 2
    else:
        print(f"ERROR: путь не найден: {path}")
        return 2

    total_errors = 0
    total_warnings = 0
    total_files = 0

    for filepath in sorted(files):
        total_files += 1
        findings = check_file(filepath)
        has_errors = False
        has_warnings = False
        for level, line, msg in findings:
            prefix = f"{level} {filepath}"
            if line > 0:
                prefix += f":{line}"
            print(f"{prefix}: {msg}")
            if level == 'ERROR':
                total_errors += 1
                has_errors = True
            elif level == 'WARN':
                total_warnings += 1
                has_warnings = True
        if not findings:
            print(f"OK {filepath}")

    print(f"\nИтог: файлов={total_files}, ERROR={total_errors}, WARN={total_warnings}")

    if total_errors > 0:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
