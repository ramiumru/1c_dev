#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""git_readonly.py — контролируемый read-only интерфейс к Git для 1c-git-reviewer.

Назначение: дать агенту 1c-git-reviewer безопасный доступ к Git-истории проекта,
не выдавая unrestricted bash. Все операции проходят через строгий whitelist:

Разрешённые subcommand'ы (только чтение + контролируемый fetch):
  status, remote, ls-remote, fetch, log, show, diff, merge-base,
  branch (только чтение), cat-file, rev-parse, cherry

Правила fetch: только явные имена веток (explicit refspecs),
без флагов; обновляются только remote-tracking refs.

Запрещено (отвергается с exit 2): checkout, switch, reset, merge, rebase,
commit, push, pull, clean, stash, config и любые неизвестные subcommand'ы/флаги.

Выход: 0 — успешно; 2 — операция отвергнута whitelist'ом; 3 — окружение
(git не найден, репозиторий не существует); иначе — код ошибки git.

Примеры:
  python scripts/git_readonly.py --repo C:/proj status --short
  python scripts/git_readonly.py --repo C:/proj fetch origin dev prod
  python scripts/git_readonly.py --repo C:/proj log origin/dev --oneline --grep=123125756
  python scripts/git_readonly.py --repo C:/proj show --stat abc1234
  python scripts/git_readonly.py --repo C:/proj diff origin/prod origin/dev -- src/x.bsl
  python scripts/git_readonly.py --repo C:/proj merge-base --is-ancestor abc1234 origin/prod
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

# --- Шаблоны валидации токенов ---

# Имя remote/ветки для fetch: ASCII, без :/@, без ведущего '-'
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

def _looks_absolute(p: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:", p)) or p.startswith(("/", "\\"))

def _is_revlike(a: str) -> bool:
    """Ревизия/rev:path: первый символ — буква/цифра/_ (или ведущий ^); остальное —
    печатаемые символы (включая Unicode-пути 1С). Флаг-инъекция невозможна:
    позиционный токен не может начинаться с '-'."""
    s = a[1:] if a.startswith("^") else a
    if not s or not re.match(r"^[A-Za-z0-9_\u0080-\uffff]", s):
        return False
    return not any(ord(c) < 0x20 or ord(c) == 0x7f for c in s)

# Числовой короткий флаг: -5, -U3 (лимиты/контекст diff)
NUM_FLAG = re.compile(r"^-[A-Za-z]?[0-9]+$")

# Опасные env-переменные git, способные перенаправить операции — вычищаются из окружения потомка
GIT_ENV_DENY = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR",
)

FORBIDDEN_SUBCOMMANDS = [
    "checkout", "switch", "reset", "merge", "rebase", "commit", "push", "pull",
    "clean", "stash", "config", "apply", "am", "revert", "cherry-pick", "restore",
    "worktree", "submodule", "remote set-url", "tag", "init", "clone", "mv", "rm",
    "add", "update-index", "gc", "reflog", "filter-branch", "replace", "notes",
    "send-pack", "receive-pack", "upload-pack", "fast-import", "fast-export",
    "archive", "bundle", "sparse-checkout", "maintenance", "run", "hook",
]


