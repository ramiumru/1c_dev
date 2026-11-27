#!/usr/bin/env python3
"""Configuration repository pilot: profile, exact scope, comments and receipts."""
from __future__ import annotations
import hashlib
import json
import os
import re
import string
from pathlib import Path
from urllib.parse import urlsplit

READ_OPS = {'status', 'history', 'diff'}
WRITE_OPS = {'lock', 'commit', 'unlock'}
INTENTS = {'lock': 'prepare', 'commit': 'publish', 'unlock': 'release'}
RESERVED_CREDENTIAL_ENV = {'PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP',
                           'PSMODULEPATH', 'PYTHONPATH', 'PYTHONHOME', 'CODEX_HOME', 'PATHEXT', 'COMSPEC'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def validate_repository_address(address):
    """Validate supported transports without rewriting the platform's address."""
    if not isinstance(address, str) or not address.strip():
        raise ValueError('repository: address is required')
    if any(c == '"' or ord(c) < 32 for c in address):
        raise ValueError('repository: invalid address (quote/control character)')
    if '://' in address:
        try:
            uri = urlsplit(address)
            port = uri.port  # Access validates malformed/out-of-range ports.
        except ValueError:
            raise ValueError('repository: invalid repository URI') from None
        if uri.scheme.lower() not in {'tcp', 'http', 'https'} or not uri.hostname:
            raise ValueError('repository: URI requires tcp/http/https and a host')
        if uri.username is not None or uri.password is not None:
            raise ValueError('repository: credentials must not be embedded in repository URI')
        if any(c.isspace() for c in address) or '\\' in address or port == 0:
            raise ValueError('repository: invalid repository URI')
    elif not Path(address).is_absolute():
        raise ValueError('repository: address must be tcp://, http://, https:// or an absolute path')


def windows_user_environment(name):
    """Read one literal User env value, never enumerate/export the user's environment."""
    if os.name != 'nt':
        raise ValueError('repository: Windows User credentials require Windows')
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            value, kind = winreg.QueryValueEx(key, name)
            if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
                raise ValueError('repository: unsupported User environment value')
            return value  # Do not expand percent signs in passwords.
    except FileNotFoundError:
        return ''


def refresh_repository_credentials(repo):
    source = repo.get('credential_source', 'process-env')
    if source not in ('process-env', 'windows-user-env'):
        raise ValueError('repository: unsupported credential_source')
    if source == 'windows-user-env':
        names = [repo.get(k) for k in ('username_env', 'password_env')]
        if any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', n) for n in names):
            raise ValueError('repository: valid credential env names required')
        if len(set(n.casefold() for n in names)) != 2:
            raise ValueError('repository: credential env names must differ')
        if any(n.upper() in RESERVED_CREDENTIAL_ENV for n in names):
            raise ValueError('repository: system variables cannot store credentials')
        values = [windows_user_environment(n) for n in names]
        for name, value in zip(names, values):
            # User selection is authoritative; never silently reuse stale Process values.
            if value:
                os.environ[name] = value
            else:
                os.environ.pop(name, None)


def profile(root, db_id, operation):
    config = read_json(Path(root) / '.v8-project.json')
    if not isinstance(config, dict) or not isinstance(config.get('databases'), list):
        raise ValueError('repository: invalid database registry')
    dbs = [d for d in config['databases'] if isinstance(d, dict) and d.get('id') == db_id]
    if len(dbs) != 1:
        raise ValueError('repository: database id must identify exactly one registered database')
    db = dict(dbs[0])
    repo = db.get('repository', {})
    if not isinstance(repo, dict) or repo.get('enabled') is not True:
        raise ValueError('repository: not enabled for the selected database')
    if operation not in READ_OPS | WRITE_OPS:
        raise ValueError('repository: operation outside main-configuration pilot')
    if repo.get('extension') or db.get('extension'):
        raise ValueError('repository: extensions are outside this pilot')
    env = db.get('environment', config.get('environment') if db.get('type') == 'file' else None)
    if not isinstance(env, str) or env not in {'local', 'test', 'staging', 'production'}:
        raise ValueError('repository: explicit database environment is required')
    validate_repository_address(repo.get('path'))
    if operation in WRITE_OPS:
        if env == 'production' or (not isinstance(repo.get('environment'), str) or repo.get('environment') not in {'dev', 'test', 'staging'}):
            raise ValueError('repository: mutations require non-production database AND repository')
        if repo.get('allow_write') is not True:
            raise ValueError('repository: repository.allow_write must be true; apply permission is checked separately by applier_guard')
        if repo.get('binding_confirmed') is not True:
            raise ValueError('repository: binding is unverified; confirm the selected IB/repository in Designer first')
    if any(db.get(k) for k in ('user', 'password')) or any(repo.get(k) for k in ('user', 'password', 'username')):
        raise ValueError('repository: plaintext credentials are unsupported; use env variable names')
    refresh_repository_credentials(repo)
    for owner, default in ((db, 'none'), (repo, 'env')):
        mode = owner.get('password_mode', default)
        if mode not in ('none', 'env'):
            raise ValueError('repository: invalid password_mode')
        if mode == 'none' and owner.get('password_env'):
            raise ValueError('repository: password_mode none cannot also select password_env')
        if mode == 'env' and not owner.get('password_env'):
            raise ValueError('repository: password_env required for env mode')
        for key in ('username_env', 'password_env'):
            name = owner.get(key)
            if name and (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)):
                raise ValueError('repository: invalid credential env variable name')
            if name and not os.environ.get(name):
                raise ValueError(f'repository: required Process environment variable is absent: {name}')
    if not repo.get('username_env'):
        raise ValueError('repository: repository.username_env is required')
    v8path = db.get('v8path') or config.get('v8path')
    if not isinstance(v8path, str) or not v8path.strip():
        raise ValueError('repository: explicit v8path is required')
    for value in (v8path, repo['path'], db.get('path', ''), db.get('server', ''), db.get('ref', '')):
        if not isinstance(value, str) or any(c == '"' or ord(c) < 32 for c in value):
            raise ValueError('repository: invalid connection field (quote/control character)')
    if db.get('type') == 'file':
        if not db.get('path') or not Path(db['path']).is_absolute():
            raise ValueError('repository: file database requires an absolute path')
    elif db.get('type') == 'server':
        if not db.get('server') or not db.get('ref'):
            raise ValueError('repository: server database requires server/ref')
    else:
        raise ValueError('repository: unsupported database type')
    db['_repository_v8path'] = v8path
    db['_repository_environment'] = env
    return db


