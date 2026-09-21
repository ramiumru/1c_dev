# Пример дополнительного контекстного документа (example overlay)

> Dummy-файл из examples/overlay/. Произвольный документ контекста.

Generic-категория `context/` копируется в `<contextDir>/` с сохранением
подкаталогов: `context/common/example-notes.md` → `<contextDir>/common/example-notes.md`.
Плейсхолдеры `{{CONTEXT_DIR}}`, `{{SKILLS_DIR}}`, `{{AGENTS_DIR}}`, `{{LOGS_DIR}}`
подставляются установщиком так же, как для public core.