def _reject(message: str) -> None:
    print(f"git_readonly: ОТВЕРГНУТО: {message}", file=sys.stderr)
    print(
        "Разрешены только read-only subcommand'ы: status, remote, ls-remote, fetch "
        "(явные ветки), log, show, diff, merge-base, branch (чтение), cat-file, "
        "rev-parse, cherry. Подробности: python scripts/git_readonly.py --help",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _check_paths(paths: list[str]) -> None:
    for p in paths:
        if not p:
            continue
        if re.match(r"^[A-Za-z]:", p) or p.startswith(("/", "\\")):
            _reject(f"абсолютный путь не разрешён: {p[:80]}")
        if ".." in p:
            _reject(f"путь с '..' не разрешён: {p[:80]}")


def _val_flag(arg: str, allowed_prefixes: tuple[str, ...]) -> str:
    """Проверить флаг вида --name=value; вернуть значение."""
    name, _, value = arg.partition("=")
    if not any(name == p or name.startswith(p) for p in allowed_prefixes):
        _reject(f"неизвестный флаг: {arg[:80]}")
    if not value:
        _reject(f"пустое значение флага: {arg[:80]}")
    return value


def build_args(sub: str, args: list[str]) -> list[str]:
    """Собрать безопасный argv для git по subcommand и аргументам. Мутации исключены."""
    out: list[str] = []
    i = 0
    n = len(args)

    def next_pos() -> str:
        nonlocal i
        if i >= n:
            _reject(f"{sub}: ожидался позиционный аргумент")
        a = args[i]
        i += 1
        return a

    while i < n:
        a = args[i]
        if a == "--":
            i += 1
            paths = args[i:]
            _check_paths(paths)
            out.append("--")
            out.extend(paths)
            i = n
            break
        if a.startswith("--"):
            key = a.split("=", 1)[0]
            if sub == "status":
                if key in ("--short", "--porcelain", "--long", "-b"):
                    out.append(a)
                else:
                    _reject(f"status: флаг не разрешён: {a[:80]}")
            elif sub == "remote":
                if key in ("-v", "--verbose"):
                    out.append(a)
                else:
                    _reject(f"remote: флаг не разрешён: {a[:80]} (только -v)")
            elif sub == "ls-remote":
                if key in ("--heads", "--tags"):
                    out.append(a)
                else:
                    _reject(f"ls-remote: флаг не разрешён: {a[:80]}")
            elif sub == "log":
                if key in ("--oneline", "--all", "--stat", "--shortstat", "--name-only",
                           "--name-status", "--abbrev-commit", "-i", "--regexp-ignore-case"):
                    out.append(a)
                elif key in ("--grep", "--format", "--pretty", "--date", "--author",
                             "--since", "--until"):
                    _val_flag(a, (key,))
                    out.append(a)
                else:
                    _reject(f"log: флаг не разрешён: {a[:80]}")
            elif sub == "show":
                if key in ("--stat", "--shortstat", "--name-only", "--name-status",
                           "--abbrev-commit", "-s"):
                    out.append(a)
                elif key in ("--format", "--pretty", "--date"):
                    _val_flag(a, (key,))
                    out.append(a)
                else:
                    _reject(f"show: флаг не разрешён: {a[:80]}")
            elif sub == "diff":
                if key in ("--stat", "--shortstat", "--name-only", "--name-status",
                           "--numstat", "--minimal", "-w", "--ignore-all-space",
                           "--ignore-space-change"):
                    out.append(a)
                elif key == "-U":
                    _val_flag(a, ("-U",))
                    out.append(a)
                else:
                    _reject(f"diff: флаг не разрешён: {a[:80]}")
            elif sub == "merge-base":
                if key in ("--is-ancestor", "--all"):
                    out.append(a)
                else:
                    _reject(f"merge-base: флаг не разрешён: {a[:80]}")
            elif sub == "branch":
                if key in ("-a", "-r", "--all", "--remotes", "--list"):
                    out.append(a)
                elif key in ("--contains", "--format"):
                    if key == "--format":
                        _val_flag(a, (key,))
                        out.append(a)
                    else:
                        out.append(a)
                        rev = next_pos()
                        if not _is_revlike(rev) or _looks_absolute(rev):
                            _reject(f"branch --contains: недопустимая ревизия: {rev[:80]}")
                        out.append(rev)
                else:
                    _reject(f"branch: флаг не разрешён: {a[:80]} (только чтение: -a/-r/--contains)")
            elif sub == "cat-file":
                if key in ("-t", "-s", "-e"):
                    out.append(a)
                else:
                    _reject(f"cat-file: флаг не разрешён: {a[:80]} (только -t/-s/-e)")
            elif sub == "rev-parse":
                if key in ("--verify", "--short", "--abbrev-ref", "--symbolic-full-name"):
                    out.append(a)
                else:
                    _reject(f"rev-parse: флаг не разрешён: {a[:80]}")
            elif sub == "cherry":
                if key in ("-v", "--verbose"):
                    out.append(a)
                else:
                    _reject(f"cherry: флаг не разрешён: {a[:80]}")
            else:
                _reject(f"{sub}: флаги не ожидались: {a[:80]}")
            i += 1
            continue
        if a.startswith("-") and a != "-":
            # короткие флаги (только чтение): -5/-U3/-s/-w, status -sb/-s, branch -a/-r, cherry -v
            if sub in ("log", "show", "diff") and (NUM_FLAG.match(a) or a in ("-s", "-w")):
                out.append(a)
                i += 1
                continue
            if sub == "status" and a in ("-sb", "-s", "-b"):
                out.append(a)
                i += 1
                continue
            if sub == "branch" and a in ("-a", "-r", "-v", "-vv"):
                out.append(a)
                i += 1
                continue
            if sub == "cherry" and a in ("-v",):
                out.append(a)
                i += 1
                continue
            if sub == "remote" and a in ("-v",):
                out.append(a)
                i += 1
                continue
            if sub == "cat-file" and a in ("-t", "-s", "-e"):
                out.append(a)
                i += 1
                continue
            _reject(f"{sub}: короткий флаг не разрешён: {a[:80]}")
        # позиционный аргумент
        if not _is_revlike(a) or _looks_absolute(a):
            _reject(f"{sub}: недопустимый позиционный аргумент: {a[:80]}")
        if sub in ("show", "cat-file") and ":" in a and ".." in a.split(":", 1)[1]:
            _reject(f"{sub}: rev:path с '..' не разрешён: {a[:80]}")
        if sub == "branch":
            # позиционный без --contains = создание ветки — запрещено
            _reject(f"branch: позиционные аргументы не разрешены (только чтение): {a[:80]}")
        elif sub in ("status", "remote"):
            _reject(f"{sub}: позиционные аргументы не разрешены: {a[:80]}")
        elif sub == "ls-remote" and not SAFE_TOKEN.match(a) and not re.match(r"^[A-Za-z0-9_/*\[\]-]+$", a):
            _reject(f"ls-remote: недопустимый remote/ref-шаблон: {a[:80]}")
        out.append(a)
        i += 1
    return out


def build_fetch_args(args: list[str]) -> list[str]:
    """fetch: только [remote] + непустой список явных веток. Без флагов.

    `git fetch origin dev prod` обновляет только refs/remotes/origin/{dev,prod} —
    рабочее дерево и локальные ветки не затрагиваются.
    """
    if not args:
        _reject("fetch: требуется хотя бы одна явная ветка (explicit refspec)")
    for a in args:
        if a.startswith("-"):
            _reject(f"fetch: флаги не разрешены (только имена веток): {a[:80]}")
        if not SAFE_TOKEN.match(a):
            _reject(f"fetch: недопустимое имя remote/ветки: {a[:80]}")
    if len(args) == 1:
        _reject("fetch: укажите remote и хотя бы одну ветку (напр.: fetch origin dev)")
    remote, branches = args[0], args[1:]
    if not branches:
        _reject("fetch: пустой список веток")
    return [remote] + branches


HELP = """git_readonly.py — контролируемый read-only Git для 1c-git-reviewer.

Использование:
  python scripts/git_readonly.py [--repo <путь>] [--timeout <сек>] <subcommand> [аргументы...]

Wrapper-флаги (--repo, --timeout) распознаются только ДО subcommand; всё, что
идёт после subcommand, передаётся git после строгой валидации.

Разрешённые subcommand'ы:
  status [--short|--porcelain|--long|-sb|-b]          — состояние рабочего дерева
  remote [-v]                                          — список remote
  ls-remote [--heads] [remote] [ref-шаблон]           — актуальные remote refs (без fetch)
  fetch <remote> <ветка> [<ветка>...]                  — контролируемый fetch ТОЛЬКО
                                                        явных веток (remote-tracking refs)
  log [флаги] [<rev>...] [-- <пути>]                   — история (--oneline, --all,
                                                        --grep=, --format=, --name-only, ...)
  show [флаги] <rev[:путь]>... [-- <пути>]             — коммит/diff/файл по ref
  diff [флаги] [<rev> <rev>] [-- <пути>]               — точечный diff между refs
  merge-base [--is-ancestor] <rev> <rev>               — базы/предки
  branch -a|-r|--contains <rev>                        — ветки (только чтение)
  cat-file -t <объект>                                 — тип объекта
  rev-parse [--verify] <rev>...                        — SHA по ref
  cherry [-v] <upstream> <head>                        — патч-эквивалентность

Запрещены (read-only политика): checkout, switch, reset, merge, rebase, commit,
push, pull, clean, stash, config и любые другие subcommand'ы и флаги вне whitelist.
Fetch без явных веток и флаги fetch (--all, --prune, --depth, refspec с ':')
запрещены.

Выход: 0 — успешно; 2 — отвергнуто whitelist'ом; 3 — окружение/таймаут; иначе код git.
"""

ALLOWED_SUBS = ("status", "remote", "ls-remote", "fetch", "log", "show", "diff",
                "merge-base", "branch", "cat-file", "rev-parse", "cherry")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # Ручной разбор argv: wrapper-флаги только до subcommand, хвост — вербатимно.
    argv = sys.argv[1:]
    repo = "."
    timeout = 180
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(HELP)
            return 0
        if a == "--repo":
            i += 1
            if i >= len(argv) or not argv[i]:
                _reject("--repo: требуется путь к репозиторию")
            repo = argv[i]
        elif a.startswith("--repo="):
            repo = a.split("=", 1)[1]
            if not repo:
                _reject("--repo=: пустой путь")
        elif a == "--timeout":
            i += 1
            if i >= len(argv) or not argv[i].isdigit():
                _reject("--timeout: требуется положительное число секунд")
            timeout = int(argv[i])
        elif a.startswith("--timeout="):
            v = a.split("=", 1)[1]
            if not v.isdigit():
                _reject("--timeout=: требуется положительное число секунд")
            timeout = int(v)
        elif a.startswith("-") and a != "-":
            _reject(f"неизвестный wrapper-флаг: {a[:80]}")
        else:
            break
        i += 1

    if i >= len(argv):
        print("git_readonly: не указан subcommand. " + HELP.splitlines()[0], file=sys.stderr)
        return 2
    sub = argv[i]
    sub_args = argv[i + 1:]

    if sub in FORBIDDEN_SUBCOMMANDS or " " in sub:
        _reject(f"subcommand не разрешён (read-only): {sub}")
    if sub not in ALLOWED_SUBS:
        _reject(f"неизвестный subcommand: {sub}")

    if sub == "fetch":
        git_args = build_fetch_args(sub_args)
    else:
        git_args = build_args(sub, sub_args)

    git = shutil.which("git")
    if not git:
        print("git_readonly: git не найден в PATH", file=sys.stderr)
        return 3

    argv_git = [git, "-C", repo, "-c", "core.quotepath=false", sub] + git_args
    # Изолировать потомка от GIT_* env-override'ов (GIT_DIR/GIT_WORK_TREE/...)
    # и запретить интерактивные запросы учётных данных.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"

    try:
        r = subprocess.run(argv_git, capture_output=True, timeout=max(10, timeout), env=env)
    except subprocess.TimeoutExpired:
        print(f"git_readonly: таймаут {timeout}s: {' '.join(argv_git[:6])} ...", file=sys.stderr)
        return 3
    except FileNotFoundError:
        print("git_readonly: не удалось запустить git", file=sys.stderr)
        return 3

    if r.stdout:
        sys.stdout.buffer.write(r.stdout)
    if r.stderr:
        sys.stderr.buffer.write(r.stderr)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
