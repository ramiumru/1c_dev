---
name: form-add
description: Добавить пустую управляемую форму к объекту 1С. Используй когда нужно создать у объекта новую форму
argument-hint: <ObjectPath> <FormName> [Purpose] [--set-default]
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
---

# /form-add — Добавление формы к объекту конфигурации

Создаёт управляемую форму (metadata XML + Form.xml + Module.bsl) и регистрирует её в корневом XML объекта конфигурации (Document, Catalog, InformationRegister и др.).

## Usage

```
/form-add <ObjectPath> <FormName> [Purpose] [Synonym] [--set-default]
```

| Параметр    | Обязательный | По умолчанию | Описание                                     |
|-------------|:------------:|--------------|----------------------------------------------|
| ObjectPath  | да           | —            | Путь к XML-файлу объекта (Documents/Док.xml)  |
| FormName    | да           | —            | Имя формы (ФормаДокумента)                    |
| Purpose     | нет          | Object       | Назначение: Object, List, Choice, Record      |
| Synonym     | нет          | = FormName   | Синоним формы                                 |
| --set-default | нет        | авто         | Установить как форму по умолчанию             |

## Команда

```powershell
powershell.exe -NoProfile -File "{{SKILL_DIR}}/scripts/form-add.ps1" -ObjectPath "<ObjectPath>" -FormName "<FormName>" [-Purpose "<Purpose>"] [-Synonym "<Synonym>"] [-SetDefault]
```

## Purpose — назначение формы

| Purpose | Допустимые типы объектов | Основной реквизит | DefaultForm-свойство |
|---------|-------------------------|-------------------|---------------------|
| Object  | Document, Catalog, DataProcessor, Report, ExternalDataProcessor, ExternalReport, ChartOf*, ExchangePlan, BusinessProcess, Task | Объект (тип: *Object.Имя) | DefaultObjectForm (DefaultForm для DataProcessor/Report/ExternalDataProcessor/ExternalReport) |
| List    | Все кроме DataProcessor | Список (DynamicList) | DefaultListForm |
| Choice  | Document, Catalog, ChartOf*, ExchangePlan, BusinessProcess, Task | Список (DynamicList) | DefaultChoiceForm |
| Record  | InformationRegister | Запись (InformationRegisterRecordManager) | DefaultRecordForm |

## Примеры

```
# Форма документа
/form-add Documents/АвансовыйОтчет.xml ФормаДокумента --purpose Object

# Форма списка каталога
/form-add Catalogs/Контрагенты.xml ФормаСписка --purpose List

# Форма записи регистра сведений
/form-add InformationRegisters/КурсыВалют.xml ФормаЗаписи --purpose Record

# Форма выбора с синонимом
/form-add Catalogs/Номенклатура.xml ФормаВыбора --purpose Choice --synonym "Выбор номенклатуры"

# Установить как форму по умолчанию
/form-add Documents/Заказ.xml ФормаДокументаНовая --purpose Object --set-default
```

## Структура каталогов формы

`/form-add` создаёт каркас формы со следующей структурой:

```
Forms/Форма/
├── Форма.xml          (метаданные формы)
└── Ext/
    ├── Form.xml       (определение формы — пустой каркас)
    └── Form/          (подкаталог для модуля)
        └── Module.bsl (модуль формы)
```

> ⚠️ **Неверно**: `Forms/Форма/Ext/Module.bsl` — модуль в корне `Ext/`.
> ✅ **Верно**: `Forms/Форма/Ext/Form/Module.bsl` — модуль внутри подкаталога `Form/`.

Модуль формы **обязательно** располагается в подкаталоге `Form/` внутри `Ext/`, а не непосредственно в `Ext/`. Это соответствует формату XML-исходников 1С (`DumpConfigToFiles` / `LoadConfigFromFiles`).

## Workflow

1. `/form-add` — создать каркас формы
2. `/form-compile` или `/form-edit` — наполнить Form.xml элементами
3. `/form-validate` — проверить корректность
4. `/form-info` — проанализировать результат

## ⚠️ Важно: не редактируйте Form.xml вручную

`/form-add` создаёт **минимальный каркас** формы (пустой Form.xml).
Для добавления элементов (таблиц, полей, кнопок, команд) **всегда** используйте `/form-compile` или `/form-edit`.

Ручное редактирование Form.xml приводит к ошибке XDTO при сборке EPF из-за:
- Отсутствия обязательного `<Type>` внутри `<AdditionSource>` для дополнений таблицы
- Отсутствия `<RowFilter xsi:nil="true"/>` в таблицах
- Нарушения порядка элементов (схема XDTO требует строгий порядок)
- Неправильного регистра тегов (например, `<Autofill>` вместо `<AutoFill>`)
