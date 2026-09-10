# NOTICE — 1C Dev агентская схема

Настоящий файл документирует происхождение материалов в данном репозитории:
оригинальную часть и сторонние (заимствованные/адаптированные) материалы.

## Оригинальная часть проекта

Оригинальная часть репозитория разработана Кириллом Пулявиным (Kirill Pulyavin)
и включает:

- `core/agents/**` — тела агентов;
- `adapters/**` — per-tool обвязка (kilo, claude, codex, openworks);
- `install/**` — установщик и миграционные скрипты;
- `core/scripts/**` — `bsl-check.py`, `build_summaries.py`, `applier_guard.py`,
  `safe_apply.py`, `validate.py`, `doctor.py`, `_root.py`;
- `core/sdd/**` — шаблоны SDD;
- `core/rules/**` — on-demand правила;
- `core/context/**` — `INSTRUCTIONS.md`, `BslChecklists.md`, `standards/`, `common/`,
  `projects/`, `.dev.env.example`;
- `docs/**` — документация;
- `README.md`, `NOTICE.md`, `THIRD_PARTY_LICENSES.md`, `SECURITY.md`, `CONTRIBUTING.md`,
  `AGENT-INSTALL.md`, `LICENSE`.

**Лицензия:** MIT (см. `LICENSE`). Copyright (c) 2025-2026 Kirill Pulyavin.

## Сторонние материалы

### Скиллы (core/skills/**)

Каталог `core/skills/**` содержит адаптированные/заимствованные скиллы из стороннего
проекта:

- **Upstream-проект:** [Nikolay-Shirokov/cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills) (GitHub)
- **Автор:** Nikolay Shirokov
- **Лицензия upstream:** MIT (выдержка — в `THIRD_PARTY_LICENSES.md`)
- **Затронутые каталоги:** `core/skills/**` — `SKILL.md` (инструкции скиллов),
  `scripts/*.ps1` и `scripts/*.py` (исполнение), `scripts/*.mjs` (скилл `web-test`,
  Node.js/Playwright), а также вспомогательные файлы (`reference.md` в `cf-edit`,
  `package.json`/`package-lock.json` в `web-test`).

**Объём заимствования (по локальным файлам):**

- 78 каталогов-скиллов, 78 файлов `SKILL.md`;
- 140 скриптов `.ps1`/`.py` + 3 скрипта `.mjs` (`web-test`);
- строку атрибуции `# Source: https://github.com/Nikolay-Shirokov/cc-1c-skills`
  содержат 142 файла (139 `.ps1`/`.py` + 3 `.mjs`).

**Атрибуция upstream в исходниках.** Скрипты в `core/skills/**/scripts/` содержат
строку атрибуции источника:

```
# Source: https://github.com/Nikolay-Shirokov/cc-1c-skills
```

(на строке 2 для `.ps1`/`.mjs`, на строке 3 для `.py`). Эти строки сохранены без
изменений. Полный текст лицензии upstream и copyright см. в `THIRD_PARTY_LICENSES.md`.

Традиционных блоков `Copyright (c) <год> <автор>` или явных заголовков «MIT License» в
файлах скиллов **не обнаружено** — присутствует только строка атрибуции источника
`# Source:`. Статус лицензии MIT относится к upstream-репозиторию; из локальных файлов
он не подтверждается напрямую (см. «Неоднозначности»).

**Адаптация.** Материалы адаптированы под данную агентскую схему:

- параметризация путей через плейсхолдеры `{{SKILL_DIR}}` / `{{SKILLS_DIR}}`
  (установщик подставляет путь целевого инструмента — Kilo/Claude/Open Works/Codex;
  см. `docs/adding-skills.md`, раздел «Правила плейсхолдеров»);
- добавлены собственные скиллы и вспомогательные скрипты, не входящие в upstream
  (см. «Неоднозначности»);
- структура приведена к единому каноническому виду `core/skills/<name>/{SKILL.md, scripts/}`.

## BSL Language Server (опционально, не входит в репозиторий)

Утилита [BSL Language Server](https://github.com/1c-syntax/bsl-language-server) (JAR) —
сторонняя, лицензия **LGPL-3.0**. В репозиторий **не входит**: скачивается пользователем
отдельно в `tools/bsl-language-server.jar` (каталог `tools/` исключён из git в
`.gitignore`). См. также раздел «Требования окружения» в `README.md`.

## Неоднозначности

- `core/skills/form-validate/scripts/check-form-structure.py` — единственный скрипт в
  `core/skills/**`, не содержащий строки атрибуции `# Source:`. По комментариям в файле
  является оригинальным добавлением проекта. Подтвердить upstream-происхождение из
  локальных файлов невозможно.
- `core/skills/web-test/**` — помимо скриптов `.mjs` (`run.mjs`, `browser.mjs`, `dom.mjs`)
  содержит `package.json`/`package-lock.json` (Node.js-проект на Playwright). Зависимости
  npm (включая Playwright, лицензия Apache-2.0) скачиваются пользователем через
  `npm install` и в репозиторий не входят (`node_modules/` исключён в `.gitignore`).
- Статус лицензии MIT для `core/skills/**` основан на upstream-репозитории
  `Nikolay-Shirokov/cc-1c-skills` и не подтверждается локальными заголовками файлов
  (в файлах есть только `# Source:`, без блока copyright/лицензии). Точный год copyright
  upstream из локальных файлов не определяется.

## Как обновлить NOTICE

Контрибьюторы при добавлении или обновлении сторонних материалов:

1. Укажите upstream-проект (URL), автора и лицензию в разделе «Сторонние материалы».
2. При появлении новой сторонней лицензии — дополните `THIRD_PARTY_LICENSES.md` выдержкой
   её текста.
3. Сохраняйте существующие строки атрибуции (`# Source:`) в исходниках; не удаляйте и не
   изменяйте оригинальные copyright-заголовки.
4. Оригинальная часть harness — под лицензией MIT (Copyright (c) 2025-2026 Kirill Pulyavin,
   см. `LICENSE`). При добавлении нового оригинального материала он автоматически попадает
   под ту же лицензию MIT. Не добавляйте сторонние материалы без указания upstream и лицензии.
