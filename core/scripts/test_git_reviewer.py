r"""test_git_reviewer.py — регрессионные тесты read-only Git-инструмента 1c-git-reviewer.

Полностью детерминированный набор: временные локальные репозитории + локальный
bare-«remote» (file://), без сети и без корпоративного GitLab.

Проверяет:
1. Whitelist: разрешённые read-only subcommand'ы работают на тестовом репозитории.
2. Запрет мутирующих операций: checkout/commit/push/config/merge/... → exit 2.
3. Запрет опасных флагов: fetch --all, refspec с ':', log --upload-pack, абсолютные пути.
4. Fetch: только явные ветки; обновляет remote-tracking refs; рабочее дерево,
   локальная ветка и незакоммиченные изменения пользователя не затрагиваются.
5. Поиск коммитов задачи по grep (несколько коммитов одной задачи).
6. merge-base --is-ancestor: отсутствие SHA не является ошибкой (exit 1).

NOT AUTOMATED (осознанно, LLM-поведение агента, не код): однозначность
привязки коммитов к задаче при отсутствии номера, классификация
PRESENT/MISSING/PARTIAL/DIFFERENT/UNCERTAIN по содержимому, отказ от
prod..dev как diff задачи. Эти инварианты закреплены в теле агента
(core/agents/1c-git-reviewer.md) и проверяются validate.py статически.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WRAPPER = SCRIPT_DIR / "git_readonly.py"


def run_git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(cwd), "-c", "core.quotepath=false", *args],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr}")
    return r


def wrap(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(WRAPPER), "--repo", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


def commit_file(repo: Path, rel: str, content: str, message: str) -> str:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    run_git(repo, "add", rel)
    run_git(repo, "commit", "--allow-empty", "-m", message,
            "--author", "tester <t@example.com>")
    return run_git(repo, "rev-parse", "HEAD").stdout.strip()


def make_fixture() -> tuple[Path, Path, Path]:
    """Локальный «корпоративный» remote (bare) + clone с ветками dev/prod.

    История:
      base -- #100001 init (dev, prod)
             \- #100111 fix A (dev)      <- задача 100111, 1-й коммит
                \ #100111 fix B (dev)     <- задача 100111, 2-й коммит
                   \ #100222 other (dev)  <- посторонняя задача
    """
    origin_path = Path(tempfile.mkdtemp(prefix="grt_origin_"))
    work_path = Path(tempfile.mkdtemp(prefix="grt_work_"))

    # bare «сервер»
    run_git(origin_path, "init", "--bare", "--initial-branch", "dev", ".")
    # временный seed-репозиторий, чтобы наполнить bare
    seed = work_path / "seed"
    seed.mkdir()
    run_git(seed, "init", "--initial-branch", "dev", ".")
    run_git(seed, "config", "user.name", "tester")
    run_git(seed, "config", "user.email", "t@example.com")
    commit_file(seed, "src/ОбщийМодуль/Ext/Module.bsl",
                "// базовый модуль\n", "#100001 init")
    run_git(seed, "push", str(origin_path), "dev")

    # clone у «пользователя»
    clone = work_path / "clone"
    run_git(work_path, "clone", str(origin_path), "clone")
    run_git(clone, "config", "user.name", "tester")
    run_git(clone, "config", "user.email", "t@example.com")
    run_git(clone, "fetch", "origin", "dev:refs/remotes/origin/dev")

    # prod из dev base
    run_git(clone, "push", "origin", "origin/dev:refs/heads/prod")
    run_git(clone, "fetch", "origin", "dev", "prod")

    # задача 100111: два коммита в dev
    commit_file(clone, "src/Документ/Ext/Module.bsl",
                "// ++ #100111\nПроцедура А() КонецПроцедуры\n// -- #100111\n",
                "#100111 fix A")
    commit_file(clone, "src/ОбщийМодуль/Ext/Module.bsl",
                "// базовый модуль\n// ++ #100111 дополнение\n",
                "#100111 fix B")
    # посторонний коммит
    commit_file(clone, "src/Прочее/Ext/Module.bsl", "// прочее\n", "#100222 other")
    run_git(clone, "push", "origin", "dev")
    run_git(clone, "fetch", "origin", "dev")
    return origin_path, work_path / "clone", clone


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if not shutil.which("git"):
        print("SKIP: git не найден в PATH — тесты read-only Git не выполнены")
        return 0

    results: list[tuple[str, bool, str]] = []
    origin_path, _root, clone = make_fixture()

    try:
        # --- 1. Разрешённые read-only операции ---
        r = wrap(clone, "status", "--short")
        results.append(("allow: status --short", r.returncode == 0, r.stderr[:100]))
        r = wrap(clone, "remote", "-v")
        results.append(("allow: remote -v", r.returncode == 0 and "origin" in r.stdout, r.stderr[:100]))
        r = wrap(clone, "ls-remote", "--heads", "origin")
        results.append(("allow: ls-remote --heads origin",
                        r.returncode == 0 and "refs/heads/dev" in r.stdout, r.stderr[:100]))
        r = wrap(clone, "log", "origin/dev", "--oneline")
        results.append(("allow: log origin/dev --oneline",
                        r.returncode == 0 and "#100222" in r.stdout, r.stderr[:100]))
        sha_b = run_git(clone, "rev-parse", "origin/dev").stdout.strip()
        r = wrap(clone, "show", "--stat", sha_b)
        results.append(("allow: show --stat <sha>", r.returncode == 0, r.stderr[:100]))
        r = wrap(clone, "diff", "origin/dev", "origin/prod", "--", "src/Документ/Ext/Module.bsl")
        results.append(("allow: diff точечный по файлу",
                        r.returncode == 0 and "#100111" in r.stdout, r.stderr[:100]))
        r = wrap(clone, "cat-file", "-t", sha_b)
        results.append(("allow: cat-file -t", r.returncode == 0 and "commit" in r.stdout, r.stderr[:100]))
        r = wrap(clone, "rev-parse", "--verify", "origin/prod")
        results.append(("allow: rev-parse --verify", r.returncode == 0, r.stderr[:100]))
        r = wrap(clone, "branch", "-a")
        results.append(("allow: branch -a (чтение)", r.returncode == 0 and "dev" in r.stdout, r.stderr[:100]))

        # --- 2. Запрет мутирующих subcommand'ов ---
        for bad in (["push", "origin", "dev"], ["commit", "-m", "x"], ["checkout", "dev"],
                    ["config", "user.name", "x"], ["merge", "dev"], ["reset", "--hard"],
                    ["stash"], ["clean", "-fd"], ["switch", "dev"], ["rebase", "dev"],
                    ["tag", "v1"], ["worktree", "add", "../x"], ["clone", str(origin_path)]):
            r = wrap(clone, *bad)
            results.append((f"deny: {' '.join(bad[:2])}", r.returncode == 2, f"exit={r.returncode}"))

        # --- 3. Запрет опасных флагов/форм ---
        for bad in (["fetch", "origin", "--all"], ["fetch", "origin", "dev:refs/heads/evil"],
                   ["fetch", "origin"], ["fetch"], ["log", "--upload-pack=evil"],
                   ["diff", "--no-index", "a", "b"], ["branch", "newbranch"],
                   ["status", "--", "C:/Windows/win.ini"], ["show", "origin/dev:../../outside.bsl"],
                   ["ls-remote", "--upload-pack=evil", "origin"]):
            r = wrap(clone, *bad)
            results.append((f"deny: {' '.join(bad[:3])}", r.returncode == 2, f"exit={r.returncode}"))

        # --- 4. Fetch: явные ветки, refs обновляются, рабочее дерево не трогается ---
        # clone чист; добавить в origin новый коммит (через seed-подключение)
        tmp_push = Path(tempfile.mkdtemp(prefix="grt_push_"))
        run_git(tmp_push, "clone", str(origin_path), "repo")
        push_repo = tmp_push / "repo"
        run_git(push_repo, "config", "user.name", "tester")
        run_git(push_repo, "config", "user.email", "t@example.com")
        new_sha = commit_file(push_repo, "src/Новый/Ext/Module.bsl", "// new\n", "#100333 remote commit")
        run_git(push_repo, "push", "origin", "dev")

        # незакоммиченное изменение пользователя в clone
        dirty = clone / "src" / "ОбщийМодуль" / "Ext" / "Module.bsl"
        dirty.write_text("// базовый модуль с правкой пользователя\n", encoding="utf-8")
        head_before = run_git(clone, "rev-parse", "HEAD").stdout.strip()
        branch_before = run_git(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

        r = wrap(clone, "fetch", "origin", "dev")
        ok_fetch = r.returncode == 0
        results.append(("fetch: явные ветки разрешены", ok_fetch, r.stderr[:100]))
        r2 = wrap(clone, "rev-parse", "--verify", "origin/dev")
        results.append(("fetch: origin/dev обновлён",
                        r2.returncode == 0 and new_sha.startswith(r2.stdout.strip()[:12]),
                        r2.stdout.strip()[:20]))
        r3 = run_git(clone, "rev-parse", "HEAD")
        results.append(("fetch: HEAD не сдвинут", r3.stdout.strip() == head_before, ""))
        r4 = run_git(clone, "rev-parse", "--abbrev-ref", "HEAD")
        results.append(("fetch: ветка не переключена", r4.stdout.strip() == branch_before, r4.stdout.strip()))
        r5 = run_git(clone, "status", "--short")
        results.append(("fetch: незакоммиченные изменения сохранены",
                        "M" in r5.stdout or "src/ОбщийМодуль" in r5.stdout, r5.stdout[:100]))
        shutil.rmtree(tmp_push, ignore_errors=True)

        # --- 5. Поиск коммитов задачи (несколько коммитов одной задачи) ---
        r = wrap(clone, "log", "origin/dev", "--oneline", "--grep=100111")
        found = [ln for ln in r.stdout.splitlines() if "#100111" in ln]
        results.append(("log --grep: найдены оба коммита задачи", len(found) == 2, f"found={len(found)}"))
        results.append(("log --grep: посторонние не попали", "#100222" not in r.stdout, ""))

        # --- 6. merge-base --is-ancestor: отсутствие SHA = exit 1, не ошибка ---
        r = wrap(clone, "merge-base", "--is-ancestor", new_sha, "origin/prod")
        results.append(("merge-base --is-ancestor: absent → exit 1", r.returncode == 1, f"exit={r.returncode}"))
        r = wrap(clone, "merge-base", "--is-ancestor", new_sha, "origin/dev")
        results.append(("merge-base --is-ancestor: present → exit 0", r.returncode == 0, f"exit={r.returncode}"))

        # --- 7. show rev:path (чтение файла по ref) ---
        r = wrap(clone, "show", "origin/dev:src/ОбщийМодуль/Ext/Module.bsl")
        results.append(("show rev:path: содержимое файла по ref",
                        r.returncode == 0 and "базовый модуль" in r.stdout, r.stderr[:100]))
    finally:
        shutil.rmtree(origin_path, ignore_errors=True)
        shutil.rmtree(_root, ignore_errors=True)

    print("=== test_git_reviewer ===")
    failed = 0
    for name, ok, info in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{info}]" if info and not ok else ""))
        if not ok:
            failed += 1
    print(f"Итог: PASS={len(results) - failed}, FAIL={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
