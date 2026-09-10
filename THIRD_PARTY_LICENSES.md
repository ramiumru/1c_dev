# THIRD_PARTY_LICENSES.md — лицензии сторонних материалов

Настоящий файл содержит выдержки лицензий сторонних материалов, используемых в данном
репозитории. Полный текст соответствующей лицензии см. в upstream-источнике.

## Скиллы (core/skills/**) — upstream Nikolay-Shirokov/cc-1c-skills

- **Upstream-проект:** https://github.com/Nikolay-Shirokov/cc-1c-skills
- **Автор:** Nick Shirokov
- **Лицензия upstream:** MIT
- **Затронутые каталоги:** `core/skills/**` (адаптированные/заимствованные скиллы:
  `SKILL.md`, `scripts/*.ps1`, `scripts/*.py`, `scripts/*.mjs` для `web-test`,
  вспомогательные файлы). Из локальных файлов статус лицензии MIT подтверждается
  upstream-репозиторием; в самих файлах присутствует только строка атрибуции
  `# Source: https://github.com/Nikolay-Shirokov/cc-1c-skills` (без блока copyright/лицензии).

### Выдержка лицензии MIT

```
MIT License

Copyright (c) 2025-2026 Nick Shirokov

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

> Год copyright (2025-2026) и имя (Nick Shirokov) — из upstream LICENSE:
> https://github.com/Nikolay-Shirokov/cc-1c-skills/blob/main/LICENSE

## BSL Language Server (опционально, не входит в репозиторий)

- **Проект:** https://github.com/1c-syntax/bsl-language-server
- **Лицензия:** LGPL-3.0
- Не входит в репозиторий: скачивается пользователем отдельно в
  `tools/bsl-language-server.jar` (каталог `tools/` исключён из git в `.gitignore`).

## npm-зависимости скилла web-test (опционально, не входят в репозиторий)

Скилл `core/skills/web-test` использует Node.js/Playwright. Зависимости (включая
Playwright, лицензия Apache-2.0) скачиваются пользователем через `npm install` и в
репозиторий не входят (`node_modules/` исключён в `.gitignore`).

## Оригинальная часть проекта

Лицензия оригинальной части (`core/agents`, `adapters`, `install`, `core/scripts`,
`core/sdd`, `core/context`, `docs`) **не определена владельцем**. Выбор лицензии —
прерогатива владельца; настоящий файл не выбирает лицензию от имени владельца.
