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
python scripts/safe_repository.py --db dev --operation diff --objects ОбщийМодуль.МойМодуль
python scripts/safe_repository.py --db dev --operation lock --objects ОбщийМодуль.МойМодуль --task TASK-TEST --intent prepare
python scripts/safe_repository.py --db dev --operation commit --objects ОбщийМодуль.МойМодуль --task TASK-TEST --intent publish --summary "уточнён комментарий метода"
python scripts/safe_repository.py --db dev --operation unlock --objects ОбщийМодуль.МойМодуль --task TASK-TEST --intent release
```

`--dry-run` проверяет только профиль, scope, политику и имеющиеся receipts. Он не
проверяет соединение и живые захваты. Для самостоятельного помещения уже имеющегося
изменения без SDD/apply этого Harness нужен `--existing-change` и явная просьба пользователя.
Для SDD-задачи этот флаг не обходит evidence успешного apply и неизменности source/spec/review.
Сначала прочитать diff. Отдельные куски объекта не публикуются: операция помещает объект целиком.

Profile `.v8-project.json`: запись базы с repository.enabled, адресом, env-именами credentials;
для мутаций — repository.allow_write, непроизводственные назначения обоих
контуров и пользовательское binding_confirmed. Право apply проверяется отдельно апликером. Самостоятельно профиль не менять.

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
