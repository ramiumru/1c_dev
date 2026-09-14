#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_plan_parser.py — регрессионные тесты общего парсера apply-плана.

Проверяет:
- Положительный сценарий: 23 файла + маска вне раздела → ровно 23
- Отрицательные сценарии: *, **, ?, .., абсолютные пути, дубликаты, пустой раздел, etc.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from plan_parser import parse_plan_from_report, parse_plan_from_file, normalize_files_for_powershell

CONFIG_SRC = "projects/example/src"
PROJECT_ROOT = None  # Не проверяем существование файлов в этих тестах


def make_report(plan_files: list[str], extra_sections: str = "") -> str:
    """Создать синтетический 06_change_report.md."""
    plan_lines = "\n".join(f"- {p} — описание" for p in plan_files)
    return f"""# Отчёт об изменениях: TASK-TEST

```yaml
scope_hash: abc123
spec_version: 1
```

## Изменённые файлы
{plan_lines}

## Реализованная логика
{extra_sections}

## Что проверить вручную
Проверить форму документа.
"""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    results = []

    # === Положительный сценарий: 23 файла + маска вне раздела ===
    files_23 = [f"projects/example/src/Documents/Doc{i}/Ext/ObjectModule.bsl" for i in range(1, 24)]
    extra = """Реализация охватывает все файлы в projects/example/src/**
Также изменён projects/example/src/Catalogs/Cat1/Ext/ObjectModule.bsl в контексте.
Команда: python scripts/safe_apply.py --task TASK-TEST --db test"""
    report = make_report(files_23, extra)
    plan, errors = parse_plan_from_report(report, CONFIG_SRC, PROJECT_ROOT)
    results.append(("positive: ровно 23 файла", len(plan) == 23, f"got={len(plan)}"))
    results.append(("positive: нет ошибок", len(errors) == 0, f"errors={errors[:2]}"))
    results.append(("positive: маска из описания не в плане",
                     "projects/example/src/**" not in plan, ""))
    results.append(("positive: путь из описания не добавлен повторно",
                     plan.count("projects/example/src/Catalogs/Cat1/Ext/ObjectModule.bsl") == 0, ""))
    # Путь из описания не в плане (он не в списке изменённых файлов)
    results.append(("positive: порядок сохранён", plan == files_23, f"mismatch at first diff"))

    # === Отрицательные сценарии ===

    # 1. ** внутри раздела
    report_bad1 = make_report(["projects/example/src/**/*.bsl"])
    _, errs = parse_plan_from_report(report_bad1, CONFIG_SRC, PROJECT_ROOT)
    results.append(("negative: ** в пути заблокирован", len(errs) > 0, f"errors={errs[:1]}"))

    # 2. * внутри пути
    report_bad2 = make_report(["projects/example/src/Documents/Doc*/Ext/ObjectModule.bsl"])
    _, errs = parse_plan_from_report(report_bad2, CONFIG_SRC, PROJECT_ROOT)
    results.append(("negative: * в пути заблокирован", len(errs) > 0, f"errors={errs[:1]}"))

    # 3. ? внутри пути
    report_bad3 = make_report(["projects/example/src/Documents/Doc?/Ext/ObjectModule.bsl"])
    _, errs = parse_plan_from_report(report_bad3, CONFIG_SRC, PROJECT_ROOT)
    results.append(("negative: ? в пути заблокирован", len(errs) > 0, f"errors={errs[:1]}"))

    # 4. .. в пути
    report_bad4 = make_report(["projects/example/src/../other/file.bsl"])
    _, errs = parse_plan_from_report(report_bad4, CONFIG_SRC, PROJECT_ROOT)
    results.append(("negative: .. в пути заблокирован", len(errs) > 0, f"errors={errs[:1]}"))

    # 5. Абсолютный Windows путь
    report_bad5 = make_report(["C:\\Projects\\file.bsl"])
    # Note: C:\... doesn't start with projects/ so list_item_re won't match
    # But if someone writes it in the section
    report_bad5_text = f"""# Report

## Изменённые файлы
- C:\\Projects\\file.bsl — описание

## Other
"""
    _, errs = parse_plan_from_report(report_bad5_text, CONFIG_SRC, PROJECT_ROOT)
    # C:\... doesn't start with "projects/" so it won't be extracted at all → empty plan
    results.append(("negative: Windows абсолютный путь не извлекается", len(errs) > 0, f"errors={errs[:1]}"))

    # 6. Абсолютный POSIX путь
    report_bad6 = f"""# Report

## Изменённые файлы
- /home/user/file.bsl — описание

## Other
"""
    _, errs = parse_plan_from_report(report_bad6, CONFIG_SRC, PROJECT_ROOT)
    results.append(("negative: POSIX абсолютный путь не извлекается", len(errs) > 0, f"errors={errs[:1]}"))

    # 7. Файл вне configSrc
    report_bad7 = make_report(["projects/other/src/file.bsl"])
    _, errs = parse_plan_from_report(report_bad7, CONFIG_SRC, PROJECT_ROOT)
    results.append(("negative: файл вне configSrc заблокирован", len(errs) > 0, f"errors={errs[:1]}"))

    # 8. Несуществующий файл (с project_root)
    with tempfile.TemporaryDirectory() as td:
        tdpath = Path(td)
        (tdpath / "projects" / "example" / "src" / "Documents").mkdir(parents=True, exist_ok=True)
        report_bad8 = make_report(["projects/example/src/Documents/NonExistent/Ext/ObjectModule.bsl"])
        _, errs = parse_plan_from_report(report_bad8, CONFIG_SRC, tdpath)
        results.append(("negative: несуществующий файл заблокирован", len(errs) > 0, f"errors={errs[:1]}"))

    # 9. Дубликат
    report_bad9 = make_report([
        "projects/example/src/Documents/Doc1/Ext/ObjectModule.bsl",
        "projects/example/src/Documents/Doc1/Ext/ObjectModule.bsl",
    ])
    _, errs = parse_plan_from_report(report_bad9, CONFIG_SRC, PROJECT_ROOT)
    results.append(("negative: дубликат заблокирован", len(errs) > 0, f"errors={errs[:1]}"))

    # 10. Пустой раздел
    report_bad10 = """# Report

## Изменённые файлы

## Other
"""
    _, errs = parse_plan_from_report(report_bad10, CONFIG_SRC, PROJECT_ROOT)
    results.append(("negative: пустой раздел заблокирован", len(errs) > 0, f"errors={errs[:1]}"))

    # 11. Отсутствующий раздел
    report_bad11 = """# Report

## Реализованная логика
Что-то сделано.
"""
    _, errs = parse_plan_from_report(report_bad11, CONFIG_SRC, PROJECT_ROOT)
    results.append(("negative: отсутствующий раздел заблокирован", len(errs) > 0, f"errors={errs[:1]}"))

    # 12. Следующий заголовок ## корректно завершает чтение
    report_ok12 = f"""# Report

## Изменённые файлы
- projects/example/src/Documents/Doc1/Ext/ObjectModule.bsl — описание

## Реализованная логика
- projects/example/src/Documents/Doc2/Ext/ObjectModule.bsl — не должен попасть в план
"""
    plan, errs = parse_plan_from_report(report_ok12, CONFIG_SRC, PROJECT_ROOT)
    results.append(("negative: заголовок ## завершает список", len(plan) == 1, f"got={len(plan)}"))
    results.append(("negative: путь из следующего раздела не в плане", len(errs) == 0, f"errors={errs[:1]}"))

    # 13. Похожий путь в произвольном описательном тексте не попадает в план
    report_ok13 = f"""# Report

## Изменённые файлы
- projects/example/src/Documents/Doc1/Ext/ObjectModule.bsl — описание

## Реализованная логика
Изменения затрагивают projects/example/src/** и конкретно
projects/example/src/Documents/Doc99/Ext/ObjectModule.bsl в контексте миграции.
Команда: python scripts/safe_apply.py --task TASK-TEST --db test --files projects/example/src/Documents/Doc1/Ext/ObjectModule.bsl
"""
    plan, errs = parse_plan_from_report(report_ok13, CONFIG_SRC, PROJECT_ROOT)
    results.append(("positive: описательный путь не в плане", len(plan) == 1, f"got={len(plan)}"))
    results.append(("positive: описательный путь без ошибок", len(errs) == 0, f"errors={errs[:1]}"))

    # 14. Backtick path support
    report_backtick = f"""# Report

## Изменённые файлы
- `projects/example/src/Documents/Doc1/Ext/ObjectModule.bsl` — описание

## Other
"""
    plan, errs = parse_plan_from_report(report_backtick, CONFIG_SRC, PROJECT_ROOT)
    results.append(("positive: backtick path извлекается", len(plan) == 1, f"got={len(plan)}"))

    # Print results
    print("=== Plan Parser Regression Tests ===")
    passed = 0
    failed = 0
    for name, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: {name} {detail}")
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\nResult: {passed} passed, {failed} failed out of {len(results)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
