# 1C Dev — агентская схема разработки 1С

Мультиагентная схема для разработки конфигураций 1С:Предприятие 8.3 (BSL + XML-метаданные)
поверх AI-кодинг-ассистентов. Tool-agnostic ядро + адаптеры под конкретный инструмент.

## Поддерживаемые инструменты

| Инструмент | Установка | Режим | Механика |
|---|---|---|---|
| **Kilo** | `install.ps1 -Tool kilo` | Multi-agent | `.kilo/agent`, `.kilo/skills`, `kilo.json` |
| **Claude Code** | `install.ps1 -Tool claude` | Multi-agent | `.claude/agents`, `.claude/skills`, `CLAUDE.md` |
| **Open Works** | `install.ps1 -Tool openworks` | Multi-agent | `.openworks/agents`, `.openworks/skills`, `openworks.json` |
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
- **1c-developer** — правка BSL/XML строго по спецификации (gate «нет кода без spec», `status: approved`).
- **1c-reviewer** — независимый review (соответствие spec/scope, регрессии, права/RLS, контракты); обязателен для `risk: high` и перед apply.
- **1c-applier** — применение правок в ИБ (db-load-xml → db-update, бэкап dt), только по явному запросу, после preflight `applier_guard.py`; не работает с `production`.
- **1c-tools** — исполнитель утилит (`build_summaries.py`), без самостоятельности.

### Spec-Driven Development (SDD)

Нетривиальные правки проходят через спецификацию:

```
00_request → 01_context → 03_solution_spec (GATE, status: approved) → реализация → 05_test_scenarios → 06_change_report → review.md (verdict, для high-risk/перед apply)
```

TASK-ID маркирует и папку спецификации, и комментарии в коде (`// ++ #TASK-ID`).
Спецификация содержит машиночитаемый блок `status`/`risk`/`scope_hash`; разработка
допускается только при `status: approved`; для `risk: high` обязателен независимый
review (`1c-reviewer`, `specs/<TASK-ID>/review.md`) перед применением. `1c-applier`
проверяет preflight через `scripts/applier_guard.py`. Шаблоны SDD-артефактов — в
`core/sdd/README.md`.

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
│   ├── agents/              — 6 агентов: тела (без frontmatter) — 1c-do/analyst/developer/reviewer/applier/tools
│   ├── skills/              — 78 скиллов: <name>/{SKILL.md, scripts/}
│   ├── context/             — INSTRUCTIONS.md, BslChecklists.md, standards/, common/, projects/
│   ├── scripts/             — bsl-check.py, build_summaries.py, applier_guard.py, validate.py, doctor.py
│   └── sdd/                 — README.md (SDD-шаблоны)
├── adapters/                — per-tool обвязка
│   ├── kilo/                — kilo.json.tpl + frontmatter/*.yml
│   ├── claude/              — CLAUDE.md.tpl + frontmatter/*.yml
│   ├── codex/               — AGENTS.md.tpl + config.toml.example
│   └── openworks/           — openworks.json.tpl + frontmatter/*.yml
├── install/                 — install.ps1 + миграционные скрипты
├── examples/                — v8-project.example.json (безопасный, без секретов)
└── docs/                    — adding-skills.md
```

## Быстрый старт

1. Клонируйте репозиторий в рабочее пространство проекта 1С.
2. Установите схему под ваш инструмент:
   ```powershell
   powershell -File install/install.ps1 -Tool <kilo|claude|codex|openworks>
   ```
3. Создайте `.v8-project.json` из `examples/v8-project.example.json` (реестр баз; укажите
   `environment`; **НЕ коммитить**). Логин/пароль — через переменные окружения
   (`username_env`/`password_env`), не в файле.
4. Разместите исходники конфигурации в `projects/<источник>/src/` (DumpConfigToFiles).
5. Первичный индекс:
   ```powershell
   python scripts/build_summaries.py --project <имя> --scan --context-dir .kilo/context/projects
   ```
   (замените `.kilo/context/projects` на `.claude/context/projects`/`context/projects`/
   `.openworks/context/projects` для claude/codex/openworks)
6. Проверка окружения: `python scripts/doctor.py`.
7. Локальная валидация: `python scripts/validate.py`.

## Требования окружения

- **1С:Предприятие 8.3.27+** (утилита `1cv8`/`ibcmd` в PATH или в реестре баз).
- **PowerShell 5.1+** (скиллы).
- **Python 3.x** (`bsl-check.py`, `build_summaries.py`, `applier_guard.py`, `validate.py`, `doctor.py`).
- Опционально: **BSL Language Server** (JAR) — скачать с
  https://github.com/1c-syntax/bsl-language-server/releases → `tools/bsl-language-server.jar`.

## Безопасность

- `.v8-project.json` (пути/учётки ИБ), `specs/TASK-*`, `logs/`, `summaries/` — **не коммитятся**.
- Секреты (логин/пароль) — только в переменных окружения; не в файлах, логах, отчётах, SDD-артефактах.
- `1c-applier` не работает с `production`; требует явное допустимое `environment`, однозначный
  выбор базы, `status: approved` spec, review verdict (для high-risk) и успешный preflight
  (`scripts/applier_guard.py`). Автоматическое восстановление DT запрещено.
- Текст запроса пользователя в логах — ненадёжные данные (защита от prompt-injection при разборе).
- Правка конфигурации самой схемы (`core/agents`, `adapters`) — только вручную вне сессии агента.
- См. `SECURITY.md` и `NOTICE.md`.

## Лицензия

**Лицензия оригинальной части проекта НЕ определена владельцем** (файл `LICENSE` отсутствует;
выбор лицензии — прерогатива владельца). Скиллы в `core/skills/**` — адаптация материалов из
upstream-проекта [Nikolay-Shirokov/cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills)
(лицензия MIT; выдержка — в `THIRD_PARTY_LICENSES.md`, атрибуция — в `NOTICE.md`). BSL Language
Server (если используется) имеет лицензию LGPL-3.0.
