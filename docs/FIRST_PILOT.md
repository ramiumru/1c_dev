# Первая версия: поддерживаемый процесс (Windows + Kilo)

## Поддерживаемый сценарий

- Агенты (`1c-do`, `1c-analyst`, `1c-developer`, `1c-reviewer`) готовят спецификацию,
  реализацию и независимый review.
- **Пользователь** запускает штатные CLI для вычисления хеша, статических проверок,
  dry-run и применения из терминала.
- Автоматическое исполнение `1c-reviewer` / `1c-applier` через Kilo Task **не является**
  поддерживаемым сценарием этой версии.
- `safe_apply.py` и guards сохраняются.
- Целевая среда: Windows + Kilo. Linux и другие адаптеры не заявляются проверенными.

## Предварительные требования

1. Установленный harness (см. установку ниже).
2. Файловая тестовая база 1С (`environment: local`, `password_mode: none`,
   `backup_mode: external`).
3. Внешний backup, сделанный владельцем штатной процедурой.
4. Платформа 1С:Предприятие 8.3 в PATH или указанная в `.dev.env`.

## Поддерживаемый процесс по шагам

### Шаг 1: SDD-подготовка (через агентов)

| Кто | Что делает | Артефакт |
|---|---|---|
| Пользователь → `1c-do` | Формулирует запрос, получает TASK-ID | — |
| `1c-analyst` | Анализирует, заполняет контекст и spec | `specs/<TASK-ID>/01_context.md`, `03_solution_spec.md`, `05_test_scenarios.md` |
| Пользователь | Согласовывает spec (внешний approval) | `status: approved` в yaml-блоке `03_solution_spec.md` |
| `1c-developer` | Реализует по spec | `specs/<TASK-ID>/06_change_report.md` + `projects/**/src/**` |

### Шаг 2: Вычисление scope_hash (пользователь, из корня проекта)

После финализации разделов «Границы изменения» и «Затрагиваемые файлы» в spec:

```powershell
python scripts\scope_hash.py --spec specs\<TASK-ID>\03_solution_spec.md
```

Вывод: 64 hex-символа. Записать в yaml-блок `scope_hash:` в `03_solution_spec.md`,
`06_change_report.md` и `pilot-control/<TASK-ID>/review.md`.

**Изменение scope после записи хеша аннулирует его** — пересчитать заново.

### Шаг 3: Независимый review (через агента или вручную)

`1c-reviewer` анализирует фактические результаты проверок и реализацию, не подменяет
проверки предположениями.

**Reviewer обязан пересчитать хеш самостоятельно:**

```powershell
python scripts\scope_hash.py --spec specs\<TASK-ID>\03_solution_spec.md
```

Сверить результат со значением в yaml-блоке spec, `06_change_report.md` и `review.md`.
Несовпадение → `blocked`. Перенос хеша из spec без пересчёта недопустим.

Review-артефакт: `pilot-control/<TASK-ID>/review.md` (verdict: approved + findings).

### Шаг 4: Статическая проверка BSL (пользователь, из корня проекта)

```powershell
python scripts\bsl-check.py projects\<источник>\src\...\<Module.bsl>
```

ERROR — исправить перед apply. WARN (lone LF) — некритично.

### Шаг 5: Dry-run (пользователь, из корня проекта)

```powershell
python scripts\safe_apply.py --task <TASK-ID> --db <database-id> --dry-run
```

Guard проверяет: environment, TASK-ID, approved spec, review, scope_hash, plan files,
backup_mode. Exit 0 = можно применять. Exit 1 = СТОП, исправить причину.

### Шаг 6: Применение (пользователь, ровно один раз)

```powershell
python scripts\safe_apply.py --task <TASK-ID> --db <database-id>
```

Wrapper выполняет: guard → `db-load-xml` (Partial) → при успехе `db-update` (UpdateDBCfg).

**Не вызывать apply второй раз «для проверки».**

### Шаг 7: При ошибке

- Остановиться. Не выполнять автоматическое восстановление.
- Восстановление — отдельная ручная процедура (DT-загрузка из внешнего backup).

## Установка и обновление

### Установка

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File install\install.ps1 -Tool kilo -Target .
```

### Обновление (сохраняет пользовательские файлы)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File install\install.ps1 -Tool kilo -Target . -Mode update
```

Сохраняются: `.v8-project.json`, `.dev.env`, `projects/`, `specs/`, `pilot-control/`.

### Проверка после установки

```powershell
python scripts\doctor.py
python scripts\validate.py --skip-smoke
```

### Продолжение задачи после обновления

1. Перечитать обновлённые инструкции и существующие артефакты задачи.
2. Не начинать разработку заново — исходники, спецификации и отчёты сохранены.
3. Не выдумывать новые approvals — существующий review действителен.
4. Перед dry-run проверить: `pilot-control/<TASK-ID>/review.md` существует,
   `scope_hash` и `spec_version` совпадают.
5. Если review находится только в прежнем месте — запустить `1c-reviewer` на готовых
   артефактах, получить новый канонический review без повторной разработки.
6. После предыдущей неудачной попытки сначала установить фактическое состояние базы,
   затем применять только при подтверждении, что изменения ещё не применены.

## Запрещённые операции

- Full load
- DT restore (load-dt)
- CF/CFE load (load-cf)
- create DB
- web-publish / web-unpublish
- production
- автоматический rollback
- массовое изменение данных
- прямой ручной вызов load-xml или update в обход `safe_apply.py`
- повторный apply без `--dry-run`

## Разрешённые операции

- `python scripts\safe_apply.py --task <TASK-ID> --db <id>` — единая команда apply.
- `--dry-run` для preflight проверки.
- `python scripts\scope_hash.py --spec <путь>` — вычисление хеша.
- `python scripts\bsl-check.py <путь>` — статическая проверка BSL.

## Известные ограничения этой версии

- Автоматическое делегирование `1c-reviewer`/`1c-applier` через Kilo Task не проверено
  и не является поддерживаемым сценарием.
- `bsl-check.py` — эвристическая проверка без полноценного BSL-парсера.
- Linux и адаптеры кроме Kilo не заявляются проверенными.
- Экранированные кавычки в BSL-строках не учитываются `bsl-check.py`.
