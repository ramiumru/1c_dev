# 1C Dev — агентская схема разработки 1С

Мультиагентная схема для разработки конфигураций 1С:Предприятие 8.3 (BSL + XML-метаданные)
поверх AI-кодинг-ассистентов. Tool-agnostic ядро + адаптеры под конкретный инструмент.

## Поддерживаемые инструменты

| Инструмент | Установка | Режим | Механика |
|---|---|---|---|
| **Kilo** | `install.ps1 -Tool kilo` | Multi-agent | `.kilo/agents`, `.kilo/skills`, `kilo.json` |
| **Claude Code** | `install.ps1 -Tool claude` | Multi-agent | `.claude/agents`, `.claude/skills`, `CLAUDE.md` |
| **OpenCode** | `install.ps1 -Tool opencode` | Multi-agent | `.opencode/agents`, `.opencode/skills`, `opencode.json` |
| **Codex CLI** | `install.ps1 -Tool codex` | Single-agent | `AGENTS.md` + скрипты (одноагентный режим) |

## Архитектура

```
                    ┌──────────┐
        запрос ───▶ │  1c-do   │  маршрутизатор + SDD-оркестратор
                    └────┬─────┘
              ┌──────────┼──────────┬─────────────┐
              ▼          ▼          ▼             ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ 1c-      │ │ 1c-      │ │ 1c-      │ │ 1c-tools │
        │ analyst  │ │developer │ │ applier  │ │ summaries│
        └──────────┘ └──────────┘ └──────────┘ └──────────┘
          анализ        код BSL     применение   регенерация
          структуры     + XML       в живую ИБ   индексов
```

- **1c-do** — точка входа: определяет проект, классифицирует запрос, ведёт SDD, делегирует.
- **1c-analyst** — анализ конфигурации по XML/BSL-исходникам (без правки кода).
- **1c-developer** — правка BSL/XML строго по спецификации (gate «нет кода без spec»).
- **1c-applier** — применение правок в ИБ (db-load-xml → db-update, бэкап dt), только по явному запросу.
- **1c-tools** — исполнитель утилит (`build_summaries.py`), без самостоятельности.

### Spec-Driven Development (SDD)

Нетривиальные правки проходят через спецификацию:

```
00_request → 01_context → 03_solution_spec (GATE) → реализация → 05_test_scenarios → 06_change_report
```

TASK-ID маркирует и папку спецификации, и комментарии в коде (`// ++ #TASK-ID`).
Шаблоны SDD-артефактов — в `core/sdd/README.md`.

## Скиллы (78)

| Группа | Скиллы |
|---|---|
| Метаданные | meta-compile, meta-edit, meta-info, meta-validate, meta-remove, meta-decompile |
| Формы | form-compile, form-edit, form-info, form-validate, form-add, form-remove, form-decompile, form-patterns, form-data-types |
| Отчётность | skd-compile, skd-edit, skd-info, skd-validate, skd-decompile |
| Макеты | mxl-compile, mxl-decompile, mxl-info, mxl-validate |
| Роли | role-compile, role-info, role-validate |
| Подсистемы | subsystem-compile, subsystem-edit, subsystem-info, subsystem-validate |
| Конфигурация | cf-init, cf-edit, cf-info, cf-validate |
| Расширения | cfe-init, cfe-borrow, cfe-patch-method, cfe-diff, cfe-validate |
| Внешние обработки | epf-init, epf-build, epf-dump, epf-validate, epf-bsp-init, epf-bsp-add-command |
| Внешние отчёты | erf-init, erf-build, erf-dump, erf-validate |
| Информационные базы | db-create, db-list, db-run, db-update, db-dump-xml, db-dump-cf, db-dump-dt, db-load-xml, db-load-cf, db-load-dt, db-load-git |
| Веб | web-info, web-publish, web-stop, web-test, web-unpublish |
| XDTO | xdto-compile, xdto-decompile, xdto-edit, xdto-info, xdto-validate |
| Интерфейс | interface-edit, interface-validate |
| Прочее | img-grid, help-add, support-edit, template-add, template-remove |

Каждый скилл — `SKILL.md` (инструкция + параметры) + `scripts/*.ps1|py` (исполнение).
Пути к скриптам в `core/skills/` используют плейсхолдер `{{SKILL_DIR}}`, установщик
подставляет путь целевого инструмента.

## Структура репозитория

```
1c-dev/
├── core/                    — канонический источник (tool-agnostic)
│   ├── agents/              — 5 агентов: тела (без frontmatter)
│   ├── skills/              — 78 скиллов: <name>/{SKILL.md, scripts/}
│   ├── context/             — INSTRUCTIONS.md, BslChecklists.md, standards/, common/, projects/
│   ├── scripts/             — bsl-check.py, build_summaries.py
│   └── sdd/                 — README.md (SDD-шаблоны)
├── adapters/                — per-tool обвязка
│   ├── kilo/                — kilo.json.tpl + frontmatter/*.yml
│   ├── claude/              — CLAUDE.md.tpl + frontmatter/*.yml
│   ├── codex/               — AGENTS.md.tpl + config.toml.example
│   └── opencode/            — opencode.json.tpl + frontmatter/*.yml
├── install/                 — install.ps1 + миграционные скрипты
├── examples/                — v8-project.example.json
└── docs/                    — adding-skills.md
```

## Быстрый старт

1. Клонируйте репозиторий в рабочее пространство проекта 1С.
2. Установите схему под ваш инструмент:
   ```powershell
   powershell -File install/install.ps1 -Tool <kilo|claude|codex|opencode>
   ```
3. Создайте `.v8-project.json` из `examples/v8-project.example.json` (реестр баз; **НЕ коммитить**).
4. Разместите исходники конфигурации в `projects/<источник>/src/` (DumpConfigToFiles).
5. Первичный индекс:
   ```powershell
   python scripts/build_summaries.py --project <имя> --scan --context-dir .kilo/context/projects
   ```
   (замените `.kilo/context/projects` на `context/projects` для claude/codex/opencode)

## Требования окружения

- **1С:Предприятие 8.3.27+** (утилита `1cv8` в PATH или в реестре баз).
- **PowerShell 5.1+** (скиллы).
- **Python 3.x** (`bsl-check.py`, `build_summaries.py`).
- Опционально: **BSL Language Server** (JAR) — скачать с
  https://github.com/1c-syntax/bsl-language-server/releases → `tools/bsl-language-server.jar`.

## Безопасность

- `.v8-project.json` (пути/учётки ИБ), `specs/TASK-*`, `logs/`, `summaries/` — **не коммитятся**.
- Текст запроса пользователя в логах — ненадёжные данные (защита от prompt-injection при разборе).
- Правка конфигурации самой схемы (`core/agents`, `adapters`) — только вручную вне сессии агента.

## Лицензия

См. LICENSE (если применимо). Скиллы используют PowerShell/Python скрипты для работы
с XML-метаданными 1С; BSL Language Server (если используется) имеет свою лицензию (LGPL-3.0).
