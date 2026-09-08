# CONTRIBUTING.md — вклад в 1C Dev

Краткие правила для контрибьюторов агентской схемы разработки 1С.

## Структура

- `core/agents/<name>.md` — тела агентов (tool-agnostic, плейсхолдеры путей, без frontmatter).
- `adapters/<tool>/frontmatter/<name>.yml` — per-tool права/описания (kilo, claude, openworks);
  для codex — упоминание ролей в `AGENTS.md.tpl`.
- `core/skills/<name>/` — скиллы (`SKILL.md` + `scripts/*.ps1|py`).
- `core/scripts/` — CLI-утилиты (`bsl-check.py`, `build_summaries.py`, `applier_guard.py`,
  `validate.py`, `doctor.py`).
- `core/context/`, `core/sdd/` — контекст и SDD-шаблоны.
- `install/install.ps1` — установщик (kilo | claude | codex | openworks).

## Правила

1. **Плейсхолдеры путей.** В `core/**` никогда не хардкодьте пути инструментов
   (`.kilo/`, `.claude/`, `.openworks/`). Используйте `{{CONTEXT_DIR}}`, `{{LOGS_DIR}}`,
   `{{SKILLS_DIR}}`, `{{AGENTS_DIR}}`, `{{SKILL_DIR}}` — установщик подставит фактические
   пути. В `core/context/*.md` тоже допустимы эти плейсхолдеры (установщик обрабатывает их).
2. **Правильное имя адаптера** — **Open Works** (`openworks`); `OpenCode`/`opencode` —
   устаревшее ошибочное название (кроме легаси-цитат в истории).
3. **Минимальные полномочия.** Новые права агентов добавляйте точечно; заканчивайте блоки
   catch-all deny. Опасные операции (БД, веб) — отдельные классы, по умолчанию выключены.
4. **Секреты.** Не коммитьте `.v8-project.json`, логи, `specs/TASK-*/`. В примерах и тестах —
   только вымышленные данные; пароли — через `username_env`/`password_env`.
5. **Атрибуция стороннего.** При добавлении чужих материалов сохраняйте copyright-заголовки и
   строку `# Source:`; обновляйте `NOTICE.md` и `THIRD_PARTY_LICENSES.md`. Не выбирайте лицензию
   для оригинальной части от имени владельца.
6. **SDD.** Правки самих агентов/скриптов схемы — нетривиальные: сопровождайте спецификацией с
   машиночитаемым блоком `status`/`risk` (`core/sdd/README.md`).
7. **Проверка перед коммитом:**
   ```powershell
   python scripts/validate.py          # единая локальная проверка
   python scripts/doctor.py            # диагностика окружения
   python -m compileall core/scripts   # синтаксис Python
   ```
   `validate.py` должен проходить без ERROR.

## Стиль

- BSL-агенты/доки — на русском. CLI-скрипты — Python 3.8+, UTF-8, дружественные ошибки
  (без traceback для обычных ошибок аргументов), `--help`/`-h` обязательны.
- Никаких эмодзи в артефактах. Кратко и по делу.
