# Первый пилот: инструкция по запуску

## Цель
Первое контролируемое использование harness на изолированной тестовой базе 1С.

## Предварительные требования
1. Сделать репозиторий и overlay приватными.
2. Подготовить отдельную файловую или изолированную серверную тестовую базу.
3. Не использовать production-копию с актуальными персональными данными без обезличивания.
4. Запретить тестовым машинам сетевой доступ к production (если возможно).
5. Создать отдельного пользователя 1С с минимальными правами.
6. Создать backup штатной внешней процедурой (DT-выгрузка или копия файла базы).

## Пошаговая последовательность

### 1. Установка
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File install\install.ps1 -Tool kilo -Target .
```
При наличии корпоративного overlay:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File install\install.ps1 -Tool kilo -Target . -OverlayPath C:\overlay
```

### 2. Проверка окружения
```bash
python scripts/doctor.py
python scripts/validate.py
python scripts/doctor.py --pilot
```

### 3. SDD-цикл
- Пользователь → `1c-do` → классификация → SDD (для нетривиальной правки).
- `1c-analyst` → `01_context.md`, `03_solution_spec.md`, `05_test_scenarios.md`.
- `1c-do` → gate (status: approved, risk, scope_hash).
- `1c-developer` → реализация по spec → `06_change_report.md`.

### 4. Review
- `1c-reviewer` → `pilot-control/<TASK-ID>/review.md` (verdict: approved).
- Внешний субъект → approval spec (status: approved).

### 5. Backup
- **`backup_mode: external`** (рекомендуется для первого пилота): внешний backup уже сделан
  владельцем. Не требуются повторная выгрузка, `backup.md` и его хеш. Не требовать новый backup
  на каждом продолжении задачи.
- **Иные режимы:** пользователь создаёт backup штатной процедурой (DT-выгрузка).
  Формирует `pilot-control/<TASK-ID>/backup.md` с `artifact`, `artifact_sha256`, `status: success`.
  Файл backup.md — read-only для агентов (в `pilot-control/`).

### 6. Dry-run
```bash
python scripts/safe_apply.py --task TASK-001 --db local-demo --dry-run
```
Guard должен пройти (exit 0). Если guard FAIL — остановиться.

### 7. Применение (ровно один раз)
- Wrapper выполняет load-xml Partial, затем update — отдельными операциями внутри одной команды.
```bash
python scripts/safe_apply.py --task TASK-001 --db local-demo
```
- Не вызывать apply второй раз «для проверки».

### 8. Продолжение задачи после обновления harness
После установки обновлённого harness:
1. Перечитать обновлённые инструкции и существующие артефакты задачи.
2. Не начинать разработку заново — исходники, спецификации и отчёты сохранены.
3. Не выдумывать новые approvals — существующий review в `pilot-control/<TASK-ID>/review.md`
   действителен.
4. Не считать предыдущую неудачную попытку доказательством отсутствия изменений в базе —
   сначала выяснить фактическое состояние.

### 8. Проверка
- Проверить фактический scope (сверить изменённые файлы с планом).
- Выполнить статические проверки (`bsl-check.py`).
- Выполнить reviewer post-check.
- Сформировать change report.

### 9. При ошибке
- Остановиться и не выполнять автоматическое восстановление.
- Восстановление — отдельная ручная процедура (DT-загрузка из бэкапа).

## Операции, запрещённые в первом пилоте
- Full load
- DT restore (load-dt)
- CF/CFE load (load-cf)
- create DB
- web-publish / web-unpublish
- production
- автоматический rollback
- массовое изменение данных
- совмещение load-xml и update в одной команде

## Разрешённые операции
- `load-xml` в режиме `Partial` (только по утверждённому списку файлов).
- Отдельный `update` (если явно разрешён политикой пилота и планом).