def object_names(value):
    from build_summaries import EN_TO_RU_TYPE
    aliases = dict(EN_TO_RU_TYPE, Form='Форма', Template='Макет', Command='Команда')
    result = []
    if isinstance(value, str) and not value.strip():
        return result  # CLI default '' for status/history: no objects, not one empty name
    for raw in value.split(',') if isinstance(value, str) else value:
        raw = raw.strip()
        parts = raw.split('.')
        if len(parts) not in (2, 4) or any(not re.fullmatch(r'[^\W\d]\w*', p, re.UNICODE) for p in parts):
            raise ValueError('repository: exact existing object names required; no root/wildcards/inline attributes in pilot')
        parts[0] = aliases.get(parts[0], parts[0]).replace('ё', 'е')
        if parts[0] not in {kind.replace('ё', 'е') for kind in EN_TO_RU_TYPE.values()}:
            raise ValueError('repository: unsupported metadata type')
        if len(parts) == 4:
            parts[2] = aliases.get(parts[2], parts[2])
            if parts[2] not in ('Форма', 'Макет', 'Команда'):
                raise ValueError('repository: inline parts require their owner, not a separate repository object')
        name = '.'.join(parts)
        if name not in result:
            result.append(name)
    return result


def validate_comment_fields(policy, data):
    """Optional private constraints; no company-specific limits in public code."""
    constraints = policy.get('field_constraints', {})
    distinct = policy.get('distinct_fields', [])
    if not isinstance(constraints, dict) or not isinstance(distinct, list):
        raise ValueError('repository: invalid comment field constraints')
    for field, limits in constraints.items():
        if field not in data or not isinstance(limits, dict) or not set(limits) <= {'min_length', 'max_length', 'single_line'}:
            raise ValueError('repository: invalid constrained comment field')
        value = data[field]
        low, high = limits.get('min_length', 0), limits.get('max_length', 16000)
        single = limits.get('single_line', False)
        if type(low) is not int or type(high) is not int or not 0 <= low <= high <= 16000 or type(single) is not bool:
            raise ValueError('repository: invalid comment field limits')
        if not isinstance(value, str) or not low <= len(value) <= high or (single and any(c in value for c in '\r\n\u0085\u2028\u2029')):
            raise ValueError(f'repository: comment field violates policy: {field}')
    for pair in distinct:
        if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(k, str) or k not in data for k in pair):
            raise ValueError('repository: invalid distinct comment fields')
        if ' '.join(data[pair[0]].split()).casefold() == ' '.join(data[pair[1]].split()).casefold():
            raise ValueError('repository: comment fields must differ')


