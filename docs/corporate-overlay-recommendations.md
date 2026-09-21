# External overlays: контракт private-слоя поверх public base

## Назначение

Публичный репозиторий `1c_dev` — это **base installer**: core-агенты, правила, SDD,
guards, скрипты, generic-скиллы, адаптеры и validation. Он не знает ничего о
компании, внутренних репозиториях, project IDs, URL, credentials и корпоративных
стандартах.

**External overlay** — опциональный private-слой, который накладывается поверх
базовой установки в целевой проект, позволяя внешнему приватному репозиторию:

1. использовать public `1c_dev` как базовый installer;
2. после штатной установки накладывать собственный overlay;
3. добавлять project context, rules, skills и tool-specific configuration;
4. не форкать и не дублировать public core.

Overlay не изменяет публичный (и приватный) исходный репозиторий — применяется
только к установленной target-раскладке.

## Команда

```powershell
.\install\install.ps1 `
  -Tool kilo `
  -Target C:\MyProject `
  -OverlayPath C:\MyPrivateRepo\overlay
```

Без `-OverlayPath` поведение установщика на 100% совпадает с установкой без
overlay (overlay — полностью optional feature).

Флаг `-OverlayDryRun` (требует `-OverlayPath`) показывает план
add/override/skip, не записывая файлы и манифест.

## Ожидаемая структура overlay

Формируется вокруг фактических категорий public install (installer не применяет
категории, которых не существует):

```text
overlay/
├── agents/      → <agentDir>/            (add/override агентов: agents/1c-developer.md)
├── skills/      → <skillDir>/            (add/override скиллов: skills/my-skill/...)
├── rules/       → <contextDir>/rules/    (add/override on-demand правил)
├── context/     → <contextDir>/          (generic: любые файлы контекста, включая
│                                            context/projects/<project>/...)
├── standards/   → <contextDir>/standards/ (add/override стандартов компании)
├── projects/    → <contextDir>/projects/ (per-project контекст:
│                                            projects/<project>/context.md)
└── tool/
    ├── kilo/      → <target root>/       (tool-specific config, только для -Tool kilo)
    ├── openworks/ → <target root>/       (только для -Tool openworks)
    ├── claude/    → <target root>/       (только для -Tool claude)
    └── codex/     → <target root>/       (только для -Tool codex)
```

`<agentDir>`, `<skillDir>`, `<contextDir>` — пути выбранного адаптера
(`.kilo/agent`, `.kilo/skills`, `.kilo/context` для kilo; `.claude/...` для claude;
`.opencode/...` для openworks; `agents/`, `skills/`, `context/` для codex).

Пример:

```text
overlay/tool/kilo/.kilo/kilo.jsonc        →   <target>/.kilo/kilo.jsonc
overlay/projects/myproject/context.md     →   <target>/.kilo/context/projects/myproject/context.md
overlay/agents/1c-developer.md            →   <target>/.kilo/agent/1c-developer.md
```

Generic-пример структуры — `examples/overlay/` (только dummy-файлы).

## Семантика

### Add

Файла нет в base install → overlay добавляет его (corporate rule, corporate
context, дополнительный skill и т.п.).

### Override

Файл уже существует → overlay **заменяет** установленную копию. Никакого
автоматического текстового слияния (merge) Markdown/YAML/JSON не производится:
`overlay file wins`, но только внутри target installation — public source
остаётся неизменным.

### Приоритет и update

Последовательность установки:

```text
public core → selected adapter → base install → overlay → validation / doctor
```

При повторной установке (update):

1. public installer обновляет base (overlay-файлы исключаются из
   user-modified-защиты — ими управляет overlay);
2. overlay накладывается заново;
3. **overlay override снова имеет приоритет**: если private overlay содержит
   `overlay/agents/1c-developer.md`, именно он остаётся установленным после
   update; если overlay для developer ничего не содержит — используется
   обновлённый public.

### Ограничения записи (security boundaries)

Overlay не может:

- писать `..\` / absolute / symlink / path traversal — установщик завершается
  ошибкой до первой записи (план overlay валидируется целиком до применения);
- записывать что-либо в `.git`;
- писать вне target project root;
- изменять public source checkout и произвольные user/system files.

Никогда не пишутся overlay (независимо от `-Force`): `LICENSE`,
`.ai-rules.json`, `.install-manifest-overlay.json`, `.dev.env`, `.v8-project.json`.

Корневые конфиги (`AGENTS.md`, `CLAUDE.md`, `INSTRUCTIONS.md`, `kilo.json`,
`openworks.json`, `specs/README.md`) — override только с `-Force` (создаётся
`.bak`); их add разрешён.

### Manifest `.install-manifest-overlay.json`

Установщик ведёт отдельный manifest overlay-файлов (рядом с base-манифестом
`.ai-rules.json`). По каждому файлу хранится: relative target path, relative
source path, operation (`add` / `override`), sha256-hash, `installedAt` /
`updatedAt`. Содержимое файлов НЕ хранится.

Цели: отличать public base от private overlay, диагностировать расхождения,
безопасно переустанавливать/update.

Orphan-семантика: файл из старого overlay, отсутствующий в новом, удаляется
при повторной установке, если не менялся после установки; override-файл, которым
снова владеет base, восстанавливается из public. Для полной очистки overlay —
update с пустым overlay, затем можно удалить манифест.

Если overlay-манифест от предыдущей установки существует, а `-OverlayPath` не
передан — установщик завершается ошибкой (защита от незаметного расхождения).

## Не копировать public core в overlay

Private overlay НЕ должен содержать копий public-файлов без причины:

```text
overlay/agents/1c-developer.md   # плохой пример, если файл идентичен public-версии
```

Private repository хранит только:

- additions (новые файлы);
- intentional overrides (осознанные замены);
- corporate configuration / context.

Это основной принцип предотвращения расхождения двух репозиториев.

## Corporate standards / rules / skills / context

Overlay доставляет private standards, project rules, дополнительные skills и
контекстные документы через принятую категорийную структуру (см. выше).
Агенты видят их через существующую context/rule-архитектуру — новый механизм
загрузки не вводится. Плейсхолдеры `{{CONTEXT_DIR}}`, `{{SKILLS_DIR}}`,
`{{AGENTS_DIR}}`, `{{LOGS_DIR}}` (и `{{SKILL_DIR}}` в скиллах) подставляются
установщиком так же, как для public core, поэтому один overlay работает со
всеми адаптерами.

## Отчёт установщика

```text
Overlay:
  added: N
  overridden: M     (relative path выводится для каждого override)
  skipped: K
```

Содержимое файлов не выводится. При `-OverlayDryRun` выводится полный план
без записи.

## Требования

- Overlay не копируется в публичный репозиторий.
- Overlay устанавливается только в target-раскладку выбранного адаптера.
- Исходный внешний overlay не изменяется.
- Отсутствие overlay не является ошибкой универсальной установки.
- См. `SECURITY.md`.
