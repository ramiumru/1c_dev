---
name: form-validate
description: Валидация управляемой формы 1С. Используй после создания или модификации формы для проверки корректности. При наличии BaseForm автоматически проверяет callType и ID расширений
argument-hint: <FormPath> [-Detailed] [-MaxErrors 30]
allowed-tools:
  - Bash
  - Read
  - Glob
---

# /form-validate — валидация управляемой формы 1С

Проверяет Form.xml на структурные ошибки: уникальность ID, наличие companion-элементов, корректность ссылок DataPath и команд.

Дополнительно проверяет структуру каталогов формы: модуль формы (`Module.bsl`) должен находиться в `Ext/Form/Module.bsl`, а не в `Ext/Module.bsl`.

## Параметры

| Параметр  | Обяз. | Умолч. | Описание                                |
|-----------|:-----:|---------|-----------------------------------------|
| FormPath  | да    | —       | Путь к файлу Form.xml или к каталогу формы |
| Detailed  | нет   | —       | Подробный вывод (все проверки, включая успешные) |
| MaxErrors | нет   | 30      | Остановиться после N ошибок              |

## Команда

```powershell
powershell.exe -NoProfile -File "{{SKILL_DIR}}/scripts/form-validate.ps1" -FormPath "Catalogs/Номенклатура/Forms/ФормаЭлемента"
powershell.exe -NoProfile -File "{{SKILL_DIR}}/scripts/form-validate.ps1" -FormPath "src/МояОбработка/Forms/Форма/Ext/Form.xml"
```

## Проверка структуры каталогов

Перед валидацией содержимого Form.xml рекомендуется проверить структуру каталогов формы отдельным скриптом:

```powershell
python "{{SKILL_DIR}}/scripts/check-form-structure.py" -FormPath "src/МояОбработка/Forms/Форма"
python "{{SKILL_DIR}}/scripts/check-form-structure.py" -FormPath "src/МояОбработка/Forms/Форма/Ext/Form.xml"
```

Скрипт проверяет:
- наличие `Ext/Form.xml` (определение формы);
- наличие `Ext/Form/Module.bsl` (модуль формы в правильном месте);
- отсутствие `Ext/Module.bsl` (модуль в неверном месте — частая ошибка).

Возвращает ненулевой код выхода и сообщение об ошибке, если структура неверна. Поддерживает проверку как для форм обработок, так и для форм объектов конфигурации.

Ожидаемая структура:

```
Forms/<ИмяФормы>/
└── Ext/
    ├── Form.xml       (определение формы)
    └── Form/
        └── Module.bsl (модуль формы)
```

