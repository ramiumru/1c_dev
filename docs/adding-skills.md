# Добавление скиллов и агентов

## Структура скилла

Каждый скилл — каталог в `core/skills/<name>/`:

```
core/skills/<name>/
├── SKILL.md          — инструкция (frontmatter + описание параметров и команд)
└── scripts/
    ├── <name>.ps1    — PowerShell-скрипт (основной)
    └── <name>.py     — Python-скрипт (опционально, cross-platform fallback)
```

## Формат SKILL.md

```markdown
---
name: <name>
description: Краткое описание — когда применять и что возвращает
argument-hint: <Param1> [-Param2 value]
allowed-tools:
  - Bash
  - Read
  - Glob
---

# /<name> — <заголовок>

<Описание что делает скилл>

## Параметры и команда

| Параметр | Описание |
|----------|----------|
| `Param1` | ... |

```powershell
powershell.exe -NoProfile -File "{{SKILL_DIR}}/scripts/<name>.ps1" <параметры>
```
```

## Правила плейсхолдеров

- `{{SKILL_DIR}}` — путь к текущему скиллу. Установщик подставляет:
  - Kilo: `.kilo/skills/<name>`
  - Claude: `.claude/skills/<name>`
  - Open Works: `.openworks/skills/<name>`
- `{{SKILLS_DIR}}` — путь к корневому каталогу скиллов (для кросс-скилловых ссылок).

**Всегда** используйте плейсхолдеры вместо хардкода путей. Никогда не пишите
`.kilo/skills/...` или `.claude/skills/...` в `core/skills/` — это нарушит
переносимость.

## Регистрация

Регистрация в `install.ps1` **не требуется** — установщик автоматически обходит
все каталоги в `core/skills/` по glob. Просто создайте каталог с `SKILL.md`.

## Имена скиллов

- Строчные латинские буквы, цифры, дефисы: `meta-info`, `form-compile`.
- Длина: 1–64 символа.
- Не начинать/заканчивать дефисом, без `--`.
- Совпадает с именем каталога.

## Добавление агентов

Агенты — в `core/agents/<name>.md` (тело без frontmatter) + frontmatter в
`adapters/<tool>/frontmatter/<name>.yml` для каждого инструмента.

### Тело агента

- Начинается с `<!-- Agent: <name> | Mode: <mode> | Model: <model> -->`.
- Использует плейсхолдеры путей: `{{CONTEXT_DIR}}`, `{{LOGS_DIR}}`,
  `{{SKILLS_DIR}}`, `{{AGENTS_DIR}}`.
- Установщик подставляет пути целевого инструмента.

### Frontmatter (per-tool)

Для multi-agent инструментов (Kilo, Claude, Open Works) — отдельный `.yml` файл
в `adapters/<tool>/frontmatter/`. Для Codex — инструкция в `AGENTS.md.tpl`.

## Сборка после изменений

После правки `core/` или `adapters/` — перезапустите установщик:

```powershell
powershell -File install/install.ps1 -Tool <kilo|claude|codex|openworks>
```
