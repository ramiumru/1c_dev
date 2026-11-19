#!/usr/bin/env python3
"""Local-only repository selection for git_readonly.py resolve. Never fetches/clones."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
from urllib.parse import unquote, urlsplit

MAX_REPOS = 24
MAX_ENTRIES = 64
BUDGET_SECONDS = 15


def identity(value, task=False):
    """Server + full project path, with standard SSH/HTTPS transport equivalence."""
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('invalid repository URL')
    if re.match(r'^[A-Za-z]:[\\/]', value):
        raise ValueError('network repository URL required')
    if '://' in value:
        url = urlsplit(value)
        if url.scheme not in ({'http', 'https'} if task else {'http', 'https', 'ssh', 'git'}):
            raise ValueError('unsupported repository URL')
        if not url.hostname or url.password is not None or (url.username and url.scheme != 'ssh'):
            raise ValueError('credentials in repository URL are unsupported')
        host, path = url.hostname.lower(), url.path
        port = url.port
        if port and port != {'https': 443, 'http': 80, 'ssh': 22, 'git': 9418}[url.scheme]:
            host += ':' + str(port)
        if not task and (url.query or url.fragment):
            raise ValueError('invalid repository URL')
    else:
        if task:
            raise ValueError('HTTP task URL required')
        match = re.fullmatch(r'(?:[^/@:\s]+@)?([^/:\s]+):(.+)', value)
        if not match:
            raise ValueError('network repository URL required')
        host, path = match[1].lower(), match[2]
    path = unquote(path).strip('/')
    if task:
        match = re.fullmatch(r'(.+?)/(?:-/)?(?:work_items|issues|merge_requests)/[0-9]+', path)
        if not match:
            raise ValueError('unsupported task URL')
        path = match[1]
    elif path.endswith('.git'):
        path = path[:-4]
    if len(path.split('/')) < 2 or any(p in ('', '.', '..') for p in path.split('/')):
        raise ValueError('full namespace/project path required')
    return host, path


def label(project):
    return '/'.join(project) if project else None


def load_registry(path, workspace):
    path = Path(path)
    path = (workspace / path).resolve() if not path.is_absolute() else path.resolve()
    if workspace != path and workspace not in path.parents:
        raise ValueError('registry must be inside the workspace')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('unsupported registry version')
    rows = data.get('repositories')
    if not isinstance(rows, list) or not rows or len(rows) > MAX_REPOS:
        raise ValueError('registry requires a bounded nonempty repositories list')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', row['id']):
            raise ValueError('invalid registry id')
        if row['id'] in seen:
            raise ValueError('duplicate registry id')
        seen.add(row['id'])
        if not isinstance(row.get('path'), str) or not row['path'].strip() or any(ord(c) < 32 for c in row['path']):
            raise ValueError('invalid registry path')
        primary = identity(row.get('remote'))
        aliases = row.get('remote_aliases', [])
        if not isinstance(aliases, list) or len(aliases) > 8:
            raise ValueError('invalid remote aliases')
        row['_identities'] = {primary, *(identity(a) for a in aliases)}
        if any(item[1] != primary[1] for item in row['_identities']):
            raise ValueError('remote aliases must name the same full project path')
    return rows


def candidates(workspace):
    """Only workspace, immediate child roots, projects/* and projects/*/src."""
    paths, incomplete = [workspace], False
    for parent in (workspace, workspace / 'projects'):
        try:
            children = []
            for n, child in enumerate(parent.iterdir()):
                if n >= MAX_ENTRIES:
                    incomplete = True
                    break
                children.append(child)
            for child in sorted(children):
                if stat.S_ISDIR(child.stat().st_mode):
                    paths.append(child)
                    if parent == workspace / 'projects':
                        paths.append(child / 'src')
        except FileNotFoundError:
            continue
        except PermissionError:
            incomplete = True
    found = []
    for path in paths:
        try:
            (path / '.git').stat()  # .git file (worktree/submodule) is supported too.
            path = path.resolve()
            if path not in found:
                found.append(path)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except PermissionError:
            incomplete = True
    return found[:MAX_REPOS], incomplete or len(found) > MAX_REPOS


def probe(path, expected, git, deadline, local_scope=False):
    try:
        if not stat.S_ISDIR(path.stat().st_mode):
            return {'status': 'missing', 'path': str(path)}
    except FileNotFoundError:
        return {'status': 'missing', 'path': str(path)}
    except PermissionError:
        return {'status': 'access_denied', 'path': str(path)}
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    env['GIT_TERMINAL_PROMPT'] = '0'

    def read(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired('local repository probe', BUDGET_SECONDS)
        return subprocess.run([git, '-C', str(path), '-c', 'core.quotepath=false', *args],
                              capture_output=True, text=True, encoding='utf-8', errors='replace',
                              env=env, timeout=min(3, remaining))
    try:
        root = read('rev-parse', '--show-toplevel')
        if root.returncode:
            return {'status': 'git_error', 'path': str(path), 'git_exit': root.returncode}
        remote = read('remote', '-v')
        if remote.returncode:
            return {'status': 'git_error', 'path': str(path), 'git_exit': remote.returncode}
    except PermissionError:
        return {'status': 'access_denied', 'path': str(path)}
    except (OSError, subprocess.TimeoutExpired):
        return {'status': 'incomplete', 'path': str(path)}
    if local_scope and not expected and not remote.stdout.strip():
        return {'status': 'ready', 'path': str(Path(root.stdout.strip()).resolve()),
                'remote': None, 'repository': None, 'identity_basis': 'explicit_local_path'}
    actual, matched = set(), []
    for line in remote.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[2] != '(fetch)':
            continue
        try:
            project = identity(fields[1])
        except ValueError:
            continue
        actual.add(project)
        if (not expected or project in expected) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', fields[0]):
            matched.append((fields[0], project))
    if not matched:
        return {'status': 'mismatch' if actual else 'identity_unverified', 'path': str(path),
                'actual_repositories': sorted(label(p) for p in actual)}
    if not expected and len(actual) != 1:
        return {'status': 'ambiguous', 'path': str(path), 'actual_repositories': sorted(label(p) for p in actual)}
    name, project = sorted(matched, key=lambda x: (x[0] != 'origin', x[0]))[0]
    return {'status': 'ready', 'path': str(Path(root.stdout.strip()).resolve()),
            'remote': name, 'repository': label(project)}


def resolve(workspace, registry=None, task_url=None, repository=None, explicit_path=None):
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise ValueError('workspace directory unavailable')
    expected = {identity(task_url, task=True)} if task_url else set()
    rows = load_registry(registry, workspace) if registry else []
    selected = []
    if repository:
        selected = [row for row in rows if row['id'] == repository]
        if not selected:
            return {'status': 'unknown_repository', 'review_started': False}
        if expected and not expected & selected[0]['_identities']:
            return {'status': 'identity_conflict', 'review_started': False}
    elif expected and rows:
        selected = [row for row in rows if expected & row['_identities']]
        if not selected and not explicit_path:
            return {'status': 'unregistered', 'expected_repository': label(next(iter(expected))), 'review_started': False}
    elif rows and not explicit_path:
        if len(rows) != 1:
            return {'status': 'ambiguous', 'repository_ids': [r['id'] for r in rows], 'review_started': False}
        selected = rows
    if selected:
        expected = set().union(*(row['_identities'] for row in selected))
    # Multiple registered copies are not chosen silently, even if only one exists.
    if len(selected) > 1 and not explicit_path:
        return {'status': 'ambiguous', 'repository_ids': [r['id'] for r in selected], 'review_started': False}
    if explicit_path:
        raw_paths, incomplete = [Path(explicit_path)], False
    elif selected:
        raw_paths, incomplete = [Path(selected[0]['path'])], False
    else:
        raw_paths, incomplete = candidates(workspace)
    paths = [(workspace / p).resolve() if not p.is_absolute() else p.resolve() for p in raw_paths]
    git = shutil.which('git')
    if not git:
        return {'status': 'git_unavailable', 'review_started': False}
    deadline = time.monotonic() + BUDGET_SECONDS
    checks = []
    for path in paths:
        if time.monotonic() >= deadline:
            incomplete = True
            break
        checks.append(probe(path, expected, git, deadline, local_scope=bool(explicit_path) and not expected))
    ready = {c['path']: c for c in checks if c['status'] == 'ready'}
    if (explicit_path or selected) and len(checks) == 1 and checks[0]['status'] != 'ready':
        status = checks[0]['status']
    elif incomplete or any(c['status'] in {'incomplete', 'access_denied', 'git_error', 'identity_unverified', 'ambiguous'} for c in checks):
        status = 'incomplete'
    elif len(ready) > 1:
        status = 'ambiguous'
    elif len(ready) == 1:
        status = 'ready'
    elif explicit_path or selected:
        status = checks[0]['status'] if checks else 'missing'
    else:
        status = 'not_found_in_scope'
    result = {'status': status, 'review_started': False, 'checked': checks,
              'scope': 'explicit paths or bounded workspace roots; not the entire computer',
              'expected_repositories': sorted(label(p) for p in expected)}
    if status == 'ready':
        result.update(ready.popitem()[1])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', default='.')
    parser.add_argument('--registry')
    parser.add_argument('--task-url')
    parser.add_argument('--repository')
    parser.add_argument('--path', help='Explicit clone directory, relative to workspace or absolute')
    args = parser.parse_args(argv)
    try:
        result = resolve(args.workspace, args.registry, args.task_url, args.repository, args.path)
    except PermissionError:
        result = {'status': 'access_denied', 'review_started': False}
    except (ValueError, OSError):
        result = {'status': 'invalid_configuration', 'review_started': False}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'ready' else 1