def comment(root, db, task, summary, objects, explicit='', task_ref='', title='', description=''):
    summary = summary.strip()
    data = {'task_id': task, 'summary': summary, 'objects': ', '.join(objects), 'task_ref': task_ref,
            'title': title.strip(), 'description': description.strip().replace('\r\n', '\n')}
    policy_path = db['repository'].get('comment_policy_file')
    required = db['repository'].get('comment_policy_required', False)
    if type(required) is not bool or (required and not policy_path):
        raise ValueError('repository: required corporate comment policy is not configured')
    if policy_path:
        if not isinstance(policy_path, str):
            raise ValueError('repository: invalid comment policy path')
        path = (Path(root) / policy_path).resolve()
        if Path(root).resolve() not in (path, *path.parents):
            raise ValueError('repository: comment policy must be inside project root')
        policy = read_json(path)
        if not isinstance(policy, dict):
            raise ValueError('repository: invalid comment policy')
        template = policy.get('template')
        if not isinstance(template, str) or not template.strip():
            raise ValueError('repository: configured comment policy has no template')
        fields = {field for _, field, _, _ in string.Formatter().parse(template) if field}
        required_fields = policy.get('required_fields', [])
        if not isinstance(required_fields, list) or any(not isinstance(k, str) or k not in data for k in required_fields):
            raise ValueError('repository: invalid required comment fields')
        if not fields <= data.keys() or (not explicit and any(not data.get(k) for k in fields | set(required_fields))):
            raise ValueError('repository: required comment fields are missing or unsupported')
        text = (explicit.strip() if explicit else template.format(**data)).strip().replace('\r\n', '\n')
        pattern = policy.get('validation_regex')
        if explicit and not pattern:
            raise ValueError('repository: explicit corporate comment requires validation_regex')
        if pattern:
            if not isinstance(pattern, str):
                raise ValueError('repository: invalid comment regex')
            try:
                matches = re.fullmatch(pattern, text)
            except re.error as exc:
                raise ValueError('repository: invalid comment regex') from exc
            if matches is None:
                raise ValueError('repository: comment does not match corporate policy')
            # Validate the actual rendered/supplied text, never just the CLI inputs.
            captured = matches.groupdict()
            if not set(captured) <= data.keys():
                raise ValueError('repository: unsupported comment capture field')
            for key, value in captured.items():
                if value is None or (data.get(key) and data[key] != value):
                    raise ValueError('repository: comment fields differ from supplied values')
            data.update(captured)
        if policy.get('field_constraints') or policy.get('distinct_fields'):
            checked = set(policy.get('field_constraints', {})) if isinstance(policy.get('field_constraints', {}), dict) else set()
            pairs = policy.get('distinct_fields', [])
            if isinstance(pairs, list):
                for pair in pairs:
                    if isinstance(pair, list):
                        checked.update(k for k in pair if isinstance(k, str))
            if not pattern or not checked <= matches.groupdict().keys():
                raise ValueError('repository: constrained fields require named regex captures')
        if any(not data.get(k) for k in required_fields):
            raise ValueError('repository: required comment fields are missing')
        validate_comment_fields(policy, data)
    else:
        text = explicit.strip() if explicit else (f'{task}: {summary}' if task else f'Изменения: {summary}')
        if not explicit and not summary:
            raise ValueError('repository: a meaningful summary is required to generate the comment')
    if not text.strip() or len(text) > 16000 or '\x00' in text:
        raise ValueError('repository: empty/invalid comment')
    if any(os.environ.get(owner.get('password_env', ''), '') and os.environ[owner['password_env']] in text
           for owner in (db, db['repository'])):
        raise ValueError('repository: comment contains a credential value')
    return text


