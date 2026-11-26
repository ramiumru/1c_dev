---
name: 1c-repository-manage
description: "Хранилище конфигурации 1С: status/history/diff, захват существующих объектов, помещение с комментарием и отмена захватов задачи. Использовать по явному запросу на хранилище или при подготовке repository-enabled базы к apply. Основная конфигурация; без force/update/bind/unbind."
---

# Хранилище конфигурации: пилот 1C Dev

Все операции выполнять через `python scripts/safe_repository.py` из корня проекта.
Не запускать raw `{{SKILL_DIR}}/scripts/repo-ops.ps1`, ad-hoc 1cv8.exe и команды
обхода guard. Исполнитель — 1c-repository, координатор — 1c-do.

Поддержаны основная конфигурация, существующие объекты и точный список полных имён.
Нет force, получения версии, корня конфигурации, новых объектов, bind/unbind и расширений.
Read-only операции status/history/diff требуют настроенного профиля, но не разрешают apply.

```text
python scripts/safe_repository.py --db dev --operation status
python scripts/safe_repository.py --db dev --operation diff --objects ОбщийМодуль.МойМодуль --version 125 --background
python scripts/safe_repository.py --wait pilot-control/TASK-TEST/repository/<stamp>/receipt.json
python scripts/safe_repository.py --db dev --operation lock --objects ОбщийМодуль.МойМодуль --task TASK-TEST --intent prepare
python scripts/safe_repository.py --db dev --operation commit --objects ОбщийМодуль.МойМодуль --task TASK-TEST --intent publish --summary "уточнён комментарий метода"
python scripts/safe_repository.py --db dev --operation unlock --objects ОбщийМодуль.МойМодуль --task TASK-TEST --intent release
```

`--dry-run` проверяет только профиль, scope, политику и имеющиеся receipts. Он не
проверяет соединение и живые захваты. Для самостоятельного помещения уже имеющегося
изменения без SDD/apply этого Harness нужен `--existing-change` и явная просьба пользователя.
Для SDD-задачи этот флаг не обходит evidence успешного apply и неизменности source/spec/review.
Сначала прочитать diff. Отдельные куски объекта не публикуются: операция помещает объект целиком.

`diff` требует `--version N`: платформа 8.3.27 без номера версии хранилища отвечает
«Не указана версия хранилища». Номер последней версии взять из отчёта `status`
(`report.txt` в каталоге receipt), не выдумывать.

Длительность: запуск Конфигуратора и сравнение крупной конфигурации идут минутами.
Обёртка сама ограничивает 1cv8 (`repository.timeout_seconds`, по умолчанию 480 с) и
укладывается в 540 с вместе с записью receipt/platform.log. Вызывай её через shell-инструмент
с таймаутом 600000 мс (у Kilo по умолчанию 120 с — обёртка будет убита посреди сеанса, receipt
останется `started`, 1cv8.exe — висеть). Не поднимай timeout_seconds выше 540 без увеличения
лимита клиента. После прерывания: проверить процессы 1cv8.exe и захваты, не повторять вслепую.

`diff` и `history` на крупной конфигурации запускай с `--background`: Конфигуратор сначала
загружает всю версию хранилища (`-Objects` сужает только отчёт), это легко дольше 10 минут.
Фоновый запуск сразу возвращает путь receipt (`state=running`, лимит 1cv8 —
`repository.background_timeout_seconds`, по умолчанию 3600 с). Дальше `--wait <receipt>`:
каждый вызов ждёт до 540 с. Коды: 0 — готово, читать `report.txt`; 3 — ещё идёт, вызвать
снова; 1 — неуспех (`not_started` = 1С не запускалась, `unverified` = исход неизвестен);
4 — `stale`: дедлайн прошёл без результата. Мутации (lock/commit/unlock) в фоне не поддерживаются.

На одну базу — один сеанс Конфигуратора: обёртка держит lock-файл
`logs/repository-locks/`, пока исход операции не установлен. Любая следующая операция по этой
базе (включая dry-run и захват перед apply) отклоняется. Lock снимается сам, когда процесс 1С
завершился и записал результат. Если исход неизвестен (stale, прерванный запуск) — lock
остаётся. Снять его можно только явно: спросить пользователя, что процессов 1cv8 по этой
базе нет, затем
`python scripts/safe_repository.py --reconcile <receipt> --reason "<что подтвердил пользователь>"`.
Сверка записывается в receipt, исход остаётся `unverified` и не служит evidence для помещения.
Не вызывать `--reconcile` без подтверждения пользователя и не удалять lock-файлы вручную.

Profile `.v8-project.json`: запись базы с repository.enabled, адресом, env-именами credentials;
для мутаций — repository.allow_write, непроизводственные назначения обоих
контуров и пользовательское binding_confirmed. Право apply проверяется отдельно апликером. Самостоятельно профиль не менять. Для настройки по явному запросу пользователя
разрешён только repository_setup.py: check → plan → одобрение пользователя → save;
никакого прямого редактирования. Binding подтверждает пользователь, не результат status.

Платформенный status — отчёт хранилища; он не доказывает привязку ИБ. Binding в первом
пилоте подтверждает пользователь в Конфигураторе, результат маркируется user-confirmed.
Значения credentials читаются из Process environment, не из .dev.env; пароль не включается
в команду PowerShell. 1С получает его своим нативным ключом внутри запускающего процесса.

Комментарий по умолчанию: TASK-ID + смысловая --summary. Если пользователь дал текст,
использовать --comment. При настроенной корпоративной политике никаких fallback:
неполный шаблон/ошибка регулярного выражения/отсутствующий файл — остановка до платформы.
Встроенный серверный перехват комментария остаётся окончательным контролем.

Журналы и receipts — pilot-control/TASK-ID/repository/<run>/; status без task —
logs/repository-inspect/. Они сохраняются и при отказе/частичном результате. Отчёт о
захвате другого пользователя означает stop; не освобождать ранее существовавшие свои
или чужие захваты. После неясного commit сначала history/diff, не повторять вслепую.
Не заявлять номер версии без подтверждения по истории.

Оригинал и происхождение: comol/ai_rules_1c 44cea4dbd3a03acbe1329dcf9ce98c88a0b38c50,
1c-repository-manage/repo-ops v1.1; адаптирован для профилей/guards 1C Dev.
Платформенная основа заимствована upstream из MIT Nikolay-Shirokov/cc-1c-skills.

Для частной политики прочитай файл comment_policy_file из выбранного профиля (не меняй
его). Поля title/description передаются через --title/--description; summary остаётся
кратким описанием для нейтрального public-комментария. Ограничения field_constraints,
distinct_fields и validation_regex задаёт private-слой. Следуй его writing_instructions
при составлении текста; не выдумывай номер задачи. Готовый --comment тоже проверяется
по фактическому тексту, а не по отдельно переданным правильным полям.
