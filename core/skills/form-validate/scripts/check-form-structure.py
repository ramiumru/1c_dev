#!/usr/bin/env python3
# check-form-structure v1.0 — Check 1C managed form directory structure
# Verifies that Module.bsl is located at Ext/Form/Module.bsl (not Ext/Module.bsl)
# and that Ext/Form.xml exists.
#
# Usage:
#   python check-form-structure.py -FormPath "<path-to-form-dir-or-Form.xml>"
#   python check-form-structure.py -FormPath "src/МояОбработка/Forms/Форма"
#   python check-form-structure.py -FormPath "src/МояОбработка/Forms/Форма/Ext/Form.xml"

import argparse
import os
import sys


def resolve_form_dir(form_path: str) -> str:
    """Resolve the form root directory from a path that may point to either
    the form directory itself or the Ext/Form.xml file."""
    p = os.path.normpath(form_path)
    if os.path.isfile(p):
        # Could be Ext/Form.xml or Forms/Форма.xml
        base = os.path.basename(p)
        if base.lower() == "form.xml":
            # Ext/Form.xml -> form dir is two levels up
            return os.path.dirname(os.path.dirname(p))
        # Otherwise treat parent as form dir
        return os.path.dirname(p)
    # It's a directory — assume it's the form dir
    return p


def check_structure(form_dir: str) -> list:
    """Return list of error messages. Empty list means OK."""
    errors = []

    ext_dir = os.path.join(form_dir, "Ext")
    if not os.path.isdir(ext_dir):
        errors.append(
            "Каталог 'Ext/' не найден: {0}".format(ext_dir)
        )
        return errors

    # 1. Ext/Form.xml must exist
    form_xml = os.path.join(ext_dir, "Form.xml")
    if not os.path.isfile(form_xml):
        errors.append(
            "Файл 'Ext/Form.xml' не найден: {0}".format(form_xml)
        )

    # 2. Module.bsl must be at Ext/Form/Module.bsl, NOT at Ext/Module.bsl
    correct_module = os.path.join(ext_dir, "Form", "Module.bsl")
    wrong_module = os.path.join(ext_dir, "Module.bsl")

    if not os.path.isfile(correct_module):
        errors.append(
            "Модуль формы не найден в правильном месте 'Ext/Form/Module.bsl': {0}".format(
                correct_module
            )
        )

    if os.path.isfile(wrong_module):
        errors.append(
            "ОШИБКА: модуль формы находится в неверном месте 'Ext/Module.bsl' "
            "(должен быть 'Ext/Form/Module.bsl'): {0}".format(wrong_module)
        )

    return errors


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Check 1C managed form directory structure",
        allow_abbrev=False,
    )
    parser.add_argument(
        "-FormPath", "-Path", required=True,
        help="Путь к каталогу формы или к файлу Ext/Form.xml",
    )
    args = parser.parse_args()

    form_dir = resolve_form_dir(args.FormPath)

    if not os.path.isdir(form_dir):
        print("ОШИБКА: каталог формы не найден: {0}".format(form_dir))
        sys.exit(2)

    errors = check_structure(form_dir)

    if errors:
        print("Структура каталогов формы НЕВЕРНА: {0}".format(form_dir))
        print("Каталог формы: {0}".format(form_dir))
        for e in errors:
            print("  - {0}".format(e))
        print()
        print("Ожидаемая структура:")
        print("  Forms/<ИмяФормы>/")
        print("    └── Ext/")
        print("        ├── Form.xml       (определение формы)")
        print("        └── Form/")
        print("            └── Module.bsl (модуль формы)")
        sys.exit(1)
    else:
        print("OK: структура каталогов формы корректна: {0}".format(form_dir))
        sys.exit(0)


if __name__ == "__main__":
    main()
