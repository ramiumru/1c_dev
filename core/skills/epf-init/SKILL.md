---
name: epf-init
description: Создать пустую внешнюю обработку 1С (scaffold XML-исходников). Используй когда нужно создать новую внешнюю обработку с нуля
argument-hint: <Name> [Synonym]
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
---

# /epf-init — Создание новой обработки

Генерирует минимальный набор XML-исходников для внешней обработки 1С: корневой файл метаданных и каталог обработки.

## Usage

```
/epf-init <Name> [Synonym] [SrcDir]
```

| Параметр  | Обязательный | По умолчанию | Описание                            |
|-----------|:------------:|--------------|-------------------------------------|
| Name      | да           | —            | Имя обработки (латиница/кириллица)  |
| Synonym   | нет          | = Name       | Синоним (отображаемое имя)          |
| SrcDir    | нет          | `src`        | Каталог исходников относительно CWD |

## Команда

```powershell
powershell.exe -NoProfile -File "{{SKILL_DIR}}/scripts/init.ps1" -Name "<Name>" [-Synonym "<Synonym>"] [-SrcDir "<SrcDir>"]
```

## Дальнейшие шаги

- Добавить форму: `/form-add`
- Добавить макет: `/template-add`
- Добавить справку: `/help-add`
- Собрать EPF: `/epf-build`

## ⚠️ Критичные правила XDTO для Form.xml

При наполнении формы элементами **всегда** используй `/form-compile` или `/form-edit` — они генерируют корректный XML.
**Никогда не создавай Form.xml вручную** — это приводит к ошибке XDTO при сборке EPF.

### Обязательные элементы для таблиц (Table)

При создании таблицы (`Table`) в форме необходимо включать:

1. **`<RowFilter xsi:nil="true"/>`** — обязательный элемент, даже если фильтр строк не используется
2. **`<AdditionSource>` с `<Type>`** — для каждого дополнения (`SearchStringAddition`, `ViewStatusAddition`, `SearchControlAddition`) внутри `<AdditionSource>` должен быть элемент `<Type>`:
   - `<Type>SearchStringRepresentation</Type>` — для строки поиска
   - `<Type>ViewStatusRepresentation</Type>` — для состояния просмотра
   - `<Type>SearchControl</Type>` — для управления поиском

Пример правильной структуры:
```xml
<SearchStringAddition name="ТаблицаСтрокаПоиска" id="...">
    <AdditionSource>
        <Item>Таблица</Item>
        <Type>SearchStringRepresentation</Type>
    </AdditionSource>
    <ContextMenu .../>
    <ExtendedTooltip .../>
</SearchStringAddition>
```

### Порядок элементов в Table

Элементы внутри `<Table>` должны идти в строгом порядке (схема XDTO):
1. `Representation` (List/Tree)
2. `DataPath`
3. `RowFilter`
4. `ContextMenu`
5. `AutoCommandBar`
6. `ExtendedTooltip`
7. `SearchStringAddition`
8. `ViewStatusAddition`
9. `SearchControlAddition`
10. `ChildItems` (колонки)

### Регистр тегов

- `<AutoFill>` (не `<Autofill>`) — внутри `AutoCommandBar`
- `<Representation>None</Representation>` — для групп без рамки
