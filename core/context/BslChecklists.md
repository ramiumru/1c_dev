# AGENTS.md — инструкции для агентов

## Проверка BSL-кода

После правки BSL-кода в `projects/**/src/**` — проверить изменения по
**чек-листу ручной верификации** (основной способ, не требует терминала):

- **Синтаксис:** соответствие конструкции языку BSL 8.3.27.
- **Имена:** кириллица, без транслита, соответствие стандартам 1С.
- **Области:** `#Область ... #КонецОбласти` для публичных/внутренних методов.
- **Запросы:** вместо циклов по выборке — запросы на больших объёмах.
- **Транзакции:** блокировки данных, обработка ошибок.
- **Документирование:** экспортные процедуры/функции с doc-комментарием
  (Описание/Параметры/Возвращаемое значение); при правке существующих — актуализировать
  (level §8.7).
- **Формы:** бизнес-логика вне форм, формы — только UI и вызовы сервера.
- **Кодировка:** BSL-модули хранятся как UTF-8 (BOM) + CRLF; нативный `edit`
  сохраняет кодировку автоматически.
- **SDD:** для нетривиальной правки проверять наличие `specs/<TASK-ID>/` с
  непустым `03_solution_spec.md` и заполненным `06_change_report.md`; комментарии
  `// ++ #<TASK-ID>` / `// -- #<TASK-ID>` соответствуют task-папке. Для тривиальных
  правок (опечатка, комментарий) SDD не требуется. См. `INSTRUCTIONS.md`, раздел
  «Spec-Driven Development (SDD)» и `specs/README.md`.

## Автолинт BSL через `bsl-check.py` (структурная проверка)

После правки BSL-модулей — запустить структурную проверку (доступно `1c-developer`
через `bash` whitelist):

```bash
python scripts/bsl-check.py projects/<источник>/src/.../Module.bsl
```

Проверяет (без парсера BSL — эвристика по строкам):
- кодировку UTF-8 BOM;
- переводы строк CRLF (отсутствие lone LF);
- баланс парных конструкций (`Если/КонецЕсли`, `Процедура/КонецПроцедуры`,
  `Функция/КонецФункции`, `Цикл/КонецЦикла`, `#Область/#КонецОбласти`);
- баланс маркеров изменений `// ++ #TASK` / `// -- #TASK`;
- код вне процедур/функций (orphaned).

ERROR — исправить перед возвратом; WARN (lone LF, дисбаланс маркеров) — исправить
при возможности. Скрипт не заменяет BSL Language Server (нет синтаксического
парсера), но ловит типовые структурные ошибки модели (orphaned-код, опечатки в
парных конструкциях, нарушение кодировки). Запуск — в шаге 10 алгоритма
`1c-developer` (после `*-validate` для XML).

## Проверка XML-метаданных через скиллы

После правки XML-метаданных (реквизиты, ТЧ, формы, макеты, СКД, роли, подсистемы,
XDTO) через edit/compile-скиллы (`meta-edit`, `form-edit`, `form-compile`,
`mxl-compile`, `skd-edit`, `role-compile`, `subsystem-edit`, `xdto-edit`, `cf-edit`
и др.) — запустить соответствующий `*-validate` скилл по пути объекта:
`meta-validate`, `form-validate`, `mxl-validate`, `skd-validate`, `role-validate`,
`subsystem-validate`, `xdto-validate`, `interface-validate`, `cf-validate`,
`cfe-validate`. Это проверка структуры XML (не BSL-кода) — дополняет чек-лист
ручной верификации выше. Доступно у `1c-developer` (см. секцию «Скиллы разработки»
в промпте агента).

## Опциональный автолинт через BSL Language Server

Если в окружении доступны `tools/bsl-language-server.jar` и JRE 17+ (а у агента
разрешён `bash`), автолинт можно запустить вручную:

```bash
java -jar tools/bsl-language-server.jar analyze -r json -o tools/bsl-diagnostics.json projects
```

Где:
- `tools/bsl-language-server.jar` — BSL Language Server (1c-syntax/bsl-language-server);
- `-r json` — формат отчёта JSON;
- `-o tools/bsl-diagnostics.json` — файл с результатами;
- `projects` — путь к проверяемым исходникам (все подкаталоги `projects/<источник>/src/**`).

После запуска — разобрать `tools/bsl-diagnostics.json`, исправить диагностики
уровня `ERROR` и `WARNING`, связанные с изменённым файлом.

### Предварительные требования (опционально)

- Java Runtime Environment (JRE) 17+ в `PATH` или в `tools/jre/`.
- BSL Language Server JAR в `tools/bsl-language-server.jar`.
  Скачать: https://github.com/1c-syntax/bsl-language-server/releases

### Установка (однократно, при необходимости автолинта)

1. Скачать `bsl-language-server-<version>.jar` из GitHub releases.
2. Переименовать/скопировать в `tools/bsl-language-server.jar`.
3. Убедиться, что `java -version` работает (или использовать
   `tools/jre/bin/java`).
4. Проверить: `java -jar tools/bsl-language-server.jar --version`.

### Примечание об окружении агентов

У агентов `1c-analyst` и `1c-developer` доступен `bash` через whitelist
скилл-скриптов (`powershell.exe -NoProfile -File .kilo/skills/<skill>/...`):
- `1c-analyst` — 9 info-скиллов (read-only анализ структуры: `meta-info`,
  `form-info` и др.);
- `1c-developer` — ~58 скиллов (info + edit/compile/validate/decompile).
Автолинт через `java -jar tools/bsl-language-server.jar` по-прежнему недоступен
автоматически (whitelist покрывает только скилл-скрипты, не `java`), поэтому
основным способом проверки BSL остаётся чек-лист ручной верификации выше.
Для запуска BSL Language Server пользователь может либо запустить его вручную
вне агента, либо временно расширить `bash` whitelist.

## Регенерация summaries через 1c-tools

Скрипт `scripts/build_summaries.py` обновляет `.kilo/context/projects/<проект>/summaries/**`
(per-project, с `.cache.json` в подпапке проекта) по объектам из `.kilo/context/projects/<проект>/objects-index.md`
(инкрементально по hash исходников; проект выводится из пути объекта).

Запуск автоматизирован субагентом `1c-tools` (`mode: subagent`, `bash` whitelist
из 9 форм `python scripts/build_summaries.py ...`):

- **Явный запрос**: пользователь → `1c-do` → Task → `1c-tools` →
  `python scripts/build_summaries.py --objects "A,B,C"`.
- **Авто post-SDD**: после шага 6 SDD `1c-do` читает `06_change_report.md`,
  извлекает затронутые объекты и делегирует `1c-tools`.

`1c-tools` использует только эвристический режим (без `--use-llm`). LLM-режим
(`--use-llm` через LiteLLM API) — только ручной запуск пользователем вне сессии
(требует `LITELLM_API_BASE`/`LITELLM_API_KEY`).

Требование окружения: Python 3.x в `PATH`.
