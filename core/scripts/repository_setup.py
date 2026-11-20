#!/usr/bin/env python3
"""Local repository profile setup. Never connects to 1C or reads credential values.

Run from the installed workspace: check -> plan -> user approval -> save.
Only .v8-project.json's selected database repository/v8path may change.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import string
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from repository_support import validate_repository_address, refresh_repository_credentials, RESERVED_CREDENTIAL_ENV

RESERVED_ENV = RESERVED_CREDENTIAL_ENV
WINDOWS = os.name == 'nt'

def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(path.read_bytes().decode('utf-8-sig'), object_pairs_hook=unique)


def load(root, db_id):
    root = Path(root).resolve()
    path = root / '.v8-project.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('regular .v8-project.json required; symlinks are unsupported')
    raw = path.read_bytes()
    config = read_json(path)
    if not isinstance(config, dict) or not isinstance(config.get('databases'), list):
        raise ValueError('invalid database registry')
    matches = [i for i, db in enumerate(config['databases'])
               if isinstance(db, dict) and db.get('id') == db_id]
    if len(matches) != 1:
        raise ValueError('db_id must select exactly one existing database')
    return root, path, raw, config, matches[0]


def policy_check(root, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('comment policy path required')
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError('comment policy must be an existing file inside workspace')
    policy = read_json(path)
    if not isinstance(policy, dict) or not isinstance(policy.get('template'), str) or not policy['template'].strip():
        raise ValueError('comment policy requires a template')
    known = {'task_id', 'task_ref', 'summary', 'objects', 'title', 'description'}
    fields = {field for _, field, _, _ in string.Formatter().parse(policy['template']) if field}
    required = policy.get('required_fields', [])
    if not fields <= known or not isinstance(required, list) or any(k not in known for k in required):
        raise ValueError('unsupported comment policy fields')
    pattern = policy.get('validation_regex')
    if pattern:
        re.compile(pattern)
    # Actual task-specific rendering/constraints are checked by safe_repository at commit.
    return digest(path.read_bytes())


def validate(root, config, db):
    repo = db.get('repository')
    if not isinstance(repo, dict):
        raise ValueError('repository section missing')
    for key in ('enabled', 'allow_write', 'binding_confirmed', 'comment_policy_required'):
        if type(repo.get(key, False)) is not bool:
            raise ValueError('repository boolean required: ' + key)
    env = db.get('environment', config.get('environment') if db.get('type') == 'file' else None)
    if env not in {'local', 'test', 'staging'} or db.get('extension') or repo.get('extension'):
        raise ValueError('setup supports only non-production main-configuration databases')
    if db.get('type') == 'server':
        if not db.get('server') or not db.get('ref'):
            raise ValueError('server database requires server/ref')
    elif db.get('type') == 'file':
        if not isinstance(db.get('path'), str) or not Path(db['path']).is_absolute():
            raise ValueError('file database requires absolute path')
    else:
        raise ValueError('unsupported database type')
    platform = db.get('v8path') or config.get('v8path')
    if not isinstance(platform, str) or not Path(platform).is_absolute() or not Path(platform).is_file():
        raise ValueError('v8path must point to an existing absolute platform file')
    address = repo.get('path')
    validate_repository_address(address)
    if repo.get('environment') not in {'dev', 'test', 'staging'}:
        raise ValueError('repository environment must be dev/test/staging')
    for owner in (db, repo):
        if any(owner.get(k) for k in ('user', 'password', 'username')):
            raise ValueError('plaintext credentials unsupported')
        for key in ('username_env', 'password_env'):
            name = owner.get(key)
            if name and (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)):
                raise ValueError('invalid credential environment variable name')
            if name and name.upper() in RESERVED_ENV:
                raise ValueError('system environment variable cannot store credentials')
    if str(repo.get('username_env', '')).casefold() == str(repo.get('password_env', '')).casefold() and repo.get('username_env'):
        raise ValueError('username and password environment names must differ')
    db_mode = db.get('password_mode', 'none')
    if db_mode not in {'none', 'env'} or (db_mode == 'env' and not db.get('password_env')) or (db_mode == 'none' and db.get('password_env')):
        raise ValueError('database credential mode is incomplete; setup does not change database credentials')
    if not repo.get('username_env') or repo.get('password_mode', 'env') != 'env' or not repo.get('password_env'):
        raise ValueError('repository username_env/password_env and env password mode required')
    if repo.get('credential_source', 'process-env') not in ('process-env', 'windows-user-env'):
        raise ValueError('unsupported repository credential_source')
    for value in (platform, address, db.get('path', ''), db.get('server', ''), db.get('ref', '')):
        if not isinstance(value, str) or any(c == '"' or ord(c) < 32 for c in value):
            raise ValueError('invalid connection field')
    if repo.get('allow_write') and not (repo.get('enabled') and repo.get('binding_confirmed')):
        raise ValueError('write requires enabled repository and explicit binding confirmation')
    policy = repo.get('comment_policy_file')
    if repo.get('comment_policy_required') and not policy:
        raise ValueError('required corporate policy is missing')
    return policy_check(root, policy) if policy else ''


def check(root, db_id):
    root, _, _, config, index = load(root, db_id)
    db = config['databases'][index]
    try:
        validate(root, config, db)
        errors = []
    except (ValueError, OSError, TypeError) as exc:
        errors = [str(exc)]
    repo = db.get('repository', {})
    repo = repo if isinstance(repo, dict) else {}
    if not errors:
        try:
            refresh_repository_credentials(repo)
        except (ValueError, OSError) as exc:
            errors.append(str(exc))
    credentials = {key: bool(os.environ.get(repo.get(key, '')))
                   for key in ('username_env', 'password_env') if isinstance(repo.get(key, ''), str)}
    missing = ['repository.' + key for key in ('path', 'environment', 'username_env', 'password_env')
               if not repo.get(key)]
    if not (db.get('v8path') or config.get('v8path')):
        missing.append('v8path')
    return {'status': 'configured' if not errors else 'needs_setup', 'db_id': db_id,
            'errors': errors, 'missing_fields': missing, 'credential_presence': credentials,
            'enabled': repo.get('enabled') is True, 'allow_write': repo.get('allow_write') is True,
            'binding_confirmed': repo.get('binding_confirmed') is True,
            'credentials_available': all(credentials.values()) and len(credentials) == 2,
            'credential_source': repo.get('credential_source', 'process-env'),
            'connection_checked': False, 'apply_permission_changed': False}


def plan(root, db_id, updates):
    root, path, raw, config, index = load(root, db_id)
    candidate = copy.deepcopy(config)
    db = candidate['databases'][index]
    old_repo = copy.deepcopy(db.get('repository', {}))
    if not isinstance(old_repo, dict):
        raise ValueError('invalid repository section; repair explicitly outside setup')
    repo = db.setdefault('repository', {})
    target_changed = any(key in updates and updates[key] != old_repo.get(key)
                         for key in ('path', 'environment'))
    for key, value in updates.items():
        if key == 'v8path':
            db[key] = value
        elif key in {'path', 'environment', 'username_env', 'password_env', 'enabled',
                     'allow_write', 'binding_confirmed', 'comment_policy_file', 'credential_source'}:
            repo[key] = value
        else:
            raise ValueError('unsupported setup field')
    if target_changed:
        # A confirmation of one repository never transfers silently to another.
        repo['binding_confirmed'] = updates.get('binding_confirmed', False)
        repo['allow_write'] = updates.get('allow_write', False)
    repo.setdefault('enabled', True)
    repo.setdefault('allow_write', False)
    repo.setdefault('binding_confirmed', False)
    repo.setdefault('password_mode', 'env')
    if updates.get('comment_policy_file'):
        repo['comment_policy_required'] = True
    policy_hash = validate(root, candidate, db)
    # Only controlled fields go to stdout: never echo arbitrary existing profile fields.
    changes = {}
    original = config['databases'][index]
    if original.get('v8path') != db.get('v8path'):
        changes['v8path'] = db.get('v8path')
    for key in repo:
        if old_repo.get(key) != repo[key]:
            changes['repository.' + key] = repo[key]
    payload = json.dumps(candidate, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
    token = digest(raw + b'\0' + payload + policy_hash.encode('ascii'))
    target = {'database': {k: db[k] for k in ('id', 'type', 'server', 'ref', 'path', 'environment') if k in db},
              'v8path': db.get('v8path') or config.get('v8path'),
              'repository': {k: repo[k] for k in ('path', 'environment', 'enabled', 'allow_write',
                             'binding_confirmed', 'username_env', 'password_env',
                             'comment_policy_file', 'comment_policy_required', 'credential_source') if k in repo}}
    report = {'status': 'planned', 'db_id': db_id, 'changes': changes, 'approval_token': token,
              'target': target,
              'connection_checked': False, 'apply_permission_changed': False,
              'next': 'Confirm this exact plan before save; credentials are entered locally, never in chat.'}
    return path, raw, payload, report


def save(root, db_id, updates, approval):
    root = Path(root).resolve()
    lock = root / '.v8-project.json.setup.lock'
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    temporary = None
    try:
        path, raw, payload, report = plan(root, db_id, updates)
        if not approval or approval != report['approval_token']:
            raise ValueError('plan changed or approval token invalid; request a new plan and approval')
        if not report['changes']:
            return dict(report, status='unchanged')
        backup = root / ('.v8-project.json.' + uuid.uuid4().hex + '.backup')
        with open(backup, 'xb') as stream:
            if os.name != 'nt':
                os.chmod(backup, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        fd, temporary = tempfile.mkstemp(prefix='.v8-project.json.', suffix='.tmp', dir=str(root))
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError('profile changed during save; original left untouched')
        os.replace(temporary, path)
        temporary = None
        return dict(report, status='saved', backup=backup.name)
    finally:
        if temporary:
            os.unlink(temporary)
        lock.unlink()


def credentials(root, db_id):
    """Open a separate native dialog. No stdin/stdout pipe carries credentials."""
    root, _, raw, config, index = load(root, db_id)
    db = config['databases'][index]
    validate(root, config, db)
    repo = db['repository']
    if not WINDOWS:
        raise ValueError('credential dialog requires a Windows desktop')
    if repo.get('credential_source') != 'windows-user-env':
        raise ValueError('approve a plan with --credential-source windows-user-env before opening the dialog')
    shell = shutil.which('powershell.exe')
    script = Path(__file__).resolve().with_name('set-repository-credentials.ps1')
    if not shell or not script.is_file():
        raise ValueError('Windows credential dialog component unavailable')
    folder = root / 'logs/repository-setup'
    if root not in folder.resolve().parents:
        raise ValueError('credential request directory must be inside workspace')
    folder.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex
    request = folder / (request_id + '.json')
    request.write_text(json.dumps({'db_id': db_id, 'profile_hash': digest(raw), 'created': time.time()}), encoding='utf-8')
    result = folder / (request_id + '.result.json')
    args = [shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', str(script),
            '-UsernameEnv', repo['username_env'], '-PasswordEnv', repo['password_env'],
            '-DatabaseLabel', db_id, '-RepositoryAddress', repo['path'], '-StatusFile', str(result)]
    try:
        subprocess.Popen(args, cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError:
        request.unlink()
        raise ValueError('could not open credential dialog on the Windows desktop') from None
    return {'status': 'dialog_requested', 'request_id': request_id, 'db_id': db_id,
            'next': 'User enters credentials in the native window. Poll credentials-status; never request secrets in chat.'}


def credentials_status(root, db_id, request_id):
    root, _, raw, _, _ = load(root, db_id)
    if not re.fullmatch(r'[0-9a-f]{32}', request_id):
        raise ValueError('invalid credential request id')
    folder = root / 'logs/repository-setup'
    if root not in folder.resolve().parents:
        raise ValueError('credential request directory must be inside workspace')
    request = read_json(folder / (request_id + '.json'))
    if request.get('db_id') != db_id or request.get('profile_hash') != digest(raw):
        raise ValueError('profile changed since dialog request; inspect the current profile before continuing')
    result = folder / (request_id + '.result.json')
    state = read_json(result).get('status') if result.is_file() else ('pending' if time.time() - request['created'] < 600 else 'unverified')
    if state not in ('pending', 'saved', 'cancelled', 'error', 'unverified'):
        raise ValueError('invalid dialog result')
    report = {'status': state, 'db_id': db_id, 'connection_checked': False}
    if state == 'saved':
        report['credentials_available'] = check(root, db_id)['credentials_available']
        report['restart_required'] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'plan', 'save', 'credentials', 'credentials-status'])
    parser.add_argument('--db', required=True)
    parser.add_argument('--v8path')
    parser.add_argument('--repository-path', dest='path')
    parser.add_argument('--repository-environment', dest='environment', choices=['dev', 'test', 'staging'])
    parser.add_argument('--username-env')
    parser.add_argument('--password-env')
    parser.add_argument('--comment-policy-file')
    parser.add_argument('--credential-source', choices=['process-env', 'windows-user-env'])
    parser.add_argument('--request-id', default='')
    for flag in ('enabled', 'allow-write', 'binding-confirmed'):
        parser.add_argument('--' + flag, choices=['yes', 'no'])
    parser.add_argument('--approve', default='')
    args = vars(parser.parse_args())
    action, db_id, approval = args.pop('action'), args.pop('db'), args.pop('approve')
    request_id = args.pop('request_id')
    updates = {k: (v == 'yes' if k in {'enabled', 'allow_write', 'binding_confirmed'} else v)
               for k, v in args.items() if v is not None}
    try:
        if action in ('credentials', 'credentials-status'):
            if updates or approval or (action == 'credentials' and request_id):
                raise ValueError('credential actions accept only --db and status --request-id')
            report = credentials(Path.cwd(), db_id) if action == 'credentials' else credentials_status(Path.cwd(), db_id, request_id)
        elif request_id:
            raise ValueError('--request-id is only supported for credentials-status')
        elif action == 'check':
            if updates or approval:
                raise ValueError('check accepts only --db')
            report = check(Path.cwd(), db_id)
        elif action == 'plan':
            if approval:
                raise ValueError('plan does not accept approval')
            report = plan(Path.cwd(), db_id, updates)[3]
        else:
            report = save(Path.cwd(), db_id, updates, approval)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Do not echo malformed file contents or credential values from parser exceptions.
        error = 'invalid JSON file' if isinstance(exc, json.JSONDecodeError) else str(exc)
        print(json.dumps({'status': 'blocked', 'error': error}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
