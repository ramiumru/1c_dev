---
name: epf-validate
description: Валидация внешней обработки 1С (EPF). Используй после создания или модификации обработки для проверки корректности
argument-hint: <ObjectPath> [-Detailed] [-MaxErrors 30]
allowed-tools:
  - Bash
  - Read
  - Glob
---

# /epf-validate — валидация внешней обработки (EPF)

Проверяет структурную корректность XML-исходников внешней обработки: корневую структуру, InternalInfo, свойства, ChildObjects, реквизиты, табличные части, уникальность имён, наличие файлов форм и макетов. Также работает для внешних отчётов (ERF).

## Параметры

| Параметр   | Обяз. | Умолч. | Описание                                      |
|------------|:-----:|---------|-------------------------------------------------|
| ObjectPath | да    | —       | Путь к корневому XML или каталогу обработки     |
| Detailed   | нет   | —       | Подробный вывод (все проверки, включая успешные) |
| MaxErrors  | нет   | 30      | Остановиться после N ошибок                     |
| OutFile    | нет   | —       | Записать результат в файл (UTF-8 BOM)           |

## Команда

```powershell
powershell.exe -NoProfile -File "{{SKILL_DIR}}/scripts/epf-validate.ps1" -ObjectPath "src/МояОбработка"
powershell.exe -NoProfile -File "{{SKILL_DIR}}/scripts/epf-validate.ps1" -ObjectPath "src/МояОбработка/МояОбработка.xml"
```

## ⚠️ Ограничения валидатора

`/epf-validate` проверяет **структуру метаданных** (корневой XML, ChildObjects, реквизиты), но **не проверяет XDTO-совместимость Form.xml**.

Ошибки XDTO обнаруживаются только при сборке EPF (`/epf-build`) и приводят к сообщению:
> Ошибка загрузки документа. Исключение XDTO произошло при чтении файла ... Form.xml

### Частые причины ошибок XDTO в Form.xml

1. **Отсутствие `<Type>` в `<AdditionSource>`** — для `SearchStringAddition`, `ViewStatusAddition`, `SearchControlAddition` обязательно указывать `<Type>`:
   - `<Type>SearchStringRepresentation</Type>`
   - `<Type>ViewStatusRepresentation</Type>`
   - `<Type>SearchControl</Type>`
2. **Отсутствие `<RowFilter xsi:nil="true"/>`** в элементах `Table`
3. **Нарушение порядка элементов** — схема XDTO требует строгий порядок
4. **Неправильный регистр тегов** — например, `<Autofill>` вместо `<AutoFill>`

### Рекомендация

Для создания и редактирования Form.xml используйте `/form-compile` или `/form-edit` — они генерируют XDTO-совместимый XML.
Не редактируйте Form.xml вручную.

