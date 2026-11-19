# Git review: выбрать нужный локальный клон до поиска коммитов

Номер задачи сам по себе не определяет репозиторий. Git reviewer сначала вызывает
`git_readonly.py resolve`, сверяет локальный remote с сервером и полным путём проекта
из ссылки и только при ready переходит к истории/сети. Если есть только клон main,
ссылка на extension не запускает поиск коммитов в main.

## Список клонов в корпоративном слое

Шаблон — [git-repositories.example.json](../examples/git-repositories.example.json).
Заполненный файл — `<CONTEXT_DIR>/projects/<проект>/git-repositories.json`, например
в private overlay категории projects. Локальные path считаются от корня рабочего
workspace, не от папки JSON. Абсолютный путь допускается, но права клиента продолжают
действовать; это не разрешение обходить external_directory. Сам registry должен
находиться внутри workspace. Файл не изменяется агентом и не содержит пароли/токены.
Public installer доставляет только пример, не создаёт реальные клоны или профиль.

Запись содержит id, remote, path. Пустой/повреждённый реестр, дубли id и отсутствующее
поле останавливают preflight. Remote — полный сетевой URL (SSH/SCP/HTTP/HTTPS/Git).
Стандартные SSH и HTTPS адреса одного server/namespace/project эквивалентны; namespace
и path не сокращаются. Нестандартные порты и SSH host aliases не угадываются. Для них
можно явно задать remote_aliases (список URL того же полного project path). Fork с
другим namespace — отдельная запись, а не alias основной конфигурации.

```text
python scripts/git_readonly.py resolve --workspace . --registry .kilo/context/projects/example/git-repositories.json --task-url https://git.example.invalid/team/extension/-/work_items/731
python scripts/git_readonly.py resolve --workspace . --registry .kilo/context/projects/example/git-repositories.json --repository extension
python scripts/git_readonly.py resolve --workspace . --task-url https://git.example.invalid/team/extension/-/issues/731 --path projects/extension
```

Для OpenWork/Claude/Codex используется их установленный CONTEXT_DIR; новый shell
whitelist не нужен, это subcommand существующего wrapper (python или py -3).
При ready JSON содержит проверенные path, remote и repository. Например, если совпал
remote upstream, дальнейшие команды работают с upstream/dev, а не с origin/dev.
review_started=false означает, что эта команда лишь выбирает клон и не проводит ревью.
Локальный Git без remote допустим только при явном --path и отсутствии внешней
ссылки/ожидаемого remote: identity_basis=explicit_local_path, remote=null. Ревью
идёт по локальным refs без заявления о серверной актуальности. Ссылка на внешний
проект при отсутствии remote по-прежнему блокируется как identity_unverified.

При нескольких записях один номер задачи требует выбора id или ссылки, даже если
часть клонов ещё не создана. Если совпадающих копий несколько, нужен явный path.
Несовпадение явно указанного path со ссылкой — блокер, без молчаливой замены клона.
Настроенная запись с отсутствующим path даёт быстрый missing: соседние клоны не ищутся.

## Что происходит без реестра

Проверяются только собственные Git-корни workspace, непосредственных дочерних папок,
projects/* и projects/*/src. .git-файл worktree/submodule поддерживается. Нет рекурсивного
обхода диска: не более 64 entries в каждом из двух каталогов и 24 Git-кандидатов;
общий бюджет Git-проб 15 секунд, одна команда не более 3 секунд. Команды проб — только
rev-parse --show-toplevel и remote -v, без log/fetch/ls-remote/clone и без сети.
Если достигнут лимит или доступ запрещён, результат неполный; нельзя утверждать,
что клон отсутствует на компьютере. Укажите точный path/registry вместо повторных поисков.

Missing, mismatch, ambiguous, access_denied и incomplete — разные результаты. Exit 0
только у ready. При блокере достаточно ожидаемого проекта, проверенной причины и
одного следующего действия. Шаблон ответа — в роли Git reviewer. Публичный wrapper
не является отдельной sandbox: доступ к файлам/процессам ограничивает сам клиент.

## Задача на несколько репозиториев

Проект задачи в ссылке не гарантирует полный code scope. Пользователь явно подтверждает
список репозиториев; каждый проверяется отдельно по id. Для такого подтверждённого
cross-project scope вызов по id не содержит task-url, но исходная ссылка сохраняется
в отчёте. Автоматического обхода GitLab API/MCP для поиска связей нет. Без разрешения
на частичное ревью недоступная обязательная часть даёт общий неполный результат.
Реестр не меняет SDD, apply или настройки хранилища конфигурации 1С.
