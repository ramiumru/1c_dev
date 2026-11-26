#!/usr/bin/env python3
"""Main-configuration repository pilot. No bind/update/force or implicit publication."""
from __future__ import annotations
import argparse
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
                                find_lock_receipt, required_objects, read_json, check_publication_evidence, find_diff_receipt)
from safe_apply import ROOT, find_skills_dir


# Agent shell tools (Kilo/OpenCode) allow at most 600 s per command: 1cv8 budget + grace
# must fit, otherwise the client kills the wrapper before it records evidence.
DEFAULT_TIMEOUT_SECONDS = 480  # Designer start + CompareCfg of a large configuration
RUNNER_GRACE_SECONDS = 60      # repo-ops kills 1cv8 itself; Python waits for its evidence


BACKGROUND_TIMEOUT_SECONDS = 3600  # CompareCfg loads the whole repository version first
WAIT_LIMIT_SECONDS = 540           # one --wait call must fit the agent shell limit


def _write_json(path, data):
    """Atomic replace: a background runner and --wait read the same receipt concurrently."""
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


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


def active_background(root, db_id):
    """A running background Designer session for this database, if any (one session per IB)."""
    root = Path(root)
    for path in list(root.glob('pilot-control/*/repository/*/receipt.json')) + list(root.glob('logs/repository-inspect/*/receipt.json')):
        try:
            entry = read_json(path)
        except (OSError, ValueError):
            continue
        if entry.get('mode') == 'background' and entry.get('state') == 'running' and entry.get('db') == db_id \
                and not _deadline_passed(entry):
            return path
    return None


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


def execute(root, db_id, operation, objects, task, summary='', explicit_comment='', intent='', dry_run=False, existing_change=False, task_ref='', title='', description='', version=None, background=False):
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
    busy = active_background(root, db_id)
    if busy:
        raise ValueError(f'repository: a background run is still active for this database: {busy}; use --wait')
    if dry_run:
        print(json.dumps({'dry_run': True, 'db': db_id, 'operation': operation, 'objects': objects,
                          'version': version, 'timeout_seconds': timeout, 'background': background, 'comment': text, 'binding': 'user_confirmed' if db['repository'].get('binding_confirmed') else 'unverified',
                          'platform_started': False}, ensure_ascii=False))
        return 0
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    base = receipt_root(root, task) if task else root / 'logs/repository-inspect'
    log = base / stamp
    _runner_command(root, db, operation, objects, log, version, timeout, keep_locked)  # fail before mkdir
    log.mkdir(parents=True)
    if text:
        (log / 'comment.txt').write_text(text, encoding='utf-8')
    args = _runner_command(root, db, operation, objects, log, version, timeout, keep_locked)
    receipt = {'operation': operation, 'db': db_id, 'objects': objects, 'profile_hash': fingerprint(db),
               'intent': intent, 'repository_version': version, 'timeout_seconds': timeout, 'state': 'started', 'binding_evidence': 'user-confirmed profile' if db['repository'].get('binding_confirmed') is True else 'unverified; status does not prove binding',
               'automatic_retry_allowed': False, 'verification': evidence, 'keep_locked_requested': keep_locked}
    receipt_file = log / 'receipt.json'
    _write_json(receipt_file, receipt)
    if background:
        return _start_background(root, db_id, operation, objects, version, log, receipt, receipt_file, timeout)
    return _run_recorded(root, db, operation, objects, args, log, receipt, receipt_file, timeout)