def secrets(db):
    return [os.environ.get(owner.get(k, ''), '') for owner in (db, db['repository'])
            for k in ('username_env', 'password_env')]


def redact(text, db):
    for value in sorted(set(secrets(db)), key=len, reverse=True):
        if value:
            text = text.replace(json.dumps(value, ensure_ascii=False)[1:-1], '***')
            text = text.replace(value.replace('"', '""'), '***').replace(value, '***')
    return text


def fingerprint(db):
    # Env *names* are included, values are not serialized.
    value = {k: v for k, v in db.items() if k not in {'repository'}}
    value['repository'] = {k: v for k, v in db['repository'].items() if k != 'comment_policy_file'}
    identities = [os.environ.get(owner.get('username_env', ''), '') for owner in (db, db['repository'])]
    value['identity_digest'] = hashlib.sha256(json.dumps(identities).encode()).hexdigest()
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def required_objects(config_src, files):
    from build_summaries import FOLDER_TO_EN_TYPE, EN_TO_RU_TYPE
    result = []
    prefix = config_src.replace('\\', '/').rstrip('/') + '/'
    for name in files:
        name = name.replace('\\', '/')
        if not name.startswith(prefix):
            raise ValueError('repository: apply file outside configSrc')
        parts = name[len(prefix):].split('/')
        if parts[0] in ('ConfigDumpInfo.xml', 'VERSION'):
            continue
        if len(parts) < 2 or parts[0] not in FOLDER_TO_EN_TYPE:
            raise ValueError('repository: cannot map apply file to an existing repository object')
        title = EN_TO_RU_TYPE[FOLDER_TO_EN_TYPE[parts[0]]] + '.' + Path(parts[1]).stem
        if len(parts) > 3 and parts[2] in ('Forms', 'Templates', 'Commands'):
            title += '.' + {'Forms': 'Форма', 'Templates': 'Макет', 'Commands': 'Команда'}[parts[2]] + '.' + Path(parts[3]).stem
        if parts[0] == 'Subsystems' and 'Subsystems' in parts[2:]:
            raise ValueError('repository: nested subsystems require an explicit pilot scope; automatic apply mapping unsupported')
        if title not in result:
            result.append(title)
    return object_names(result)


def receipt_root(root, task):
    if not re.fullmatch(r'TASK-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', task):
        raise ValueError('repository: TASK-ID is required for mutation receipts')
    return Path(root) / 'pilot-control' / task / 'repository'


def find_lock_receipt(root, task, db, objects, acquired_only=False):
    released = set()
    for path in sorted(receipt_root(root, task).glob('*/receipt.json'), reverse=True):
        entry = read_json(path)
        if entry.get('profile_hash') != fingerprint(db):
            continue
        result = entry.get('result', {})
        released.update(result.get('unlocked', []))
        denied = denied_objects(entry)
        released.update(n for n in objects if _object_key(n) in denied)
        if not entry.get('keep_locked_requested'):
            released.update(result.get('committed', []))
        if entry.get('state') in {'succeeded', 'no_changes'} and entry.get('operation') in {'commit', 'unlock'} and not entry.get('keep_locked_requested'):
            released.update(entry.get('objects', []))
        if (entry.get('operation') == 'lock' and (acquired_only or entry.get('state') == 'succeeded')
                and not set(objects) & released
                and (({_object_key(n) for n in objects} <= confirmed_captures(entry)) if acquired_only
                     else set(objects) <= set(entry.get('objects', [])))):
            entry['_receipt_path'] = str(path)
            return entry
    raise ValueError('repository: prepare the exact objects through 1c-repository before apply/commit')


