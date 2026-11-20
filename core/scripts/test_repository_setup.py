#!/usr/bin/env python3
"""Setup transaction tests on disposable profiles; no live 1C or credential writes."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import repository_setup as setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / '.v8-project.json'
        platform = self.root / 'platform with spaces' / '1cv8.exe'
        platform.parent.mkdir()
        platform.touch()  # Only existence checked: setup must never execute this file.
        self.config = {'custom': {'preserve': True}, 'databases': [
            {'id': 'test', 'type': 'server', 'server': 'server', 'ref': 'test',
             'environment': 'test', 'allow_apply': False, 'custom': ['preserved']},
            {'id': 'other', 'custom': {'untouched': True}}]}
        self.updates = {'v8path': str(platform), 'path': 'tcp://example.invalid/dev',
                        'environment': 'dev', 'username_env': 'TEST_REPO_USER',
                        'password_env': 'TEST_REPO_PASSWORD'}
        self.write()

    def write(self):
        self.path.write_bytes(b'\xef\xbb\xbf' + json.dumps(self.config, ensure_ascii=False, indent=2).replace('\n', '\r\n').encode())

    def test_plan_is_readonly_and_defaults_do_not_grant_write(self):
        before = self.path.read_bytes()
        report = setup.plan(self.root, 'test', self.updates)[3]
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('*.backup')), [])
        self.assertFalse(report['changes']['repository.allow_write'])
        self.assertFalse(report['changes']['repository.binding_confirmed'])

    def test_save_preserves_other_profiles_fields_and_backup_bytes(self):
        original = self.path.read_bytes()
        report = setup.plan(self.root, 'test', self.updates)[3]
        saved = setup.save(self.root, 'test', self.updates, report['approval_token'])
        self.assertEqual(saved['status'], 'saved')
        self.assertEqual((self.root / saved['backup']).read_bytes(), original)
        actual = setup.read_json(self.path)
        self.assertEqual(actual['custom'], self.config['custom'])
        self.assertEqual(actual['databases'][1], self.config['databases'][1])
        for key, value in self.config['databases'][0].items():
            self.assertEqual(actual['databases'][0][key], value)
        self.assertFalse(actual['databases'][0]['allow_apply'])

    def test_missing_or_wrong_approval_cannot_write(self):
        before = self.path.read_bytes()
        for token in ('', 'invalid'):
            with self.assertRaises(ValueError):
                setup.save(self.root, 'test', self.updates, token)
        self.assertEqual(before, self.path.read_bytes())

    def test_profile_or_plan_drift_invalidates_approval(self):
        token = setup.plan(self.root, 'test', self.updates)[3]['approval_token']
        changed = dict(self.updates, path='tcp://other.invalid/dev')
        with self.assertRaises(ValueError):
            setup.save(self.root, 'test', changed, token)
        self.config['custom']['new'] = True
        self.write()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            setup.save(self.root, 'test', self.updates, token)
        self.assertEqual(before, self.path.read_bytes())

    def test_policy_required_preserved_and_policy_drift_invalidates_approval(self):
        policy = self.root / 'corporate.json'
        policy.write_text(json.dumps({'template': '#{task_ref} {title}\n\n{description}'}))
        updates = dict(self.updates, comment_policy_file='corporate.json')
        planned = setup.plan(self.root, 'test', updates)
        self.assertTrue(json.loads(planned[2])['databases'][0]['repository']['comment_policy_required'])
        policy.write_text(json.dumps({'template': '{summary}'}))
        with self.assertRaises(ValueError):
            setup.save(self.root, 'test', updates, planned[3]['approval_token'])
        self.config['databases'][0]['repository'] = {'comment_policy_required': True}
        self.write()
        with self.assertRaisesRegex(ValueError, 'policy'):
            setup.plan(self.root, 'test', self.updates)

    def test_existing_custom_repository_fields_preserved(self):
        self.config['databases'][0]['repository'] = {'custom_corporate': {'required': True}}
        self.write()
        candidate = json.loads(setup.plan(self.root, 'test', self.updates)[2])
        self.assertEqual(candidate['databases'][0]['repository']['custom_corporate'], {'required': True})

    def test_changing_repository_resets_previous_binding_and_write(self):
        updates = dict(self.updates, allow_write=True, binding_confirmed=True)
        token = setup.plan(self.root, 'test', updates)[3]['approval_token']
        setup.save(self.root, 'test', updates, token)
        changed = {'path': 'tcp://other.invalid/dev'}
        candidate = json.loads(setup.plan(self.root, 'test', changed)[2])
        self.assertFalse(candidate['databases'][0]['repository']['binding_confirmed'])
        self.assertFalse(candidate['databases'][0]['repository']['allow_write'])

    def test_lock_contention_does_not_overwrite_or_remove_other_lock(self):
        lock = self.root / '.v8-project.json.setup.lock'
        lock.write_text('another setup')
        with self.assertRaises(FileExistsError):
            setup.save(self.root, 'test', self.updates, 'invalid')
        self.assertEqual(lock.read_text(), 'another setup')

    def test_failed_atomic_replace_preserves_original_and_cleans_temp(self):
        token = setup.plan(self.root, 'test', self.updates)[3]['approval_token']
        before = self.path.read_bytes()
        with mock.patch.object(setup.os, 'replace', side_effect=OSError('simulated write failure')):
            with self.assertRaises(OSError):
                setup.save(self.root, 'test', self.updates, token)
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(list(self.root.glob('*.tmp')))
        self.assertFalse((self.root / '.v8-project.json.setup.lock').exists())

    def test_invalid_targets_and_plaintext_do_not_write(self):
        for change in ({'path': 'tcp://user:secret@example.invalid/dev'},
                       {'v8path': str(self.root / 'absent.exe')},
                       {'username_env': 'BAD-NAME'}, {'environment': 'production'},
                       {'allow_write': True}, {'comment_policy_file': '../outside.json'},
                       {'allow_apply': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                setup.plan(self.root, 'test', dict(self.updates, **change))
        self.config['databases'][0]['password'] = 'secret'
        self.write()
        with self.assertRaises(ValueError):
            setup.plan(self.root, 'test', self.updates)

    def test_production_extensions_and_ambiguous_database_blocked(self):
        original = copy.deepcopy(self.config)
        for key, value in (('environment', 'production'), ('extension', 'extension')):
            self.config = copy.deepcopy(original)
            self.config['databases'][0][key] = value
            self.write()
            with self.assertRaises(ValueError):
                setup.plan(self.root, 'test', self.updates)
        self.config = original
        self.config['databases'].append(copy.deepcopy(original['databases'][0]))
        self.write()
        with self.assertRaises(ValueError):
            setup.plan(self.root, 'test', self.updates)

    def test_duplicate_json_keys_rejected(self):
        self.path.write_text('{"databases": [], "databases": []}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            setup.check(self.root, 'test')

    def test_check_never_echoes_credential_values(self):
        token = setup.plan(self.root, 'test', self.updates)[3]['approval_token']
        setup.save(self.root, 'test', self.updates, token)
        with mock.patch.dict(os.environ, {'TEST_REPO_USER': 'private-login', 'TEST_REPO_PASSWORD': 'private-password'}):
            result = setup.check(self.root, 'test')
        self.assertTrue(all(result['credential_presence'].values()))
        self.assertNotIn('private-', json.dumps(result))
        self.assertFalse(result['connection_checked'])

    def test_cli_installed_or_source_plan_save(self):
        script = Path(setup.__file__).resolve()
        flags = ['--db', 'test', '--v8path', self.updates['v8path'],
                 '--repository-path', self.updates['path'], '--repository-environment', 'dev',
                 '--username-env', 'TEST_REPO_USER', '--password-env', 'TEST_REPO_PASSWORD']
        def run(action, extra=()):
            result = subprocess.run([sys.executable, str(script), action] + flags + list(extra),
                                    cwd=self.root, capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        plan = run('plan')
        self.assertEqual(run('save', ['--approve', plan['approval_token']])['status'], 'saved')
        self.assertEqual(run('plan')['changes'], {})


if __name__ == '__main__':
    unittest.main()
