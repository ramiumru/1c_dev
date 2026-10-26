# Codex для домашнего проекта 1С: Windows, VS Code и CLI

Публичного `ramiumru/1c_dev` достаточно. Корпоративный репозиторий, overlay и корпоративные
MCP не требуются. Установщик не устанавливает VS Code, Codex, Python или платформу 1С.

## Окружение

- Windows 11 рекомендована OpenAI; Windows 10 22H2 может работать в режиме best effort.
  Отдельное desktop-приложение не требуется: используется официальное расширение Codex
  для VS Code. Совместимость конкретной сборки проверяется запуском расширения.
- Git, PowerShell 5.1+ (или PowerShell 7), Python. Для нового окружения — Python 3.11+
  при совместимости с вашей ОС; на Python ниже 3.11 TOML-проверки используют tomli.
- Для разработки 1С — установленная платформа подходящей версии и XML-выгрузка проекта.
  Базовая схема ориентирована на 8.3.27. Для анализа примеров сама база не обязательна.
- Актуальный Codex с поддержкой project-scoped custom agents. Возможности subagents
  зависят от версии клиента и настроек сессии; установщик не включает их принудительно.

Сначала авторизуйтесь в Codex и убедитесь, что он может прочитать файл в открытом проекте
и выполнить простую команду терминала. Для работы с Windows-платформой 1С предпочтителен
Windows-native shell: WSL — отдельное окружение с собственными путями и зависимостями.

## Чистая установка без corporate overlay

Из PowerShell, подставив свои пути:

```powershell
git clone https://github.com/ramiumru/1c_dev.git C:\Tools\1c_dev
python -m pip install -r C:\Tools\1c_dev\requirements-dev.txt
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Tools\1c_dev\install\install.ps1 -Tool codex -Target C:\Projects\Pet1C
cd C:\Projects\Pet1C
python scripts\doctor.py
python scripts\validate.py
```

Для PowerShell 7 замените `powershell` на `pwsh`. Откройте **C:\Projects\Pet1C** как
рабочую папку VS Code и начните новую сессию Codex после установки. Если клиент запрашивает
доверие к папке, решите его обычным способом; не обходите sandbox. Если уже есть AGENTS.md
или одноимённые агенты/skills, установщик сохраняет их. Сверьте различия до `-Force`.

Устанавливаются:

| Путь | Назначение |
|---|---|
| `AGENTS.md` | Автоматически читаемая точка входа и правила работы |
| `.codex/agents/1c-*.toml` | Семь нативных custom agents |
| `agents/1c-*.md` | Полные канонические инструкции ролей для прямого чтения |
| `.agents/skills/<name>/SKILL.md` | Нативное обнаружение skills; рядом scripts/resources |
| `context/`, `scripts/`, `specs/README.md` | Контекст, проверки и шаблоны SDD |

`.codex/config.toml`, пользовательский `~/.codex/config.toml`, модель, provider, MCP,
политики approval и sandbox **не изменяются**. Необязательный пример актуального TOML —
`adapters/codex/config.toml.example` в клоне harness; переносите только нужные настройки.
Файл `config.toml` в корне проекта и `~/.config/codex/` не являются целевыми путями этого примера.

Отсутствие исходников и профиля БД даёт предупреждения doctor, а не ошибку установки.
Для реализации поместите XML-выгрузку в `projects/pet/src/`, заполните проектный контекст
и `.dev.env` (включая свой префикс), затем создайте индекс:

```powershell
python scripts\build_summaries.py --project pet --scan --context-dir context/projects
```

Для применения в БД отдельно создайте `.v8-project.json` из примера: свои пути, база
и `environment: local`/`test`/`staging`, учётные данные только через переменные окружения.
Установка harness не создаёт базу и не запускает загрузку/обновление конфигурации.

## Проверка в живом Codex

1. В новой сессии попросите перечислить применённые инструкции и доступные skills 1С.
   Должны читаться AGENTS.md и skills из `.agents/skills`.
2. Попросите использовать `1c-analyst` как отдельного subagent для read-only анализа
   одного объекта из своей выгрузки. Проверьте фактическое создание subagent.
3. Перед реальной SDD-задачей убедитесь, что отдельный `1c-reviewer` доступен и что
   разработчик не выдаёт свою самопроверку за независимое заключение.

Установочные тесты проверяют файлы, TOML/YAML, инструкции и команды; они не заменяют
эту проверку в реальном расширении на вашем ноутбуке. Если subagents отключены или не
поддерживаются, доступны анализ и последовательная разработка. Обязательный независимый
review нельзя имитировать: соответствующий этап и apply ожидают отдельного заключения.

Роли Codex наследуют права и MCP родителя. Текстовые границы ролей не обеспечивают
техническую изоляцию, равную Kilo permission. Git-reviewer не должен вызывать MCP;
его connectors/plugins выключены в определении роли, но унаследованные MCP этим не
отключаются. БД остаётся за guard, явным пользовательским намерением и действующим
sandbox основной сессии. Модель и лимиты определяются вашим аккаунтом/настройками.

## Обновление старой установки

```powershell
git -C C:\Tools\1c_dev pull --ff-only
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Tools\1c_dev\install\install.ps1 -Tool codex -Target C:\Projects\Pet1C -Mode update
```

Ранее установленные `skills/` остаются на месте: пользовательские ресурсы не удаляются.
Новые native skills находятся в `.agents/skills`; там и должны обнаруживаться клиентом.
Если старый AGENTS.md изменяли вручную, обновление сохраняет его: объедините инструкции
с актуальным шаблоном или после просмотра различий используйте `-Force` (создаёт backup).
Native agents/skills с пользовательскими изменениями сохраняются и при повторном update.
После обновления начните новую сессию, повторите doctor/validate и проверку обнаружения.

## Официальные источники

- [Codex в IDE](https://learn.chatgpt.com/docs/ide)
- [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Skills](https://learn.chatgpt.com/docs/build-skills)
- [Custom subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Конфигурация CLI и IDE](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Windows](https://learn.chatgpt.com/docs/windows/windows-sandbox)