def _object_key(name):
    return str(name).strip().lower().replace('ё', 'е')


def _names(values):
    out = set()
    for value in values or []:
        if isinstance(value, dict):  # locked_by_other: {"Name": ..., "Holder": ...}
            value = value.get('Name') or value.get('name') or ''
        out.add(_object_key(value))
    return out - {''}


def denied_objects(entry):
    """Explicit platform evidence that the task does NOT hold the lock on an object:
    «не захвачен», «захват отменен» or «захвачен другим пользователем» — in any state."""
    result = entry.get('result', {}) or {}
    return _names(result.get('not_locked')) | _names(result.get('unlocked')) | _names(result.get('locked_by_other'))


def received_objects(entry):
    """A newer object version was received from the repository: sources must be reconciled."""
    return _names((entry.get('result', {}) or {}).get('received'))


def confirmed_captures(entry):
    """acquired_now minus objects the same receipt contradicts (capture and denial at once)."""
    return {_object_key(n) for n in entry.get('acquired_now', [])} - denied_objects(entry)


def _released_objects(entry):
    """Objects a receipt released (same rules as find_lock_receipt)."""
    result = entry.get('result', {}) or {}
    released = {_object_key(n) for n in result.get('unlocked', [])}
    if not entry.get('keep_locked_requested'):
        released |= {_object_key(n) for n in result.get('committed', [])}
    if entry.get('state') in {'succeeded', 'no_changes'} and entry.get('operation') in {'commit', 'unlock'} \
            and not entry.get('keep_locked_requested'):
        released |= {_object_key(n) for n in entry.get('objects', [])}
    return released


def confirmed_lock_basis(root, task, db, objects):
    """Basis for a lock refresh: the latest lock receipt of this task/profile that covers the
    objects, where EVERY object has a valid chain back to an original confirmed capture:
    a succeeded lock receipt that lists the object in acquired_now. A previous refresh counts
    only as a link of such a chain (lock_kind=refresh, refresh_confirmed, refresh_of inside
    this task). Any release of the object after its original capture breaks the chain.
    Old receipts with the object in acquired_now are valid originals as they are."""
    root = Path(root).resolve()
    folder = receipt_root(root, task)
    paths = sorted(folder.glob('*/receipt.json'))          # oldest -> newest (stamp order)
    entries = {}
    for path in paths:
        try:
            entries[str(path)] = read_json(path)
        except (OSError, ValueError):
            continue
    mine = fingerprint(db)
    order = [str(p) for p in paths if str(p) in entries]
    wanted = [_object_key(n) for n in objects]

    def covers(entry, key):
        return key in {_object_key(n) for n in entry.get('objects', [])}

    basis = None
    for path in reversed(order):
        entry = entries[path]
        if entry.get('profile_hash') != mine:
            continue
        if entry.get('operation') == 'lock' and entry.get('state') == 'succeeded' \
                and all(covers(entry, k) for k in wanted):
            basis = path
            break
        # newer release/denial/received version of a wanted object: nothing older is a basis
        if received_objects(entry) & set(wanted):
            raise ValueError('repository: a newer object version was received after the prepare; '
                             'reconcile the sources (rebase), then prepare again')
        if denied_objects(entry) & set(wanted):
            raise ValueError('repository: platform reported a wanted object as not held by this task '
                             '(not locked / locked by another user); prepare again')
        if _released_objects(entry) & set(wanted):
            raise ValueError('repository: a wanted object was released after the prepare; prepare again')
    if basis is None:
        raise ValueError('repository: no confirmed prepare for these objects in this task; refresh refused')

    for key in wanted:
        current, seen = basis, set()
        while True:
            if current in seen or current not in entries:
                raise ValueError(f'repository: broken refresh chain for {key}; refresh refused')
            seen.add(current)
            entry = entries[current]
            if entry.get('profile_hash') != mine or entry.get('operation') != 'lock' \
                    or entry.get('state') != 'succeeded' or not covers(entry, key):
                raise ValueError(f'repository: refresh chain for {key} reaches an unconfirmed receipt; refresh refused')
            if key in denied_objects(entry) or key in received_objects(entry):
                raise ValueError(f'repository: {current} contradicts the capture of {key}; refresh refused')
            if key in confirmed_captures(entry):
                origin = current
                break
            link = entry.get('refresh_of')
            if entry.get('lock_kind') != 'refresh' or not link \
                    or 'refresh_confirmed' not in str(entry.get('lock_assessment', '')):
                raise ValueError(f'repository: no confirmed capture of {key} behind {current}; refresh refused')
            link = str(Path(link).resolve()) if Path(link).is_absolute() else str((Path(root) / link).resolve())
            if not Path(link).is_relative_to(folder.resolve()):
                raise ValueError(f'repository: refresh chain for {key} leaves this task; refresh refused')
            current = link
        for later in order[order.index(origin) + 1:]:
            entry = entries[later]
            if entry.get('profile_hash') != mine:
                continue
            if key in _released_objects(entry):
                raise ValueError(f'repository: {key} was released after its capture; prepare again')
            if key in denied_objects(entry):
                raise ValueError(f'repository: platform later reported {key} as not held by this task; prepare again')
            if key in received_objects(entry):
                raise ValueError(f'repository: a newer version of {key} was received after its capture; '
                                 'reconcile the sources (rebase), then prepare again')
    return basis


