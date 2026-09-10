# Рекомендации по корпоративному overlay (раздел 9)

## Контекст

Публичная версия проекта (`ai-environment`) содержит корпоративную интеллектуальную
собственность: название компании, префиксы объектов, названия конфигураций, стандарты
разработки, per-project контексты. Это не пароли, но это архитектурная информация,
которая не должна публиковаться.

Целевая архитектура: **универсальное ядро harness** + **закрытый корпоративный overlay**
с контекстами, стандартами и индексами.

## Инвентаризация корпоративных следов

### 1. Стандарты компании

| Файл | Содержание | Действие |
|---|---|---|
| `core/context/standards/level-standards.md` | Название компании "level", БИТ.ФИНАНС / БИТ:Строительство КОРП 3.0, префикс `лвл_`, SonarQube | **Удалить из публичной версии** — перенести в overlay |

### 2. Per-project контексты

| Файл | Содержание | Действие |
|---|---|---|
| `core/context/projects/finance/context.md` | БИТ.ФИНАНС, БИТ:Строительство, модули, версии | **Удалить** — overlay |
| `core/context/projects/finance/objects-index.md` | Внутренние имена объектов | **Удалить** — overlay |
| `core/context/projects/finance/analyst-scope.md` | Справочный список объектов | **Удалить** — overlay |
| `core/context/projects/collector/*` | Самописная конфигурация Коллекционер | **Удалить** — overlay |
| `core/context/projects/trade/*` | Управление торговлей 10.3 | **Удалить** — overlay |

### 3. INSTRUCTIONS.md

| Файл | Содержание | Действие |
|---|---|---|
| `core/context/INSTRUCTIONS.md` | Стр. 34: БИТ.ФИНАНС / БИТ:Строительство; стр. 100: level-standards.md; стр. 196: level-standards.md | **Очистить** — заменить корпоративные упоминания на нейтральные примеры |

### 4. Агенты (тела)

| Файл | Содержание | Действие |
|---|---|---|
| `core/agents/1c-analyst.md` | Стр. 119: `лвл_Смета`; стр. 138: `лвл_`/`бит_` | **Заменить** `лвл_` → `<префикс_проекта>_`, `бит_` → `<префикс_библиотеки>_` |
| `core/agents/1c-developer.md` | Стр. 20, 34, 85, 155, 170, 186, 193-194, 210, 214, 222, 232, 275, 382: level-standards.md, `лвл_` | **Заменить** упоминания level-standards.md на `{{CONTEXT_DIR}}/standards/standards.md` (обобщить); `лвл_` → пример |
| `core/agents/1c-do.md` | Стр. 100: `Документ.лвл_Смета` | **Заменить** на `Документ.<ИмяОбъекта>` |
| `core/agents/1c-tools.md` | Стр. 48, 52, 69, 74, 89: `лвл_Смета`, `лвл_Работы` | **Заменить** на нейтральные примеры |
| `core/agents/1c-reviewer.md` | Стр. 58: level-standards.md | **Заменить** на `{{CONTEXT_DIR}}/standards/standards.md` |

### 5. SDD

| Файл | Содержание | Действие |
|---|---|---|
| `core/sdd/README.md` | Стр. 21: level-standards.md; стр. 132: `лвл_`/`бит_` | **Очистить** — обобщить |

### 6. Скрипты

| Файл | Содержание | Действие |
|---|---|---|
| `core/scripts/build_summaries.py` | Стр. 944, 950: `лвл_Смета` в help text | **Заменить** на `Документ.МойДокумент` |

### 7. Миграционные скрипты

| Файл | Содержание | Действие |
|---|---|---|
| `install/migrate-context.ps1` | Стр. 18-20: level-standards.md | **Очистить** — обобщить имя файла |

### НЕ корпоративные совпадения (не трогать)

Следующие совпадения по `Level` в grep — легитимные термины платформы 1С, НЕ корпоративные данные:
- `LimitLevelCount`, `LevelCount` (метаданные 1С)
- `TopLevelParent`, `ExpandTopLevel`, `TopLevel` (формы 1С)
- `currentLevel`, `Build-GroupLevel`, `Build-PlannerLevel` (формы 1С)
- `FirstLevel`, `firstLevelNames` (cfe-borrow: пути данных формы)

## Целевая архитектура

```
ai-environment/              — публичное ядро (без корпоративных данных)
├── core/
│   ├── agents/              — тела агентов (обобщённые примеры)
│   ├── skills/              — skills (MIT, upstream)
│   ├── context/
│   │   ├── INSTRUCTIONS.md  — общая схема (без упоминаний level/БИТ)
│   │   ├── BslChecklists.md
│   │   ├── common/
│   │   ├── standards/        — ПУСТО (пример standards.example.md)
│   │   └── projects/        — ПУСТО (примеры context.example.md)
│   ├── scripts/
│   └── sdd/
├── adapters/
├── install/
└── examples/

overlay/                      — закрытый корпоративный overlay (не публикуется)
├── standards/
│   └── level-standards.md   — стандарты компании
├── projects/
│   ├── finance/             — БИТ.ФИНАНС контекст
│   ├── trade/               — УТ 10.3 контекст
│   └── collector/           — Коллекционер контекст
└── README.md                — инструкция по установке overlay
```

## План действий (НЕ выполнять автоматически)

1. **Создать overlay-структуру** (отдельный каталог/репозиторий).
2. **Перенести** `core/context/standards/level-standards.md` → `overlay/standards/`.
3. **Перенести** `core/context/projects/*` → `overlay/projects/`.
4. **Очистить** `core/context/INSTRUCTIONS.md` от упоминаний БИТ/level.
5. **Обобщить** примеры в агентах: `лвл_` → `<префикс_проекта>_`, `лвл_Смета` → `Документ.<Имя>`.
6. **Обобщить** help text в `build_summaries.py`.
7. **Создать** `core/context/standards/standards.example.md` — шаблон стандартов.
8. **Создать** `core/context/projects/<project>/context.example.md` — шаблон контекста.
9. **Обновить** install.ps1 — поддержать установку overlay (копирование standards + projects).
10. **Добавить** в validate.py — проверку отсутствия корпоративных маркеров (`лвл_`, `бит_`, `БИТ.`, `Level`, `level-standards`) в публичной части.
