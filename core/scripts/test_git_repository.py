#!/usr/bin/env python3
"""Real disposable Git repositories; resolver never reads history or uses network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import git_repository as gate

SCRIPTS = Path(__file__).resolve().parent
URL = 'https://git.example.invalid/team/extension/-/work_items/731'
REMOTE = 'git@git.example.invalid:team/extension.git'


class RepositorySelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='git_repo_gate_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def git(self, repo, *args):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        run = subprocess.run(['git', '-C', str(repo), *args], env=env, capture_output=True, text=True,
                             encoding='utf-8', errors='replace')
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def repo(self, name, remote=REMOTE):
        path = self.root / 'projects' / name
        path.mkdir(parents=True)
        self.git(path, 'init', '--quiet')
        self.git(path, 'remote', 'add', 'origin', remote)
        return path

    def registry(self, *rows):
        path = self.root / 'git-repositories.json'
        path.write_text(json.dumps({'version': 1, 'repositories': list(rows)}), encoding='utf-8')
        return path

    def entry(self, name='extension', **extra):
        return dict(id=name, remote=REMOTE if name != 'main' else 'git@git.example.invalid:team/main.git', path='projects/' + name, **extra)

    def test_standard_ssh_https_and_full_project_identity(self):
        expected = ('git.example.invalid', 'team/extension')
        self.assertEqual(gate.identity(REMOTE), expected)
        self.assertEqual(gate.identity('ssh://git@git.example.invalid:22/team/extension.git'), expected)
        self.assertEqual(gate.identity(URL, task=True), expected)
        self.assertNotEqual(gate.identity('git@other.invalid:team/extension.git'), expected)
        self.assertNotEqual(gate.identity('git@git.example.invalid:other/extension.git'), expected)
        for url in ('https://user:password@git.example.invalid/team/extension.git', 'file:///tmp/repo', 'C:/repo'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                gate.identity(url)

    def test_only_wrong_clone_stops_before_history_or_network(self):
        wrong = self.repo('main', 'git@git.example.invalid:team/main.git')
        before = (wrong / '.git/config').read_bytes()
        real = subprocess.run
        with patch('git_repository.subprocess.run', wraps=real) as run:
            result = gate.resolve(self.root, task_url=URL)
        self.assertEqual(result['status'], 'not_found_in_scope')
        self.assertFalse(result['review_started'])
        self.assertTrue(run.call_args_list)
        self.assertTrue(all(call.args[0][5] in ('rev-parse', 'remote') for call in run.call_args_list))
        self.assertEqual(before, (wrong / '.git/config').read_bytes())

    def test_registered_missing_extension_does_not_probe_main(self):
        self.repo('main', 'git@git.example.invalid:team/main.git')
        registry = self.registry(self.entry('main'), self.entry())
        with patch('git_repository.subprocess.run') as run:
            result = gate.resolve(self.root, registry, task_url=URL, repository='extension')
        self.assertEqual(result['status'], 'missing')
        run.assert_not_called()

    def test_registry_selects_matching_extension(self):
        path = self.repo('extension')
        self.repo('main', 'git@git.example.invalid:team/main.git')
        result = gate.resolve(self.root, self.registry(self.entry()), task_url=URL)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(Path(result['path']), path)
        self.assertEqual(result['remote'], 'origin')

    def test_explicit_wrong_path_is_not_silently_replaced(self):
        wrong = self.repo('main', 'git@git.example.invalid:team/main.git')
        self.repo('extension')
        self.assertEqual(gate.resolve(self.root, task_url=URL, explicit_path=wrong)['status'], 'mismatch')

    def test_number_only_and_multiple_registered_repositories_requires_selection(self):
        registry = self.registry(self.entry('main'), self.entry())
        with patch('git_repository.subprocess.run') as run:
            self.assertEqual(gate.resolve(self.root, registry)['status'], 'ambiguous')
        run.assert_not_called()

    def test_unregistered_project_and_conflicting_alias_stop_early(self):
        row = dict(id='main', path='projects/main', remote='git@git.example.invalid:team/main.git')
        registry = self.registry(row)
        with patch('git_repository.subprocess.run') as run:
            self.assertEqual(gate.resolve(self.root, registry, task_url=URL)['status'], 'unregistered')
            self.assertEqual(gate.resolve(self.root, registry, task_url=URL, repository='main')['status'], 'identity_conflict')
            self.assertEqual(gate.resolve(self.root, registry, repository='unknown')['status'], 'unknown_repository')
        run.assert_not_called()

    def test_multiple_matching_clones_require_explicit_path(self):
        self.repo('copy-a'); chosen = self.repo('copy-b')
        self.assertEqual(gate.resolve(self.root, task_url=URL)['status'], 'ambiguous')
        self.assertEqual(gate.resolve(self.root, task_url=URL, explicit_path=chosen)['status'], 'ready')

    def test_remote_alias_is_explicit_and_nonstandard_ports_are_not_guessed(self):
        self.repo('extension', 'ssh://git@ssh.example.invalid:2222/team/extension.git')
        self.assertEqual(gate.resolve(self.root, task_url=URL)['status'], 'not_found_in_scope')
        registry = self.registry(self.entry(remote_aliases=['ssh://git@ssh.example.invalid:2222/team/extension.git']))
        self.assertEqual(gate.resolve(self.root, registry, task_url=URL)['status'], 'ready')

    def test_worktree_git_file_is_supported(self):
        seed = self.repo('seed')
        self.git(seed, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'fixture')
        worktree = self.root / 'projects' / 'extension'
        self.git(seed, 'worktree', 'add', '--quiet', '-b', 'review-fixture', str(worktree))
        self.assertTrue((worktree / '.git').is_file())
        result = gate.resolve(self.root, task_url=URL, explicit_path=worktree)
        self.assertEqual(result['status'], 'ready')

    def test_permission_error_and_timeout_are_not_missing_clone(self):
        path = self.repo('extension')
        original = Path.stat
        def stat(p, *args, **kwargs):
            if p == path:
                raise PermissionError('fixture')
            return original(p, *args, **kwargs)
        with patch.object(Path, 'stat', stat):
            self.assertEqual(gate.resolve(self.root, task_url=URL, explicit_path=path)['status'], 'access_denied')
        with patch('git_repository.subprocess.run', side_effect=subprocess.TimeoutExpired('fixture', 3)) as run:
            self.assertEqual(gate.resolve(self.root, task_url=URL, explicit_path=path)['status'], 'incomplete')
        self.assertEqual(run.call_count, 1)

    def test_existing_directory_without_git_is_not_reported_as_missing_clone(self):
        directory = self.root / 'plain'
        directory.mkdir()
        result = gate.resolve(self.root, task_url=URL, explicit_path=directory)
        self.assertEqual(result['status'], 'git_error')
        self.assertIn('git_exit', result['checked'][0])

    def test_missing_explicit_clone_does_not_fall_back(self):
        self.repo('main', 'git@git.example.invalid:team/main.git')
        with patch('git_repository.subprocess.run') as run:
            self.assertEqual(gate.resolve(self.root, task_url=URL, explicit_path='missing')['status'], 'missing')
        run.assert_not_called()

    def test_discovery_is_bounded_and_never_claims_full_machine_search(self):
        self.repo('extension')
        with patch.object(gate, 'MAX_ENTRIES', 0):
            result = gate.resolve(self.root, task_url=URL)
        self.assertEqual(result['status'], 'incomplete')
        self.assertIn('not the entire computer', result['scope'])

    def test_bad_registry_fails_without_git_queries(self):
        path = self.registry(self.entry(), self.entry())
        with patch('git_repository.subprocess.run') as run, self.assertRaises(ValueError):
            gate.resolve(self.root, path, task_url=URL)
        run.assert_not_called()
        with self.assertRaises(ValueError):
            gate.load_registry(self.root.parent / 'outside.json', self.root)

    def test_explicit_local_repository_without_remote_remains_supported(self):
        path = self.repo('local')
        self.git(path, 'remote', 'remove', 'origin')
        result = gate.resolve(self.root, explicit_path=path)
        self.assertEqual(result['status'], 'ready')
        self.assertIsNone(result['remote'])
        self.assertEqual(result['identity_basis'], 'explicit_local_path')

    def test_external_task_cannot_use_local_repository_without_remote(self):
        path = self.repo('local')
        self.git(path, 'remote', 'remove', 'origin')
        self.assertEqual(gate.resolve(self.root, task_url=URL, explicit_path=path)['status'], 'identity_unverified')

    def test_wrapper_dispatch_and_failure_exit(self):
        path = self.repo('extension')
        wrapper = SCRIPTS / 'git_readonly.py'
        result = subprocess.run([sys.executable, str(wrapper), 'resolve', '--workspace', str(self.root), '--task-url', URL],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['path'], str(path))
        result = subprocess.run([sys.executable, str(wrapper), 'resolve', '--workspace', str(self.root), '--path', 'missing'],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'missing')


if __name__ == '__main__':
    unittest.main(verbosity=2)
