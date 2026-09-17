# AGENT-INSTALL.md — протокол установки схемы 1C Dev для ИИ-агента

> Ты — ИИ-агент. Этот файл — протокол установки/обновления агентской схемы 1С Dev в проекте.
> Следуй шагам ниже. Текущий файл — не обзор, а инструкция к исполнению.

## Что устанавливается

Схема разработки 1С:Предприятие 8.3 (6 агентов, 78 скиллов, SDD, guard, on-demand правила).
Подробнее — в `README.md`.

## Предварительные требования

- PowerShell 5.1+ (установщик и скиллы).
- Python 3.x (`validate.py`, `doctor.py`, `applier_guard.py`, `build_summaries.py`).
- Git (для клонирования harness-репозитория).

## Протокол установки

### Шаг 1: Клонировать harness

Если harness уже клонирован локально — используй существующий путь. Иначе:

```powershell
git clone {GITHUB_URL} "$env:TEMP\1c-dev-harness"
```

Запомни путь к клону (например, `$env:TEMP\1c-dev-harness`).

### Шаг 2: Определить целевой инструмент

Спроси пользователя: «Какой инструмент вы используете?» с вариантами:
- Kilo
- Claude Code
- OpenWork
- Codex CLI

Если в проекте уже есть `.kilo/`, `.claude/`, `.opencode/` или `AGENTS.md` (codex) —
предложи соответствующий инструмент как рекомендованный, но подтверди у пользователя.

### Шаг 3: Запустить установщик

Цель — корень проекта 1С (где будет `projects/`, `.v8-project.json`, и т.д.).

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<путь-к-harness>\install\install.ps1" -Tool <kilo|claude|codex|openworks> -Target "<корень-проекта>"
```

Установщик:
- копирует агенты, скиллы, контекст, on-demand правила, скрипты;
- создаёт `.dev.env` с автодетектом (`PLATFORM_PATH`, `PLATFORM_VERSION`, `PREFIX`);
- генерирует `.ai-rules.json` манифест с hash-трекингом;
- копирует `AGENT-INSTALL.md` и `LICENSE` в корень проекта.

### Шаг 4: Проверить установку

```powershell
python "<корень-проекта>\scripts\doctor.py"
python "<корень-проекта>\scripts\validate.py"
```

`doctor.py` должен вернуть 0 ERROR. `validate.py` должен вернуть 0 FAIL.
Если есть ERROR/FAIL — сообщи пользователю, не продолжай.

### Шаг 5: Сообщить пользователю следующие шаги

1. Создать `.v8-project.json` из `examples/v8-project.example.json` (реестр баз; указать
   `environment`; логин/пароль — через env-переменные `username_env`/`password_env`).
2. Разместить исходники конфигурации в `projects/<источник>/src/` (DumpConfigToFiles).
3. Первичный индекс: `python scripts/build_summaries.py --project <имя> --scan`.

## Протокол обновления

### Шаг 1: Проверить .ai-rules.json

Если в проекте есть `.ai-rules.json` — это уже установленная схема. Запусти update:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<путь-к-harness>\install\install.ps1" -Tool <tool> -Target "<корень-проекта>" -Mode update
```

User-modified файлы (hash расходится) сохраняются. Для принудительной перезаписи — `-Force`.

### Шаг 2: Проверить установку

`doctor.py` + `validate.py` (как в установке).

## Миграция существующих файлов

Если в целевом проекте уже есть `AGENTS.md`, `CLAUDE.md`, `kilo.json`, `openworks.json`:
- НЕ перезаписывай без подтверждения пользователя.
- Спроси: «Обнаружен существующий <файл>. Перезаписать (текущий будет бэкапом в <файл>.bak)?»
- При согласии — переименуй старый в `.bak`, затем продолжи установку.

## Что НЕ делать

- НЕ создавать `.v8-project.json` (пользователь делает это вручную).
- НЕ размещать исходники в `projects/` (пользователь делает DumpConfigToFiles).
- НЕ запускать `build_summaries.py` автоматически (только по запросу пользователя).
- НЕ запускать `applier_guard.py` / `safe_apply.py` (это для `1c-applier`, не для установки).
- НЕ коммитить `.v8-project.json`, `.dev.env`, `.ai-rules.json` (содержат параметры проекта).
- НЕ изменять файлы harness-репозитория (только чтение для установки в целевой проект).