def _run_recorded(root, db, operation, objects, args, log, receipt, receipt_file, timeout):
    try:
        try:
            run = subprocess.run(args, cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                 timeout=timeout + RUNNER_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            receipt.update(state='unverified', diagnostic=(
                f'runner did not finish in {timeout + RUNNER_GRACE_SECONDS}s; a 1cv8.exe Designer may still run. '
                'Check processes and repository locks before any retry.'))
            print('repository: runner timeout; inspect receipt and running 1cv8.exe before any retry', file=sys.stderr)
            return 1
        output = redact(run.stdout + '\n' + run.stderr, db)
        (log / 'runner.log').write_text(output, encoding='utf-8')
        result = read_json(log / 'result.json') if (log / 'result.json').is_file() else {}
        code = run.returncode
        state = 'succeeded' if code == 0 and result.get('effective_exit') == 0 and result.get('platform_started') else 'unverified'
        if result.get('timed_out'):
            state = 'unverified'
        if operation == 'lock' and state == 'succeeded' and not result.get('operation_block') and not result.get('locked'):
            platform_log = log / 'platform.log'
            if platform_log.is_file() and platform_log.read_text(encoding='utf-8-sig').strip():
                state = 'unverified'  # unrecognized nonempty log is not an already-owned acknowledgement
        if result.get('received'):
            state = 'rebase_required'  # no overwrite of source truth, no automatic apply
        if operation == 'commit' and state == 'succeeded':
            acknowledged = set(result.get('committed', [])) | set(result.get('unchanged', []))
            if not set(objects) <= acknowledged:
                state = 'unverified'  # partial/silent success is not full publication evidence
            elif not result.get('committed'):
                state = 'no_changes'
        receipt.update(state=state, runner_exit=code, acquired_now=result.get('locked', []), result=result)
        print(output)
        print(f'repository: state={state}; receipt={receipt_file}')
        return 0 if state in {'succeeded', 'no_changes'} else 1
    except (OSError, ValueError) as exc:
        receipt.update(state='unverified', diagnostic=redact(str(exc), db))
        print('repository: execution failed; inspect receipt before any retry', file=sys.stderr)
        return 1
    finally:
        if receipt['state'] in {'started', 'running'}:
            receipt['state'] = 'unverified'
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        _write_json(receipt_file, receipt)


def _start_background(root, db_id, operation, objects, version, log, receipt, receipt_file, timeout):
    """Detach the same recorded run from the agent shell (Kilo kills commands after <=600 s)."""
    now = datetime.now(timezone.utc)
    receipt.update(state='running', mode='background', started_at=now.isoformat(),
                   deadline_utc=datetime.fromtimestamp(now.timestamp() + timeout + RUNNER_GRACE_SECONDS, timezone.utc).isoformat())
    # parameters only: the child rebuilds and re-validates the command itself
    job = {'db': db_id, 'operation': operation, 'objects': objects, 'version': version, 'timeout': timeout}
    _write_json(log / 'job.json', job)
    _write_json(receipt_file, receipt)
    cmd = [sys.executable, str(Path(__file__).resolve()), '--project-root', str(root), '--run-job', str(log)]
    kwargs = dict(cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == 'nt':
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kwargs)  # + CREATE_BREAKAWAY_FROM_JOB
        except OSError:
            subprocess.Popen(cmd, creationflags=flags, **kwargs)  # job forbids breakaway
    else:
        subprocess.Popen(cmd, start_new_session=True, **kwargs)
    print(f'repository: state=running; receipt={receipt_file}')
    print(f'repository: poll with --wait "{receipt_file}" (each call waits up to {WAIT_LIMIT_SECONDS}s); '
          f'1cv8 limit {timeout}s')
    return 0


def run_job(root, log):
    root = Path(root).resolve()
    log = Path(log).resolve()
    if not any(log.parent.is_relative_to(a) for a in (root / 'pilot-control', root / 'logs' / 'repository-inspect')):
        raise ValueError('repository: background job outside receipt directories')
    receipt_file = log / 'receipt.json'
    receipt = read_json(receipt_file)
    if receipt.get('mode') != 'background' or receipt.get('state') != 'running' or receipt.get('runner_claimed'):
        raise ValueError('repository: background job is not pending (already run or not a background receipt)')
    receipt['runner_claimed'] = True  # one-shot
    _write_json(receipt_file, receipt)
    job = read_json(log / 'job.json')
    operation = job['operation']
    if operation not in READ_OPS:
        raise ValueError('repository: background job supports read operations only')
    db = profile(root, job['db'], operation)  # re-validate: profile may have changed
    if fingerprint(db) != receipt.get('profile_hash') or receipt.get('db') != job['db']:
        raise ValueError('repository: profile changed since the background run was requested')
    objects = object_names(job['objects'])
    timeout = background_timeout_seconds(db)
    version = job.get('version')
    if operation == 'diff' and (type(version) is not int or version < 1):
        raise ValueError('repository: background diff without a valid version')
    args = _runner_command(root, db, operation, objects, log, version, timeout, False)
    return _run_recorded(root, db, operation, objects, args, log, receipt, receipt_file, timeout)


def wait_receipt(root, receipt_path, max_wait=WAIT_LIMIT_SECONDS, poll=5):
    root = Path(root).resolve()
    path = Path(receipt_path).resolve()
    allowed = [root / 'pilot-control', root / 'logs' / 'repository-inspect']
    if path.name != 'receipt.json' or not any(path.is_relative_to(a.resolve()) for a in allowed):
        raise ValueError('repository: --wait accepts only a receipt.json under pilot-control/ or logs/repository-inspect/')
    max_wait = max(0, min(int(max_wait), WAIT_LIMIT_SECONDS))
    end = time.monotonic() + max_wait
    while True:
        try:
            entry = read_json(path)
        except json.JSONDecodeError:
            time.sleep(1)  # replaced between open and read on some filesystems
            continue
        state = entry.get('state')
        if state not in {'running', 'started'}:
            print(json.dumps({k: entry.get(k) for k in ('operation', 'state', 'repository_version', 'runner_exit',
                                                         'started_at', 'finished_at', 'diagnostic')}, ensure_ascii=False))
            report = path.parent / 'report.txt'
            if report.is_file():
                print(f'repository: report={report}')
            return 0 if state in {'succeeded', 'no_changes'} else 1
        if _deadline_passed(entry):
            print('repository: state=stale — background runner passed its deadline without a result; '
                  'check 1cv8.exe and the runner before any retry', file=sys.stderr)
            return 1
        if time.monotonic() >= end:
            print(f"repository: state=running; still working, call --wait again (deadline {entry.get('deadline_utc')})")
            return 3
        time.sleep(poll)


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
    find_lock_receipt(root, task, db, objects)
    if dry_run:
        print('repository: prepared scope checked; live locks are NOT refreshed in dry-run')
    elif execute(root, db_id, 'lock', objects, task, intent='prepare') != 0:
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
