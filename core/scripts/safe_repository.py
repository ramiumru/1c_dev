#!/usr/bin/env python3
"""Main-configuration repository pilot. No bind/update/force or implicit publication."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from repository_support import (READ_OPS, WRITE_OPS, INTENTS, profile, object_names,
                                comment, redact, fingerprint, receipt_root,
                                find_lock_receipt, required_objects, read_json, check_publication_evidence, find_diff_receipt,
                                confirmed_lock_basis, denied_objects)
from safe_apply import ROOT, find_skills_dir


# Agent shell tools (Kilo/OpenCode) allow at most 600 s per command: 1cv8 budget + grace
# must fit, otherwise the client kills the wrapper before it records evidence.
DEFAULT_TIMEOUT_SECONDS = 480  # Designer start + CompareCfg of a large configuration
RUNNER_GRACE_SECONDS = 60      # repo-ops kills 1cv8 itself; Python waits for its evidence
BACKGROUND_TIMEOUT_SECONDS = 3600  # CompareCfg loads the whole repository version first
WAIT_LIMIT_SECONDS = 540           # one --wait call must fit the agent shell limit
MUTEX_WAIT_SECONDS = 30            # critical sections are file updates only, never a platform run
# 'running' = queued background request, 'started' = foreground request before launch,
# 'launched' = platform invocation allowed/started; everything else is terminal.
ACTIVE_STATES = {'running', 'started', 'launched'}
TERMINAL_STATES = {'succeeded', 'no_changes', 'unverified', 'rebase_required', 'not_started', 'reconciled'}
# exit codes of --wait
WAIT_DONE, WAIT_FAILED, WAIT_RUNNING, WAIT_STALE = 0, 1, 3, 4


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, data):
    """Atomic replace: readers never see a partial receipt."""
    tmp = path.with_name(path.name + f'.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(40):  # Windows: replace fails while a reader (--wait) has the file open
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


def background_timeout_seconds(db):
    value = db['repository'].get('background_timeout_seconds', BACKGROUND_TIMEOUT_SECONDS)
    if type(value) is not int or not 60 <= value <= 14400:
        raise ValueError('repository: repository.background_timeout_seconds must be an integer 60..14400')
    return value


def timeout_seconds(db):
    value = db['repository'].get('timeout_seconds', DEFAULT_TIMEOUT_SECONDS)
    if type(value) is not int or not 60 <= value <= 3600:
        raise ValueError('repository: repository.timeout_seconds must be an integer 60..3600')
    return value


def _deadline_passed(entry):
    deadline = entry.get('deadline_utc')
    if not deadline:
        return False
    due = datetime.fromisoformat(deadline)
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > due


# --- Ownership protocol ---------------------------------------------------------------------
# One Designer session per database. Every ownership transition — acquire, launch, finalize,
# reconcile, release — happens inside the database mutex: an OS advisory lock held by the kernel
# (fcntl / msvcrt), released automatically if the process dies, so it can never go stale.
# The owner lock file carries a random token; transitions verify the token under the mutex,
# so a delayed owner can never act on (or delete) a newer owner's lock.

def _db_lock_path(root, db_id):
    return Path(root) / 'logs' / 'repository-locks' / (hashlib.sha256(db_id.encode('utf-8')).hexdigest()[:16] + '.lock')


class _FileLock:
    """OS advisory lock on a file (fcntl / msvcrt); the kernel drops it when the process dies."""
    def __init__(self, path, wait_seconds, busy_message):
        self.path = Path(path)
        self.wait = wait_seconds
        self.busy = busy_message
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, 'a+b')
        deadline = time.monotonic() + self.wait
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.fh.close()
                    raise ValueError(self.busy)
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            if os.name == 'nt':
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        finally:
            self.fh.close()
        return False


def _DbMutex(lock_path):
    """Short critical sections for ownership transitions (file updates only, never a platform run)."""
    return _FileLock(Path(lock_path).with_suffix('.mutex'), MUTEX_WAIT_SECONDS,
                     'repository: database coordination mutex is busy; retry the command later')


def _runner_liveness(log, wait=0):
    """Held by the Python runner for its whole life (before launch until after finalize).
    Free means: no live runner for this receipt can launch or record anything any more."""
    return _FileLock(Path(log) / 'runner.alive', wait,
                     'repository: the safe_repository.py runner for this receipt is still alive; wait for it '
                     '(--wait) or stop that Python process first — reconcile refused')


def _read_lock(lock_path):
    try:
        return read_json(lock_path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {'receipt': '<unreadable lock>', 'token': None}


def db_lock_holder(root, db_id):
    return _read_lock(_db_lock_path(root, db_id))


def _busy_error(holder):
    return ValueError(f"repository: database is busy — run {holder.get('receipt')} has no established outcome. "
                      "Use --wait. If it is stale: ask the user to confirm that neither a 1cv8 Designer for this "
                      "base nor a safe_repository.py runner process is alive, then --reconcile <receipt> "
                      "--reason \"...\". Do not start another operation.")


def _acquire_owner(lock_path, db_id, receipt_file, operation, mode):
    """Inside the mutex: create the owner lock or refuse. Returns the owner token."""
    token = uuid.uuid4().hex
    holder = _read_lock(lock_path)
    if holder is not None:
        raise _busy_error(holder)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump({'db': db_id, 'receipt': str(receipt_file), 'token': token, 'operation': operation,
                   'mode': mode, 'created_at': _now(), 'pid': os.getpid()}, fh, ensure_ascii=False)
    return token


def _release_owner(lock_path, receipt_file, token):
    """Inside the mutex: remove the lock only if this exact owner still holds it."""
    holder = _read_lock(lock_path)
    if holder and holder.get('token') == token and holder.get('receipt') == str(receipt_file):
        os.remove(lock_path)
        return True
    return False


def _owns(lock_path, receipt_file, token):
    holder = _read_lock(lock_path)
    return bool(holder and token and holder.get('token') == token and holder.get('receipt') == str(receipt_file))


def locks_held_by(root, receipt_file):
    """Lock files that reference this receipt — independent of the receipt's own (editable) db field.
    Advisory listing only; every decision re-checks ownership under the mutex."""
    held = []
    for path in (Path(root) / 'logs' / 'repository-locks').glob('*.lock'):
        holder = _read_lock(path)
        if holder and holder.get('receipt') == str(receipt_file):
            held.append(path)
    return held


def request_digest(receipt):
    """Canonical requested parameters; the runner executes only what this digest covers."""
    fields = {k: receipt.get(k) for k in ('operation', 'db', 'objects', 'repository_version', 'timeout_seconds',
                                          'profile_hash', 'lock_token', 'lock_kind', 'refresh_of')}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def _runner_command(root, db, operation, objects, log, version, timeout, keep_locked):
    """The only place that builds the repo-ops command line (foreground and background)."""
    pwsh = shutil.which('pwsh') or shutil.which('powershell')
    script = find_skills_dir(root) / '1c-repository-manage/scripts/repo-ops.ps1'
    if not pwsh or not script.is_file():
        raise ValueError('repository: PowerShell or installed repository skill unavailable')
    args = [pwsh, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
            '-Operation', operation, '-V8Path', db['_repository_v8path'],
            '-RepositoryPath', db['repository']['path'], '-LogDirectory', str(log),
            '-TargetEnvironment', db['_repository_environment'], '-TimeoutSeconds', str(timeout)]
    if version is not None:
        args += ['-Version', str(version)]
    if db['type'] == 'file':
        args += ['-InfoBasePath', db['path']]
    else:
        args += ['-InfoBaseServer', db['server'], '-InfoBaseRef', db['ref']]
    for owner, pairs in ((db, [('username_env', '-UserNameEnv'), ('password_env', '-PasswordEnv')]),
                         (db['repository'], [('username_env', '-RepositoryUserEnv'), ('password_env', '-RepositoryPasswordEnv')])):
        for key, flag in pairs:
            if owner.get(key):
                args += [flag, owner[key]]
    if objects:
        args += ['-Objects', ','.join(objects)]
    if operation in READ_OPS:
        args += ['-ReportFile', str(log / 'report.txt')]
    if keep_locked:
        args += ['-KeepLocked']
    if (log / 'comment.txt').is_file():
        args += ['-CommentFile', str(log / 'comment.txt')]
    return args


def execute(root, db_id, operation, objects, task, summary='', explicit_comment='', intent='', dry_run=False, existing_change=False, task_ref='', title='', description='', version=None, background=False, refresh_of=None):
    root = Path(root).resolve()
    db = profile(root, db_id, operation)
    objects = object_names(objects)
    if background and operation not in READ_OPS:
        # a mutation with an unknown outcome must not outlive the caller's attention
        raise ValueError('repository: --background is supported only for status/history/diff')
    timeout = background_timeout_seconds(db) if background else timeout_seconds(db)
    if operation == 'diff':
        # 8.3.27 rejects /CompareCfg against ConfigurationRepository without -SecondVersion
        # ("Не указана версия хранилища"); the latest number comes from a status report.
        if type(version) is not int or version < 1:
            raise ValueError('repository: diff requires --version N (latest repository version from the status report)')
    elif version is not None:
        raise ValueError('repository: --version is supported only for diff')
    if operation in WRITE_OPS:
        if not objects or intent != INTENTS[operation]:
            raise ValueError('repository: explicit objects and matching prepare/publish/release intent required')
        receipt_root(root, task)
    text = comment(root, db, task, summary, objects, explicit_comment, task_ref, title, description) if operation == 'commit' else ''
    evidence = ''
    keep_locked = False
    if operation == 'commit':
        find_lock_receipt(root, task, db, objects)
        evidence = check_publication_evidence(root, task, db, objects, existing_change)
        evidence += '; diff=' + find_diff_receipt(root, task, db, objects)
        for name in objects:
            try:
                find_lock_receipt(root, task, db, [name], acquired_only=True)
            except ValueError:
                keep_locked = True  # preserve locks that predate this task
    if operation == 'unlock':
        entry = find_lock_receipt(root, task, db, objects, acquired_only=True)
        # Never release captures that existed before the task or silent/unknown locks.
        if not set(objects) <= set(entry.get('acquired_now', [])):
            raise ValueError('repository: release only locks positively acquired by this task; pre-existing locks are preserved')
    if operation in {'status', 'history'} and objects:
        raise ValueError('repository: objects are not supported for status/history')
    lock_kind = None
    if operation == 'lock':
        lock_kind = 'acquire'
        if refresh_of is not None:
            # refresh of a confirmed prepare: same task, same profile, requested objects within it;
            # re-checked under the database mutex when the operation is reserved (below)
            if confirmed_lock_basis(root, task, db, objects) != str(refresh_of):
                raise ValueError('repository: lock refresh is not bound to the latest confirmed prepare of this task')
            lock_kind = 'refresh'
    elif refresh_of is not None:
        raise ValueError('repository: refresh_of applies to lock only')
    holder = db_lock_holder(root, db_id)
    if holder:
        raise _busy_error(holder)
    if dry_run:
        print(json.dumps({'dry_run': True, 'db': db_id, 'operation': operation, 'objects': objects,
                          'version': version, 'timeout_seconds': timeout, 'background': background, 'comment': text, 'binding': 'user_confirmed' if db['repository'].get('binding_confirmed') else 'unverified',
                          'platform_started': False}, ensure_ascii=False))
        return 0
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    base = receipt_root(root, task) if task else root / 'logs/repository-inspect'
    log = base / stamp
    receipt_file = log / 'receipt.json'
    lock_path = _db_lock_path(root, db_id)
    _runner_command(root, db, operation, objects, log, version, timeout, keep_locked)  # fail before any state
    with _DbMutex(lock_path):
        if lock_kind == 'refresh':
            # an unlock/commit may have finished between the check above and this reservation
            if confirmed_lock_basis(root, task, db, objects) != str(refresh_of):
                raise ValueError('repository: confirmed prepare changed before the refresh was reserved; apply blocked')
        token = _acquire_owner(lock_path, db_id, receipt_file, operation, 'background' if background else 'foreground')
        try:
            log.mkdir(parents=True)
            if text:
                (log / 'comment.txt').write_text(text, encoding='utf-8')
            receipt = {'operation': operation, 'db': db_id, 'objects': objects, 'profile_hash': fingerprint(db),
                       'intent': intent, 'repository_version': version, 'timeout_seconds': timeout,
                       'state': 'running' if background else 'started', 'started_at': _now(),
                       'lock_token': token, 'lock_file': str(lock_path),
                       'lock_kind': lock_kind, 'refresh_of': str(refresh_of) if refresh_of else None,
                       'binding_evidence': 'user-confirmed profile' if db['repository'].get('binding_confirmed') is True else 'unverified; status does not prove binding',
                       'automatic_retry_allowed': False, 'verification': evidence, 'keep_locked_requested': keep_locked}
            if background:
                receipt.update(mode='background', deadline_utc=datetime.fromtimestamp(
                    time.time() + timeout + RUNNER_GRACE_SECONDS, timezone.utc).isoformat())
            receipt['request_sha256'] = request_digest(receipt)
            _write_json(receipt_file, receipt)
        except BaseException:
            _release_owner(lock_path, receipt_file, token)  # nothing was launched
            raise
    if background:
        return _start_background(root, receipt_file)
    args = _runner_command(root, db, operation, objects, log, version, timeout, keep_locked)
    with _runner_liveness(log):
        if not _launch(lock_path, receipt_file, receipt['request_sha256'], 'started'):
            print('repository: launch refused — the run was reconciled or ownership changed', file=sys.stderr)
            return 1
        return _run_recorded(root, db, operation, objects, args, log, receipt_file, timeout, lock_path, token)


def _launch(lock_path, receipt_file, expected_digest, pending_state):
    """Fence: the single point where a platform invocation becomes allowed.
    Under the mutex the receipt is re-read from disk; launch only if this owner still holds the
    lock, the request is unchanged, the run is still pending and its deadline has not passed."""
    with _DbMutex(lock_path):
        disk = read_json(receipt_file)
        if disk.get('state') != pending_state:
            return False  # reconciled/cancelled/finished — never revive
        token = disk.get('lock_token')
        reason = None
        if not _owns(lock_path, receipt_file, token):
            reason = 'database lock is not held by this run'
        elif disk.get('request_sha256') != expected_digest or request_digest(disk) != expected_digest:
            reason = 'requested parameters were modified after submission'
        elif _deadline_passed(disk):
            reason = 'deadline passed before the runner could launch'
        if reason:
            _not_started_locked(lock_path, receipt_file, disk, reason)
            return False
        disk.update(state='launched', launched_at=_now(), runner_pid=os.getpid())
        _write_json(receipt_file, disk)
        return True


def _not_started_locked(lock_path, receipt_file, disk, reason, db=None):
    """Inside the mutex: known non-launch -> terminal not_started, release own lock."""
    disk.update(state='not_started', platform_started=False, outcome_known=True, finished_at=_now(),
                diagnostic=redact(str(reason), db) if db else str(reason))
    _write_json(receipt_file, disk)
    _release_owner(lock_path, receipt_file, disk.get('lock_token'))


def _finalize(lock_path, receipt_file, token, updates, outcome_known):
    """Fence: the single point where a launched run records its outcome.
    A run that was reconciled meanwhile keeps its state and audit trail; the late result is
    attached for diagnosis only and never becomes evidence."""
    with _DbMutex(lock_path):
        disk = read_json(receipt_file)
        if disk.get('state') == 'launched' and disk.get('lock_token') == token and _owns(lock_path, receipt_file, token):
            disk.update(updates)
            disk.update(finished_at=_now(), outcome_known=outcome_known)
            if disk.get('state') not in TERMINAL_STATES:
                disk['state'] = 'unverified'
            _write_json(receipt_file, disk)
            if outcome_known:
                _release_owner(lock_path, receipt_file, token)
            return disk['state']
        disk['late_runner_result'] = {'at': _now(), 'state': updates.get('state'), 'runner_exit': updates.get('runner_exit'),
                                      'outcome_known': outcome_known}
        _write_json(receipt_file, disk)
        return disk.get('state')


def _run_recorded(root, db, operation, objects, args, log, receipt_file, timeout, lock_path, token):
    """Run repo-ops once (launch already fenced) and record the outcome through _finalize.
    The database is released only when repo-ops wrote result.json (the platform process ended)."""
    updates = {'state': 'unverified'}
    outcome_known = False
    code = 1
    try:
        try:
            run = subprocess.run(args, cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                 timeout=timeout + RUNNER_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            updates['diagnostic'] = (f'runner did not finish in {timeout + RUNNER_GRACE_SECONDS}s; a 1cv8.exe Designer may '
                                     'still run. Database stays locked until --reconcile after checking processes and locks.')
            print('repository: runner timeout; inspect receipt and running 1cv8.exe before any retry', file=sys.stderr)
            return 1
        output = redact(run.stdout + '\n' + run.stderr, db)
        (log / 'runner.log').write_text(output, encoding='utf-8')
        has_result = (log / 'result.json').is_file()
        result = read_json(log / 'result.json') if has_result else {}
        outcome_known = has_result  # repo-ops reached its finally: the Designer process has ended
        state = 'succeeded' if run.returncode == 0 and result.get('effective_exit') == 0 and result.get('platform_started') else 'unverified'
        if result.get('timed_out'):
            state = 'unverified'
        if operation == 'lock' and state == 'succeeded':
            state, lock_note = _lock_outcome(receipt_file, objects, result)
            if lock_note:
                updates['lock_assessment'] = lock_note
        if result.get('received'):
            state = 'rebase_required'  # no overwrite of source truth, no automatic apply
        if operation == 'commit' and state == 'succeeded':
            acknowledged = set(result.get('committed', [])) | set(result.get('unchanged', []))
            if not set(objects) <= acknowledged:
                state = 'unverified'  # partial/silent success is not full publication evidence
            elif not result.get('committed'):
                state = 'no_changes'
        # a capture line contradicted by a denial for the same object is not a capture
        denied = denied_objects({'result': result})
        captured = [n for n in result.get('locked', []) if str(n).strip().lower().replace('ё', 'е') not in denied]
        updates.update(state=state, runner_exit=run.returncode, acquired_now=captured, result=result)
        if not has_result:
            updates['diagnostic'] = 'runner exited without result.json; platform outcome unknown — database stays locked'
        print(output)
        code = 0 if state in {'succeeded', 'no_changes'} else 1
        return code
    except (OSError, ValueError) as exc:
        updates.update(state='unverified', diagnostic=redact(str(exc), db))
        print('repository: execution failed; inspect receipt before any retry', file=sys.stderr)
        return 1
    finally:
        recorded = _finalize(lock_path, receipt_file, token, updates, outcome_known)
        print(f'repository: state={recorded}; receipt={receipt_file}')
        if recorded != updates.get('state'):
            print('repository: run was reconciled meanwhile; its late result is kept for diagnosis only', file=sys.stderr)


def _lock_outcome(receipt_file, objects, result):
    """Every requested object must be confirmed either by the platform's own per-object line
    («Объект захвачен для редактирования: …») or — only when refreshing a confirmed prepare of the
    same task/profile — by the platform's completion line with all launch signals clean.
    The «Already locked by you» text is printed by repo-ops itself and is never a signal."""
    key = lambda n: str(n).strip().lower().replace('ё', 'е')  # same as repo-ops Get-ObjectKey (+ё)
    locked = {key(n) for n in result.get('locked', [])}
    # an explicit negative per-object answer wins over any general completion line
    negative = denied_objects({'result': result})  # not locked / unlocked / locked by another user
    denied = [n for n in objects if key(n) in negative]
    if denied:
        return 'unverified', f'platform reported {denied} as not locked/unlocked'
    missing = [n for n in objects if key(n) not in locked]
    if not missing:
        return 'succeeded', 'acquired by platform per-object lines'
    disk = read_json(receipt_file)
    if disk.get('lock_kind') != 'refresh' or not disk.get('refresh_of'):
        return 'unverified', f'no platform confirmation for {missing}; no confirmed prepare to refresh'
    checks = {
        'platform_exit=0': result.get('platform_exit') == 0,
        'effective_exit=0': result.get('effective_exit') == 0,
        'dump_result=0': result.get('dump_result') == 0,
        'completion line': result.get('lock_done') is True,
        'no unrecognized lines': result.get('unrecognized_count') == 0,
        'no other-user locks': not result.get('locked_by_other'),
        'no received objects': not result.get('received'),
        'not timed out': not result.get('timed_out'),
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        return 'unverified', f'refresh not confirmed for {missing}: failed {failed}'
    return 'succeeded', f'refresh_confirmed of {disk["refresh_of"]} for {missing}'


def _start_background(root, receipt_file):
    """Detach the recorded run from the agent shell (Kilo kills commands after <=600 s)."""
    cmd = [sys.executable, str(Path(__file__).resolve()), '--project-root', str(root), '--run-job', str(receipt_file.parent)]
    kwargs = dict(cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        if os.name == 'nt':
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            try:
                subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kwargs)  # + CREATE_BREAKAWAY_FROM_JOB
            except OSError:
                subprocess.Popen(cmd, creationflags=flags, **kwargs)  # job forbids breakaway
        else:
            subprocess.Popen(cmd, start_new_session=True, **kwargs)
    except OSError as exc:
        _mark_not_started(receipt_file, f'background runner could not be started: {exc}')
        print(f'repository: state=not_started; receipt={receipt_file}', file=sys.stderr)
        return 1
    timeout = read_json(receipt_file).get('timeout_seconds')
    print(f'repository: state=running; receipt={receipt_file}')
    print(f'repository: poll with --wait "{receipt_file}" (each call waits up to {WAIT_LIMIT_SECONDS}s); '
          f'1cv8 limit {timeout}s')
    return 0


def _mark_not_started(receipt_file, reason, db=None):
    """Known non-launch of a still-pending run (state running/started), fenced by the mutex."""
    try:
        lock_path = Path(read_json(receipt_file).get('lock_file') or '')
    except (OSError, ValueError):
        return
    if not lock_path.name:
        return
    with _DbMutex(lock_path):
        disk = read_json(receipt_file)
        if disk.get('state') in {'running', 'started'}:
            _not_started_locked(lock_path, receipt_file, disk, reason, db)


def _receipt_dirs(root):
    return (root / 'pilot-control', root / 'logs' / 'repository-inspect')


def run_job(root, log):
    root = Path(root).resolve()
    log = Path(log).resolve()
    if not any(log.is_relative_to(d.resolve()) for d in _receipt_dirs(root)):
        raise ValueError('repository: background job outside receipt directories')
    receipt_file = log / 'receipt.json'
    # OS-atomic one-shot claim: exactly one runner per receipt, ever.
    try:
        fd = os.open(str(log / 'runner.claim'), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError('repository: background job already claimed by another runner') from None
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps({'pid': os.getpid(), 'claimed_at': _now()}))
    with _runner_liveness(log):
        return _run_job_alive(root, log, receipt_file)


def _run_job_alive(root, log, receipt_file):
    receipt = read_json(receipt_file)
    db = None
    lock_path = None
    try:
        if receipt.get('mode') != 'background' or receipt.get('state') != 'running':
            raise ValueError('background job is not pending')
        if receipt.get('request_sha256') != request_digest(receipt):
            raise ValueError('requested parameters were modified after submission')
        lock_path = _db_lock_path(root, str(receipt.get('db', '')))
        if Path(receipt.get('lock_file', '')) != lock_path:
            raise ValueError('lock file does not belong to the requested database')
        if not _owns(lock_path, receipt_file, receipt.get('lock_token')):
            raise ValueError('database lock is not held by this run')
        operation = receipt['operation']
        if operation not in READ_OPS:
            raise ValueError('background job supports read operations only')
        db = profile(root, receipt['db'], operation)  # re-validate: profile may have changed
        if fingerprint(db) != receipt.get('profile_hash'):
            raise ValueError('profile changed since the background run was requested')
        objects = object_names(receipt.get('objects', []))
        if objects != receipt.get('objects'):
            raise ValueError('objects are not canonical')
        timeout = background_timeout_seconds(db)
        if timeout != receipt.get('timeout_seconds'):
            raise ValueError('timeout differs from the profile')
        version = receipt.get('repository_version')
        if operation == 'diff' and (type(version) is not int or version < 1):
            raise ValueError('diff without a valid repository version')
        if operation != 'diff' and version is not None:
            raise ValueError('version is supported only for diff')
        args = _runner_command(root, db, operation, objects, log, version, timeout, False)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _mark_not_started(receipt_file, f'background runner refused to launch: {exc}', db)
        raise ValueError(f'repository: background runner refused to launch: {exc}') from None
    if not _launch(lock_path, receipt_file, receipt['request_sha256'], 'running'):
        raise ValueError('repository: background runner refused to launch: run was reconciled, changed or expired')
    return _run_recorded(root, db, operation, objects, args, log, receipt_file, timeout, lock_path, receipt['lock_token'])


def wait_receipt(root, receipt_path, max_wait=WAIT_LIMIT_SECONDS, poll=5):
    root = Path(root).resolve()
    path = Path(receipt_path).resolve()
    if path.name != 'receipt.json' or not any(path.is_relative_to(d.resolve()) for d in _receipt_dirs(root)):
        raise ValueError('repository: --wait accepts only a receipt.json under pilot-control/ or logs/repository-inspect/')
    max_wait = max(0, min(int(max_wait), WAIT_LIMIT_SECONDS))
    end = time.monotonic() + max_wait
    unreadable = None
    while True:
        try:
            entry = read_json(path)
            unreadable = None
        except (OSError, ValueError) as exc:  # includes JSONDecodeError
            entry, unreadable = None, exc
        if entry is not None:
            state = entry.get('state')
            if state in TERMINAL_STATES:
                print(json.dumps({k: entry.get(k) for k in ('operation', 'state', 'repository_version', 'runner_exit',
                                                             'started_at', 'finished_at', 'outcome_known', 'diagnostic')}, ensure_ascii=False))
                report = path.parent / 'report.txt'
                if report.is_file() and state == 'succeeded':
                    print(f'repository: report={report}')
                return WAIT_DONE if state in {'succeeded', 'no_changes'} else WAIT_FAILED
            if _deadline_passed(entry):
                print('repository: state=stale — deadline passed without a result. The database stays locked: '
                      'ask the user to confirm that neither 1cv8 nor the safe_repository.py runner is alive, '
                      'then --reconcile.', file=sys.stderr)
                return WAIT_STALE
        if time.monotonic() >= end:
            if unreadable is not None:
                print(f'repository: receipt unreadable within the wait budget ({unreadable}); nothing is confirmed', file=sys.stderr)
                return WAIT_FAILED
            print(f"repository: state={entry.get('state')}; still working, call --wait again (deadline {entry.get('deadline_utc')})")
            return WAIT_RUNNING
        time.sleep(min(poll, max(0.0, end - time.monotonic())) or 0.01)


def reconcile(root, receipt_path, reason):
    """Explicit, audited release of a database whose run has no established outcome.
    Only after the user confirmed that neither a 1cv8 Designer for this base nor a Python runner
    is alive. Under the mutex: a pending run can no longer launch, a launched run can no longer
    revive or overwrite this record."""
    root = Path(root).resolve()
    path = Path(receipt_path).resolve()
    if path.name != 'receipt.json' or not any(path.is_relative_to(d.resolve()) for d in _receipt_dirs(root)):
        raise ValueError('repository: --reconcile accepts only a receipt.json under pilot-control/ or logs/repository-inspect/')
    if not reason or len(reason.strip()) < 10:
        raise ValueError('repository: --reason must state what the user confirmed (no running 1cv8 Designer / runner)')
    candidates = locks_held_by(root, path)
    if not candidates:
        raise ValueError('repository: this receipt does not hold a database lock; nothing to reconcile')
    for lock_path in candidates:
        with _DbMutex(lock_path):
            entry = read_json(path)
            holder = _read_lock(lock_path) or {}
            if not (holder.get('receipt') == str(path) and holder.get('token') and holder.get('token') == entry.get('lock_token')):
                raise ValueError('repository: lock ownership does not match this receipt; nothing released')
            with _runner_liveness(path.parent):  # raises while the Python runner is alive
                pass
            previous = entry.get('state')
            entry['state'] = 'reconciled'
            entry['outcome_known'] = False  # reconciliation releases the barrier, it does not prove the outcome
            entry.setdefault('reconciliations', []).append({'at': _now(), 'reason': reason.strip(), 'previous_state': previous})
            _write_json(path, entry)
            _release_owner(lock_path, path, entry['lock_token'])
    print(f'repository: reconciled; database lock released; receipt={path} (outcome unverified, not evidence)')
    return 0


def before_apply(root, db_id, task, files, config_src, dry_run):
    config = read_json(Path(root) / '.v8-project.json')
    matches = [d for d in config.get('databases', []) if d.get('id') == db_id]
    if len(matches) != 1:
        raise ValueError('repository: ambiguous database id')
    if not matches[0].get('repository', {}).get('enabled'):
        return
    db = profile(root, db_id, 'lock')
    objects = required_objects(config_src, files)
    if not objects:
        raise ValueError('repository: no existing mutable object scope could be derived for apply')
    prior = confirmed_lock_basis(root, task, db, objects)
    if dry_run:
        print('repository: prepared scope checked; live locks are NOT refreshed in dry-run')
    elif execute(root, db_id, 'lock', objects, task, intent='prepare', refresh_of=prior) != 0:
        raise ValueError('repository: lock refresh failed; apply blocked')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=ROOT)
    parser.add_argument('--db', default='')
    parser.add_argument('--operation', choices=sorted(READ_OPS | WRITE_OPS))
    parser.add_argument('--background', action='store_true', help='status/history/diff: detach from the agent shell; poll with --wait')
    parser.add_argument('--wait', default='', help='receipt.json of a background run: wait up to --max-wait s and report state')
    parser.add_argument('--max-wait', type=int, default=WAIT_LIMIT_SECONDS)
    parser.add_argument('--run-job', default='', help=argparse.SUPPRESS)
    parser.add_argument('--reconcile', default='', help='receipt.json holding the database lock: audited release after the user confirmed no running Designer')
    parser.add_argument('--reason', default='', help='--reconcile: what the user confirmed')
    parser.add_argument('--objects', default='')
    parser.add_argument('--version', type=int, default=None, help='diff only: repository version to compare with (latest from status)')
    parser.add_argument('--task', default='')
    parser.add_argument('--summary', default='')
    parser.add_argument('--title', default='', help='Title for a configured private comment template')
    parser.add_argument('--description', default='', help='Description for a configured private comment template')
    parser.add_argument('--task-ref', default='', help='Business task reference for a private comment policy; not the receipt TASK-ID')
    parser.add_argument('--comment', default='')
    parser.add_argument('--intent', choices=['prepare', 'publish', 'release'], default='')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--existing-change', action='store_true', help='Explicit standalone publication of an existing change; never bypasses an SDD task')
    args = parser.parse_args()
    try:
        if args.run_job:
            return run_job(args.project_root, args.run_job)
        if args.wait:
            return wait_receipt(args.project_root, args.wait, args.max_wait)
        if args.reconcile:
            return reconcile(args.project_root, args.reconcile, args.reason)
        if not args.db or not args.operation:
            raise ValueError('repository: --db and --operation are required')
        return execute(args.project_root, args.db, args.operation, args.objects, args.task,
                       args.summary, args.comment, args.intent, args.dry_run, args.existing_change, args.task_ref, args.title, args.description, args.version, args.background)
    except (ValueError, OSError) as exc:
        print(f'ERROR {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