def file_hashes(root, paths):
    result = {}
    root = Path(root).resolve()
    for relative in paths:
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError('repository: apply evidence file missing or outside project')
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def record_apply(root, task, db_id, files, config_src):
    db = profile(root, db_id, 'commit')
    paths = list(files)
    paths += [f'specs/{task}/03_solution_spec.md', f'specs/{task}/06_change_report.md',
              f'pilot-control/{task}/review.md']
    data = {'state': 'applied', 'db': db_id, 'profile_hash': fingerprint(db),
            'objects': required_objects(config_src, files), 'file_hashes': file_hashes(root, paths)}
    folder = receipt_root(root, task)
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / 'apply.json.tmp'
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(folder / 'apply.json')


def check_publication_evidence(root, task, db, objects, existing_change):
    sdd = (Path(root) / 'specs' / task / '03_solution_spec.md').exists()
    if existing_change and not sdd:
        return 'explicit-existing-change; no Harness apply claim'
    path = receipt_root(root, task) / 'apply.json'
    if not path.is_file():
        raise ValueError('repository: no successful Harness apply evidence; standalone existing changes need --existing-change')
    entry = read_json(path)
    if (entry.get('state') != 'applied' or entry.get('db') != db['id']
            or entry.get('profile_hash') != fingerprint(db)
            or not set(objects) <= set(entry.get('objects', []))):
        raise ValueError('repository: apply evidence differs from publication scope/profile')
    if file_hashes(root, entry['file_hashes']) != entry['file_hashes']:
        raise ValueError('repository: source/spec/review changed after apply; publication blocked')
    return 'unchanged Harness apply/spec/review evidence'


def find_diff_receipt(root, task, db, objects):
    apply_path = receipt_root(root, task) / 'apply.json'
    for path in sorted(receipt_root(root, task).glob('*/receipt.json'), reverse=True):
        entry = read_json(path)
        if entry.get('profile_hash') != fingerprint(db):
            continue
        if entry.get('reconciliations') or entry.get('late_runner_result'):
            continue  # a reconciled run is never publication evidence, whatever its state says
        if entry.get('operation') == 'diff' and entry.get('state') == 'succeeded' and set(objects) <= set(entry.get('objects', [])):
            if not (path.parent / 'report.txt').is_file():
                break
            if apply_path.exists() and path.stat().st_mtime_ns < apply_path.stat().st_mtime_ns:
                break
            return str(path)
        if entry.get('operation') in WRITE_OPS and set(objects) & set(entry.get('objects', [])):
            break
    raise ValueError('repository: run and inspect a fresh diff for these objects after prepare/apply before publication')
