# Скиллы разработки (reference)

Доступны ~58 скиллов для анализа структуры, правки XML-метаданных, компиляции и валидации.
Применять через `skill` (загрузить SKILL.md) + `bash` (запустить powershell-скрипт). Скрипты
edit/compile пишут XML через .NET I/O (не через нативный `edit`) — безопаснее ручной правки.

| Категория | Скиллы | Когда |
|---|---|---|
| info (read-only) | meta-info, form-info, mxl-info, skd-info, xdto-info, role-info, subsystem-info, cf-info, cfe-diff | Анализ структуры вместо ручного read XML |
| meta | meta-edit, meta-compile, meta-decompile, meta-validate, meta-remove | Правка/создание/проверка объекта метаданных |
| form | form-edit, form-compile, form-decompile, form-add, form-remove, form-validate, form-patterns*, form-data-types* | Правка/создание/проверка управляемых форм |
| mxl | mxl-compile, mxl-decompile, mxl-validate | Макеты табличных документов |
| skd | skd-compile, skd-decompile, skd-edit, skd-validate | Схемы компоновки данных |
| role | role-compile, role-validate | Роли (создание/проверка прав) |
| subsystem | subsystem-compile, subsystem-edit, subsystem-validate | Подсистемы |
| xdto | xdto-compile, xdto-decompile, xdto-edit, xdto-validate | Пакеты XDTO |
| interface | interface-edit, interface-validate | Командный интерфейс подсистем |
| template | template-add, template-remove | Макеты объектов |
| help | help-add | Встроенная справка |
| support | support-edit | Переключение поддержки типовой конфигурации |
| cf | cf-edit, cf-validate | Свойства/состав всей конфигурации |
| cfe | cfe-borrow, cfe-patch-method, cfe-validate, cfe-init | Расширения (заимствование, перехват методов) |
| epf/erf | epf-init, epf-validate, erf-init, erf-validate*, epf-bsp-init*, epf-bsp-add-command* | Внешние обработки/отчёты (без сборки — epf-build/dump требуют БД) |

*reference-only (без bash) — загружаются через skill как инструкции.

Запрещены (DB-зависимые, нет `.v8-project.json`): epf-build, epf-dump, erf-build, erf-dump,
db-*, web-*.

## Порядок вызова
`skill` (загрузить SKILL.md нужного скилла для параметров/DSL) →
`bash` (`powershell.exe -NoProfile -File {{SKILLS_DIR}}/<skill>/scripts/<script>.ps1 <параметры>`).
ObjectPath — по `INSTRUCTIONS.md`, раздел «Маппинг типов 1С → пути».
После правки XML — `*-validate`.

В некоторых SKILL.md (напр. `form-validate`) указаны `python … <script>.py`-команды
(cross-platform fallback). Они НЕ входят в bash whitelist —
выполнять только `powershell.exe -NoProfile -File … <script>.ps1`-команды из SKILL.md.
Python-команды игнорировать; если функция `.py` не покрыта `.ps1` (напр.
`check-form-structure.py`) — проверить вручную или отметить в «Допущения / Что проверить».
