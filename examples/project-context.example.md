# Project context (пример/шаблон)

> Универсальный шаблон per-project `context.md`. Реальный файл лежит в
> `{{CONTEXT_DIR}}/projects/<проект>/context.md` и создаётся пользователем/`1c-analyst`.
> **Не содержит** корпоративных URL/UUID/credentials — только placeholders и env-переменные.
> Корпоративный профиль (напр. Finance) живёт только в локальной/корпоративной среде
> (gitignored `projects/`, overlay/, `.dev.env`), никогда в публичном репозитории.

## Домен

<краткое описание бизнес-домена проекта — напр. «Финансы: казначейство, расчёты с контрагентами»>

## Источники

- `finance` — основная конфигурация;
- `extfinance` — расширение (если есть).

Исходники: `projects/<источник>/src/**` для каждого источника из списка.

## Sources (maшиночитаемый блок)

```yaml
project: finance

sources:
  local:
    enabled: true
    path: projects/finance/src

  metadata:
    type: mcp
    enabled: false                       # true в корпоративной среде
    server: metadata                     # логическое имя MCP-сервера (регистрируется в kilo.json/openworks.json)
    project_id: ""                       # UUID конфигурации — задаётся в локальном overlay, не в публичном репо

  code:
    type: mcp
    enabled: false
    server: code                         # логическое имя MCP-сервера
    repositories:
      - finance
      - extfinance

  platform_help:
    type: mcp
    enabled: false
    server: platform_help                # логическое имя MCP-сервера

  standards:
    type: mcp
    enabled: false
    server: v8std                        # логическое имя MCP-сервера
```

> Поле `enabled: false` = источник недоступен (публичный репозиторий). Корпоративная среда
> выставляет `enabled: true` и регистрирует соответствующий MCP-сервер в конфигурации
> инструмента (`kilo.json`/`openworks.json`) под логическим именем (`metadata`, `code`,
> `platform_help`, `v8std`). Реальные корпоративные URL/UUID/credentials — **только** в
> локальной конфигурации (gitignored overlay/, `.dev.env`), никогда в публичном репозитории.
> Политика приоритета и graceful degradation — `{{CONTEXT_DIR}}/rules/project-sources.md`.

## Префиксы

- Префикс проекта: из `.dev.env` (поле `PREFIX`).

## Модули

<ключевые общие модули/подсистемы проекта>

## Ключевые объекты

<часто используемые объекты — детальный список в `analyst-scope.md`>

## База данных

Справочный кросс-референс к `.v8-project.json` (источник истины — реестр, не этот файл):
- id/alias: `<id из .v8-project.json>` (не источник истины — только навигация).
