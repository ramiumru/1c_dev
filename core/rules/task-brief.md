# Обязательный Task-бриф (для 1c-do)

При каждом вызове Task передавать минимум:
- **Исполнитель** и **режим** (обычный / SDD / уточнение / summaries: `--objects` / `--scan` /
  `--scan --objects` / `--subsystem` / `--force` / apply: SDD / direct / Full / quick-fix / docs-fix /
  artifact).
- **Intent** — режим использования по требуемому результату (`analysis` / `artifact` /
  `implementation`): определяет, чем завершается flow (ответ / артефакт / полный SDD-flow).
  - `analysis` — анализ/консультация: src и БД не обязательны (MCP-only допустим); ответ
    возвращается пользователю; developer/reviewer/applier не запускаются.
  - `artifact` — совет-артефакт (текст запроса 1С / пример BSL / алгоритм; пользователь выполнит
    сам): передавать с исполнителем флаг `artifact-only: true`. Исполнитель (analyst по
    умолчанию; developer для BSL/запрос-специфики) работает read-only: анализирует и возвращает
    артефакт в ответе; НЕ редактирует `projects/**/src/**`; НЕ запускает apply; отсутствие
    writable source — не ошибка.
  - `implementation` — правка конфигурации: полный существующий flow (SDD для нетривиальных,
    triage для тривиальных); требуется writable локальный source.
- **Проект** (определён на шаге 1.5) — логический проект (напр. `finance`). Per-project данные
  исполнители читают в `{{CONTEXT_DIR}}/projects/<проект>/` (context.md, objects-index.md,
  summaries/, requirements/, analyst-scope.md).
- **Источники** — список из раздела «Источники» `context.md` (напр. `[finance, extfinance]`).
  Исполнители читают `projects/<источник>/src/**` для **каждого** источника из списка.
- **Sources** — доступные MCP sources (`metadata`/`code`/`platform_help`/`standards`) из
  машиночитаемого блока `sources` в `context.md` (для приоритета поиска; политика —
  `{{CONTEXT_DIR}}/rules/project-sources.md`). Блока нет → все источники local-only.
- **read-only** — `true`, если проект в MCP-only/read-only режиме (нет writable workspace:
  `local.enabled: false` или `projects/<источник>/src/**` отсутствует). В этом режиме анализ/spec
  разрешены, фактическая правка исходников запрещена.
- **Исходный запрос пользователя — дословно**, без искажений и пересказа.
- **TASK-ID** (в SDD и при работе с task-папкой).
- **Путь task-папки** `specs/<TASK-ID>/` и что именно прочитать/заполнить (в SDD).
- Для `1c-tools` — **режим** + список объектов (для `--objects`/`--scan --objects` — канонические
  заголовки `Тип.Имя` дословно; для `--subsystem` — имя подсистемы; для `--scan`/`--force` — без списка).
- Для `1c-applier` — **режим apply** (SDD: TASK-ID + путь task-папки, апликер читает
  `06_change_report.md`; direct: список относительных путей; Full: явный флаг подтверждения от
  пользователя) + **id/alias БД**, если разрешён на шаге 6.6.

Неполный бриф → субагент додумывает: не допускать.
