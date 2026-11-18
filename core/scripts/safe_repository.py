#!/usr/bin/env python3
"""Main-configuration repository pilot. No bind/update/force or implicit publication."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from repository_support import (READ_OPS, WRITE_OPS, INTENTS, profile, object_names,
                                comment, redact, fingerprint, receipt_root,
                                find_lock_receipt, required_objects, read_json, check_publication_evidence, find_diff_receipt)
from safe_apply import ROOT, find_skills_dir


def execute(root, db_id, operation, objects, task, summary='', explicit_comment='', intent='', dry_run=False, existing_change=False, task_ref=''):
    root = Path(root).resolve()
    db = profile(root, db_id, operation)
    objects = object_names(objects)
    if operation in WRITE_OPS:
        if not objects or intent != INTENTS[operation]:
            raise ValueError('repository: explicit objects and matching prepare/publish/release intent required')
        receipt_root(root, task)
    text = comment(root, db, task, summary, objects, explicit_comment, task_ref) if operation == 'commit' else ''
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
    if dry_run:
        print(json.dumps({'dry_run': True, 'db': db_id, 'operation': operation, 'objects': objects,
                          'comment': text, 'binding': 'user_confirmed' if db['repository'].get('binding_confirmed') else 'unverified',
                          'platform_started': False}, ensure_ascii=False))
        return 0
    pwsh = shutil.which('pwsh') or shutil.which('powershell')
    script = find_skills_dir(root) / '1c-repository-manage/scripts/repo-ops.ps1'
    if not pwsh or not script.is_file():
        raise ValueError('repository: PowerShell or installed repository skill unavailable')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    base = receipt_root(root, task) if task else root / 'logs/repository-inspect'
    log = base / stamp
    log.mkdir(parents=True)
    args = [pwsh, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
            '-Operation', operation, '-V8Path', db['_repository_v8path'],
            '-RepositoryPath', db['repository']['path'], '-LogDirectory', str(log),
            '-TargetEnvironment', db['_repository_environment']]
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
    if text:
        (log / 'comment.txt').write_text(text, encoding='utf-8')
        args += ['-CommentFile', str(log / 'comment.txt')]
    receipt = {'operation': operation, 'db': db_id, 'objects': objects, 'profile_hash': fingerprint(db),
               'intent': intent, 'state': 'started', 'binding_evidence': 'user-confirmed profile' if db['repository'].get('binding_confirmed') is True else 'unverified; status does not prove binding',
               'automatic_retry_allowed': False, 'verification': evidence, 'keep_locked_requested': keep_locked}
    receipt_file = log / 'receipt.json'
    receipt_file.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        run = subprocess.run(args, cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace')
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
        if receipt['state'] == 'started':
            receipt['state'] = 'unverified'
        receipt_file.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')


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
    parser.add_argument('--db', required=True)
    parser.add_argument('--operation', choices=sorted(READ_OPS | WRITE_OPS), required=True)
    parser.add_argument('--objects', default='')
    parser.add_argument('--task', default='')
    parser.add_argument('--summary', default='')
    parser.add_argument('--task-ref', default='', help='Business task reference for a private comment policy; not the receipt TASK-ID')
    parser.add_argument('--comment', default='')
    parser.add_argument('--intent', choices=['prepare', 'publish', 'release'], default='')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--existing-change', action='store_true', help='Explicit standalone publication of an existing change; never bypasses an SDD task')
    args = parser.parse_args()
    try:
        return execute(args.project_root, args.db, args.operation, args.objects, args.task,
                       args.summary, args.comment, args.intent, args.dry_run, args.existing_change, args.task_ref)
    except (ValueError, OSError) as exc:
        print(f'ERROR {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
