#!/usr/bin/env python3
"""Repository pilot regressions. No real 1C or repository operations."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import repository_support as support
import safe_repository as runner


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = patch.dict(os.environ, {'TEST_REPO_LOGIN': 'fixture-login', 'TEST_REPO_PASSWORD': 'fixture-secret'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = {'id': 'test', 'type': 'file', 'path': str(self.root / 'fake-db'),
                   'environment': 'test', 'allow_apply': True, 'configSrc': 'projects/pet/src',
                   'repository': {'enabled': True, 'path': 'tcp://fake.invalid/dev', 'environment': 'dev',
                                  'allow_write': True, 'binding_confirmed': True,
                                  'username_env': 'TEST_REPO_LOGIN', 'password_env': 'TEST_REPO_PASSWORD'}}
        self.task = 'TASK-TEST'
        self.objects = ['ОбщийМодуль.Example']
        self.save()
        ps = self.root / '.agents/skills/1c-repository-manage/scripts/repo-ops.ps1'
        ps.parent.mkdir(parents=True)
        ps.write_text('# mock only', encoding='utf-8')

    def save(self):
        (self.root / '.v8-project.json').write_text(json.dumps({'v8path': str(self.root / 'fake-1cv8.exe'), 'databases': [self.db]}), encoding='utf-8')

    def profile(self, op='lock'):
        return support.profile(self.root, 'test', op)

    def receipt(self, state='succeeded', acquired=None, operation='lock', key='0001'):
        path = support.receipt_root(self.root, self.task) / key
        path.mkdir(parents=True, exist_ok=True)
        value = {'operation': operation, 'state': state, 'objects': self.objects,
                 'profile_hash': support.fingerprint(self.profile()),
                 'acquired_now': self.objects if acquired is None else acquired}
        (path / 'receipt.json').write_text(json.dumps(value), encoding='utf-8')
        return value

    def test_profile_write_gates_and_separate_environments(self):
        for field, value in [('environment', 'production')]:
            original = self.db[field]
            self.db[field] = value; self.save()
            with self.assertRaises(ValueError): self.profile()
            self.db[field] = original
        for field, value in [('enabled', False), ('allow_write', False), ('binding_confirmed', False),
                             ('environment', 'prod'), ('environment', 'production'), ('extension', 'Ext')]:
            original = self.db['repository'].get(field)
            self.db['repository'][field] = value; self.save()
            with self.assertRaises(ValueError): self.profile()
            self.db['repository'].pop(field)
            if original is not None: self.db['repository'][field] = original
        self.save()
        self.assertEqual(self.profile()['id'], 'test')
        self.db['allow_apply'] = False; self.save()
        self.assertEqual(self.profile()['id'], 'test')  # standalone repository rights do not enable apply

    def test_duplicate_db_missing_env_and_plaintext_are_refused(self):
        cfg = json.loads((self.root / '.v8-project.json').read_text())
        cfg['databases'].append(self.db)
        (self.root / '.v8-project.json').write_text(json.dumps(cfg))
        with self.assertRaises(ValueError): self.profile()
        self.save()
        with patch.dict(os.environ, {'TEST_REPO_PASSWORD': ''}):
            with self.assertRaises(ValueError): self.profile()
        self.db['repository']['password'] = 'do-not-use'; self.save()
        with self.assertRaises(ValueError): self.profile()

    def test_readonly_does_not_claim_binding(self):
        self.db['repository']['binding_confirmed'] = False; self.save()
        self.assertFalse(self.profile('status')['repository']['binding_confirmed'])
        with self.assertRaises(ValueError): self.profile()

    def test_repository_transports_and_bad_uris_share_runtime_validation(self):
        for address in ('tcp://example.invalid/dev', 'http://example.invalid/repo/main.1ccr/dev',
                        'https://example.invalid:8443/repo/dev', str(self.root / 'repository folder')):
            self.db['repository']['path'] = address
            self.save()
            self.assertEqual(self.profile('status')['repository']['path'], address)
        for address in ('http://user:secret@example.invalid/dev', 'https://user@example.invalid/dev',
                        'tcp://user:secret@example.invalid/dev', 'http:///dev',
                        'https://example.invalid:bad/dev', 'ftp://example.invalid/dev'):
            self.db['repository']['path'] = address
            self.save()
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.profile('status')

    def test_user_environment_refresh_is_explicit_fresh_and_has_no_stale_fallback(self):
        with patch.dict(os.environ, {'TEST_REPO_LOGIN': 'stale', 'TEST_REPO_PASSWORD': 'stale-secret'}):
            with patch.object(support, 'windows_user_environment', side_effect=['new-login', 'new%PASSWORD%']) as read:
                support.refresh_repository_credentials(dict(self.db['repository'], credential_source='windows-user-env'))
                self.assertEqual(os.environ['TEST_REPO_PASSWORD'], 'new%PASSWORD%')
                self.assertEqual(os.environ['TEST_REPO_LOGIN'], 'new-login')
                self.assertEqual(read.call_count, 2)
            with patch.object(support, 'windows_user_environment', return_value=''):
                support.refresh_repository_credentials(dict(self.db['repository'], credential_source='windows-user-env'))
                self.assertNotIn('TEST_REPO_PASSWORD', os.environ)
            with patch.object(support, 'windows_user_environment') as read:
                support.refresh_repository_credentials(self.db['repository'])
                read.assert_not_called()

    def test_user_environment_cannot_replace_system_variables(self):
        with patch.object(support, 'windows_user_environment') as read:
            with self.assertRaises(ValueError):
                support.refresh_repository_credentials({'credential_source': 'windows-user-env',
                                                        'username_env': 'PATH', 'password_env': 'PASSWORD'})
            read.assert_not_called()

    def test_repository_disabled_does_not_add_platform_steps_to_apply(self):
        for config in (None, {'enabled': False}):
            if config is None:
                self.db.pop('repository', None)
            else:
                self.db['repository'] = config
            self.save()
            with patch('safe_repository.profile') as profile, patch('safe_repository.execute') as execute:
                runner.before_apply(self.root, 'test', self.task, [], 'projects/pet/src', False)
                profile.assert_not_called()
                execute.assert_not_called()

    def test_fresh_diff_is_required_after_the_latest_prepare(self):
        self.receipt()
        with self.assertRaises(ValueError): support.find_diff_receipt(self.root, self.task, self.profile(), self.objects)
        self.receipt(operation='diff', key='0002')
        report = support.receipt_root(self.root, self.task) / '0002/report.txt'
        report.write_text('fixture diff')
        self.assertTrue(support.find_diff_receipt(self.root, self.task, self.profile(), self.objects))
        self.receipt(key='0003')
        with self.assertRaises(ValueError): support.find_diff_receipt(self.root, self.task, self.profile(), self.objects)

    def test_corporate_business_reference_is_not_invented(self):
        db = self.profile(); db['repository']['comment_policy_required'] = True
        with self.assertRaises(ValueError): support.comment(self.root, db, self.task, 'текст', self.objects)
        db['repository']['comment_policy_file'] = 'policy.json'
        (self.root / 'policy.json').write_text(json.dumps({'template': '#{task_ref}: {summary}', 'required_fields': ['task_ref']}))
        with self.assertRaises(ValueError): support.comment(self.root, db, self.task, 'текст', self.objects)
        self.assertEqual(support.comment(self.root, db, self.task, 'текст', self.objects, task_ref='123'), '#123: текст')

    def test_exact_object_types_are_preserved(self):
        self.assertEqual(support.object_names('Catalog.X,Document.X,Report.Ёлка'),
                         ['Справочник.X', 'Документ.X', 'Отчет.Ёлка'])
        for value in ('*', 'Конфигурация', 'Document.X.Attribute.Y', 'Document.X;Stop', '../X'):
            with self.subTest(value=value), self.assertRaises(ValueError): support.object_names(value)

    def structured_policy(self):
        db = self.profile()
        db['repository']['comment_policy_file'] = 'policy.json'
        policy = {'template': '{task_ref} {title}\n\n{description}',
                  'required_fields': ['task_ref', 'title', 'description'],
                  'validation_regex': r'(?P<task_ref>[0-9]+) (?P<title>[^\s\r\n][^\r\n]*)\n\n(?P<description>[^\r\n]+)',
                  'field_constraints': {'title': {'min_length': 3, 'max_length': 8, 'single_line': True},
                                        'description': {'min_length': 6, 'max_length': 12, 'single_line': True}},
                  'distinct_fields': [['title', 'description']]}
        (self.root / 'policy.json').write_text(json.dumps(policy), encoding='utf-8')
        return db, policy

    def test_structured_comment_generated_and_supplied_are_both_validated(self):
        db, _ = self.structured_policy()
        self.assertEqual(support.comment(self.root, db, self.task, '', self.objects,
                         task_ref='42', title='Change', description='Explain why'), '42 Change\n\nExplain why')
        self.assertEqual(support.comment(self.root, db, self.task, '', self.objects,
                         explicit='42 Change\r\n\r\nExplain why'), '42 Change\n\nExplain why')
        for text in ('42  Change\n\nExplain why', '42 Change\nExplain why', '42 Change\n\n\nExplain why',
                     '42 XX\n\nExplain why', '42 LongTitle\n\nExplain why',
                     '42 Change\n\nShort', '42 Change\n\nDescription too long', '42 Change\n\nChange'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                support.comment(self.root, db, self.task, '', self.objects, explicit=text)

    def test_structured_comment_limits_are_inclusive(self):
        db, _ = self.structured_policy()
        for title, description in [('ABC', '123456'), ('ABCDEFGH', '123456789012')]:
            self.assertTrue(support.comment(self.root, db, self.task, '', self.objects,
                            task_ref='42', title=title, description=description))

    def test_structured_comment_cannot_validate_different_inputs_or_equal_text(self):
        db, _ = self.structured_policy()
        for kwargs in ({'title': 'CHANGE', 'description': 'change', 'task_ref': '42'},
                       {'explicit': '42 Change\n\nExplain why', 'task_ref': '99'},
                       {'title': 'Change\u2028x', 'description': 'Explain why', 'task_ref': '42'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                support.comment(self.root, db, self.task, '', self.objects, **kwargs)

    def test_malformed_structured_policy_is_refused(self):
        db, policy = self.structured_policy()
        for extra in ({'field_constraints': []}, {'field_constraints': {'title': {'min_length': True}}},
                      {'field_constraints': {'title': {'min_length': 9, 'max_length': 8}}},
                      {'distinct_fields': 'title'}, {'distinct_fields': [['unknown', 'title']]},
                      {'validation_regex': r'[0-9]+ [^\n]+\n\n[^\n]+'}):
            (self.root / 'policy.json').write_text(json.dumps(dict(policy, **extra)), encoding='utf-8')
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                support.comment(self.root, db, self.task, '', self.objects,
                                task_ref='42', title='Change', description='Explain why')

    def test_invalid_structured_comment_stops_before_platform(self):
        db, _ = self.structured_policy()
        self.db['repository'] = db['repository']; self.save()
        with patch('safe_repository.subprocess.run') as run:
            with self.assertRaises(ValueError):
                runner.execute(self.root, 'test', 'commit', self.objects, self.task, intent='publish',
                               task_ref='42', title='XX', description='Explain why')
            run.assert_not_called()

    def test_public_and_corporate_comments_no_fallback(self):
        db = self.profile()
        self.assertEqual(support.comment(self.root, db, self.task, 'уточнён текст', self.objects), 'TASK-TEST: уточнён текст')
        self.assertEqual(support.comment(self.root, db, self.task, '', self.objects, 'Готовый текст'), 'Готовый текст')
        for summary in ('', 'fixture-secret'):
            with self.assertRaises(ValueError): support.comment(self.root, db, self.task, summary, self.objects)
        db['repository']['comment_policy_file'] = 'private/comment.json'
        with self.assertRaises(OSError): support.comment(self.root, db, self.task, 'текст', self.objects)
        (self.root / 'private').mkdir()
        (self.root / 'private/comment.json').write_text(json.dumps({'template': '[{task_id}] {summary}', 'required_fields': ['summary'], 'validation_regex': r'\[TASK-TEST\] .+'}))
        self.assertEqual(support.comment(self.root, db, self.task, 'текст', self.objects), '[TASK-TEST] текст')
        with self.assertRaises(ValueError): support.comment(self.root, db, self.task, 'текст', self.objects, 'wrong')

    def test_intent_and_dry_run_do_not_launch_platform(self):
        with patch.object(runner.subprocess, 'run') as process:
            with self.assertRaises(ValueError): runner.execute(self.root, 'test', 'lock', self.objects, self.task)
            self.assertEqual(runner.execute(self.root, 'test', 'lock', self.objects, self.task, intent='prepare', dry_run=True), 0)
            process.assert_not_called()

    def test_partial_lock_can_be_released_but_preexisting_cannot(self):
        self.receipt(state='unverified')
        self.assertTrue(support.find_lock_receipt(self.root, self.task, self.profile(), self.objects, acquired_only=True))
        with self.assertRaises(ValueError): support.find_lock_receipt(self.root, self.task, self.profile(), self.objects)
        self.receipt(acquired=[], key='0002')
        self.assertTrue(support.find_lock_receipt(self.root, self.task, self.profile(), self.objects))
        self.receipt(operation='unlock', key='0003')
        with self.assertRaises(ValueError): support.find_lock_receipt(self.root, self.task, self.profile(), self.objects, acquired_only=True)

    def test_changed_repository_identity_cannot_reuse_old_captures(self):
        self.receipt()
        with patch.dict(os.environ, {'TEST_REPO_LOGIN': 'another-login'}):
            with self.assertRaises(ValueError): support.find_lock_receipt(self.root, self.task, self.profile(), self.objects)

    def test_no_apply_or_source_drift_blocks_publication(self):
        db = self.profile()
        with self.assertRaises(ValueError): support.check_publication_evidence(self.root, self.task, db, self.objects, False)
        self.assertIn('existing-change', support.check_publication_evidence(self.root, self.task, db, self.objects, True))
        files = ['projects/pet/src/CommonModules/Example/Ext/Module.bsl',
                 f'specs/{self.task}/03_solution_spec.md', f'specs/{self.task}/06_change_report.md',
                 f'pilot-control/{self.task}/review.md']
        for name in files:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('fixture')
        with self.assertRaises(ValueError): support.check_publication_evidence(self.root, self.task, db, self.objects, True)
        support.record_apply(self.root, self.task, 'test', files[:1], 'projects/pet/src')
        self.assertIn('unchanged', support.check_publication_evidence(self.root, self.task, db, self.objects, False))
        (self.root / files[0]).write_text('changed')
        with self.assertRaises(ValueError): support.check_publication_evidence(self.root, self.task, db, self.objects, False)

    def test_runner_env_names_only_and_durable_sanitized_results(self):
        def fake(args, **kwargs):
            self.assertNotIn('fixture-secret', ' '.join(args))
            self.assertNotIn('fixture-login', ' '.join(args))
            self.assertIn('-RepositoryPasswordEnv', args)
            log = Path(args[args.index('-LogDirectory') + 1])
            (log / 'result.json').write_text(json.dumps({'platform_started': True, 'effective_exit': 0, 'locked': self.objects}))
            return subprocess.CompletedProcess(args, 0, 'fixture-secret', '')
        with patch.object(runner.shutil, 'which', return_value='pwsh'), patch.object(runner.subprocess, 'run', side_effect=fake):
            self.assertEqual(runner.execute(self.root, 'test', 'lock', self.objects, self.task, intent='prepare'), 0)
        log = next(support.receipt_root(self.root, self.task).glob('*/runner.log'))
        self.assertNotIn('fixture-secret', log.read_text())
        self.assertEqual(json.loads(log.with_name('receipt.json').read_text())['state'], 'succeeded')

    def test_actual_status_receipt_does_not_claim_manual_binding(self):
        self.db['repository']['binding_confirmed'] = False; self.save()
        def fake(args, **kwargs):
            log = Path(args[args.index('-LogDirectory') + 1])
            (log / 'result.json').write_text(json.dumps({'platform_started': True, 'effective_exit': 0}))
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(runner.shutil, 'which', return_value='pwsh'), patch.object(runner.subprocess, 'run', side_effect=fake):
            self.assertEqual(runner.execute(self.root, 'test', 'status', [], ''), 0)
        result = support.read_json(next((self.root / 'logs/repository-inspect').glob('*/receipt.json')))
        self.assertIn('unverified', result['binding_evidence'])

    def test_cli_empty_objects_string_for_read_ops(self):
        # CLI passes --objects '' by default; it must mean "no objects", not one empty name
        self.assertEqual(support.object_names(''), [])
        self.assertEqual(support.object_names('  '), [])
        self.assertEqual(runner.execute(self.root, 'test', 'status', '', '', dry_run=True), 0)
        self.assertEqual(runner.execute(self.root, 'test', 'history', '', '', dry_run=True), 0)

    def test_diff_requires_explicit_repository_version(self):
        with self.assertRaises(ValueError): runner.execute(self.root, 'test', 'diff', self.objects, self.task, dry_run=True)
        with self.assertRaises(ValueError): runner.execute(self.root, 'test', 'diff', self.objects, self.task, dry_run=True, version=0)
        with self.assertRaises(ValueError): runner.execute(self.root, 'test', 'status', '', '', dry_run=True, version=5)
        self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, dry_run=True, version=5), 0)

    def test_version_and_timeout_reach_runner(self):
        seen = {}
        def fake(args, **kwargs):
            seen['args'], seen['timeout'] = args, kwargs.get('timeout')
            log = Path(args[args.index('-LogDirectory') + 1])
            (log / 'result.json').write_text(json.dumps({'platform_started': True, 'effective_exit': 0}))
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(runner.shutil, 'which', return_value='pwsh'), patch.object(runner.subprocess, 'run', side_effect=fake):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=42), 0)
        args = seen['args']
        self.assertEqual(args[args.index('-Version') + 1], '42')
        self.assertEqual(args[args.index('-TimeoutSeconds') + 1], str(runner.DEFAULT_TIMEOUT_SECONDS))
        self.assertEqual(seen['timeout'], runner.DEFAULT_TIMEOUT_SECONDS + runner.RUNNER_GRACE_SECONDS)

    def test_runner_timeout_is_unverified_not_started(self):
        def fake(args, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs.get('timeout'))
        with patch.object(runner.shutil, 'which', return_value='pwsh'), patch.object(runner.subprocess, 'run', side_effect=fake):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=3), 1)
        receipt = support.read_json(next(support.receipt_root(self.root, self.task).glob('*/receipt.json')))
        self.assertEqual(receipt['state'], 'unverified')
        self.assertIn('1cv8', receipt['diagnostic'])

    def test_profile_timeout_bounds(self):
        for bad in (10, 99999, '900', True):
            self.db['repository']['timeout_seconds'] = bad; self.save()
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                runner.execute(self.root, 'test', 'status', '', '', dry_run=True)

    def test_background_only_for_read_operations(self):
        with self.assertRaises(ValueError):
            runner.execute(self.root, 'test', 'lock', self.objects, self.task, intent='prepare', dry_run=True, background=True)

    def _background_diff(self, version=9):
        spawned = []
        with patch.object(runner.shutil, 'which', return_value='pwsh'), \
             patch.object(runner.subprocess, 'Popen', side_effect=lambda cmd, **kw: spawned.append(cmd)):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=version, background=True), 0)
        self.assertEqual(len(spawned), 1)
        log = Path(spawned[0][spawned[0].index('--run-job') + 1])
        return log, support.read_json(log / 'receipt.json')

    def _platform(self, log, calls):
        def fake(args, **kwargs):
            calls.append(args)
            (log / 'result.json').write_text(json.dumps({'platform_started': True, 'effective_exit': 0}))
            (log / 'report.txt').write_text('fixture report', encoding='utf-8')
            return subprocess.CompletedProcess(args, 0, '', '')
        return fake

    def _run_job(self, log, calls):
        with patch.object(runner.shutil, 'which', return_value='pwsh'), \
             patch.object(runner.subprocess, 'run', side_effect=self._platform(log, calls)):
            return runner.run_job(self.root, log)

    def test_background_start_records_request_and_holds_database(self):
        log, receipt = self._background_diff()
        self.assertEqual(receipt['state'], 'running')
        self.assertEqual(receipt['timeout_seconds'], runner.BACKGROUND_TIMEOUT_SECONDS)
        self.assertEqual(receipt['request_sha256'], runner.request_digest(receipt))
        self.assertFalse((log / 'job.json').exists())  # no separate, unbound command file
        self.assertEqual(runner.db_lock_holder(self.root, 'test')['receipt'], str(log / 'receipt.json'))
        with self.assertRaises(ValueError):  # busy: even dry-run of another operation is refused
            runner.execute(self.root, 'test', 'status', '', '', dry_run=True)

    def test_background_job_completes_releases_database_and_is_evidence(self):
        log, _ = self._background_diff()
        self.assertEqual(runner.wait_receipt(self.root, log / 'receipt.json', max_wait=0), runner.WAIT_RUNNING)
        calls = []
        self.assertEqual(self._run_job(log, calls), 0)
        self.assertEqual(len(calls), 1)
        args = calls[0]
        self.assertEqual(args[args.index('-Version') + 1], '9')
        self.assertEqual(args[args.index('-Objects') + 1], ','.join(self.objects))
        self.assertEqual(args[args.index('-TimeoutSeconds') + 1], str(runner.BACKGROUND_TIMEOUT_SECONDS))
        final = support.read_json(log / 'receipt.json')
        self.assertEqual((final['state'], final['outcome_known']), ('succeeded', True))
        self.assertIsNone(runner.db_lock_holder(self.root, 'test'))
        self.assertEqual(runner.wait_receipt(self.root, log / 'receipt.json', max_wait=0), runner.WAIT_DONE)
        self.assertEqual(support.find_diff_receipt(self.root, self.task, self.profile('commit'), self.objects), str(log / 'receipt.json'))

    def test_modified_request_is_never_executed(self):
        # review P1: receipt evidence must match what actually ran; each field change is refused
        for field, value in (('objects', ['ОбщийМодуль.Other']), ('repository_version', 123),
                             ('operation', 'status'), ('operation', 'history'), ('operation', 'lock'),
                             ('timeout_seconds', 60), ('db', 'other'), ('profile_hash', 'x')):
            with self.subTest(field=field, value=value):
                log, receipt = self._background_diff()
                receipt[field] = value
                (log / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
                calls = []
                with self.assertRaises(ValueError):
                    self._run_job(log, calls)
                self.assertEqual(calls, [])
                final = support.read_json(log / 'receipt.json')
                self.assertEqual(final['state'], 'not_started')
                self.assertTrue(final['finished_at'] and final['diagnostic'])
                self.assertEqual(runner.wait_receipt(self.root, log / 'receipt.json', max_wait=0), runner.WAIT_FAILED)
                # a known non-launch releases whatever lock this receipt held, whatever its db field says
                self.assertIsNone(runner.db_lock_holder(self.root, 'test'))

    def test_profile_drift_after_submission_is_not_started(self):
        log, _ = self._background_diff()
        self.db['repository']['background_timeout_seconds'] = 600; self.save()
        calls = []
        with self.assertRaises(ValueError):
            self._run_job(log, calls)
        self.assertEqual(calls, [])
        self.assertEqual(support.read_json(log / 'receipt.json')['state'], 'not_started')
        self.assertIsNone(runner.db_lock_holder(self.root, 'test'))  # known non-launch releases

    def test_missing_executable_in_runner_is_not_started(self):
        log, _ = self._background_diff()
        with patch.object(runner.shutil, 'which', return_value=None):
            with self.assertRaises(ValueError):
                runner.run_job(self.root, log)
        self.assertEqual(support.read_json(log / 'receipt.json')['state'], 'not_started')

    def test_popen_failure_is_not_started_and_releases(self):
        def boom(cmd, **kw):
            raise OSError('spawn denied')
        with patch.object(runner.shutil, 'which', return_value='pwsh'), patch.object(runner.subprocess, 'Popen', side_effect=boom):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=9, background=True), 1)
        receipt = support.read_json(next(support.receipt_root(self.root, self.task).glob('*/receipt.json')))
        self.assertEqual(receipt['state'], 'not_started')
        self.assertIn('spawn denied', receipt['diagnostic'])
        self.assertIsNone(runner.db_lock_holder(self.root, 'test'))

    def test_one_shot_claim_is_atomic_under_concurrency(self):
        # review P1: two runners released at the same instant -> exactly one platform call
        import threading
        log, _ = self._background_diff()
        calls, outcomes, gate = [], [], threading.Barrier(2)
        real_open = os.open
        def gated_open(path, flags, *a):
            if str(path).endswith('runner.claim'):
                gate.wait(timeout=5)
            return real_open(path, flags, *a)
        def worker():
            try:
                outcomes.append(runner.run_job(self.root, log))
            except ValueError:
                outcomes.append('refused')
        with patch.object(runner.os, 'open', side_effect=gated_open), \
             patch.object(runner.shutil, 'which', return_value='pwsh'), \
             patch.object(runner.subprocess, 'run', side_effect=self._platform(log, calls)):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            [t.start() for t in threads]; [t.join(10) for t in threads]
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(map(str, outcomes)), ['0', 'refused'])

    def test_concurrent_submissions_launch_at_most_one(self):
        # both submitters pass the advisory "database free" pre-check at the same instant,
        # then compete for ownership inside the mutex
        import threading
        results, gate, spawned = [], threading.Barrier(2), []
        real_holder = runner.db_lock_holder
        def gated_holder(root, db_id):
            value = real_holder(root, db_id)
            try:
                gate.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            return value
        def submit():
            try:
                results.append(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=9, background=True))
            except ValueError:
                results.append('busy')
        with patch.object(runner, 'db_lock_holder', side_effect=gated_holder), \
             patch.object(runner.shutil, 'which', return_value='pwsh'), \
             patch.object(runner.subprocess, 'Popen', side_effect=lambda cmd, **kw: spawned.append(cmd)):
            threads = [threading.Thread(target=submit) for _ in range(2)]
            [t.start() for t in threads]; [t.join(10) for t in threads]
        self.assertEqual(len(spawned), 1)
        self.assertEqual(sorted(map(str, results)), ['0', 'busy'])

    def test_expired_unresolved_run_keeps_database_locked(self):
        # review P1: deadline expiry is not proof of completion
        log, receipt = self._background_diff()
        receipt['deadline_utc'] = '2000-01-01T00:00:00+00:00'
        receipt['request_sha256'] = runner.request_digest(receipt)  # deadline is not a request field
        (log / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
        self.assertEqual(runner.wait_receipt(self.root, log / 'receipt.json', max_wait=0), runner.WAIT_STALE)
        with self.assertRaises(ValueError):
            runner.execute(self.root, 'test', 'status', '', '', dry_run=True)
        with self.assertRaises(ValueError):
            runner.execute(self.root, 'test', 'lock', self.objects, self.task, intent='prepare', dry_run=True)
        with self.assertRaises(ValueError):  # reconciliation needs a stated reason
            runner.reconcile(self.root, log / 'receipt.json', '')
        self.assertEqual(runner.reconcile(self.root, log / 'receipt.json', 'user confirmed: no 1cv8 Designer running'), 0)
        final = support.read_json(log / 'receipt.json')
        self.assertEqual((final['state'], final['outcome_known']), ('reconciled', False))
        self.assertEqual(len(final['reconciliations']), 1)
        self.assertEqual(runner.execute(self.root, 'test', 'status', '', '', dry_run=True), 0)
        with self.assertRaises(ValueError):  # a reconciled unverified diff is not commit evidence
            support.find_diff_receipt(self.root, self.task, self.profile('commit'), self.objects)

    def test_foreground_unknown_outcome_keeps_database_locked(self):
        def fake(args, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs.get('timeout'))
        with patch.object(runner.shutil, 'which', return_value='pwsh'), patch.object(runner.subprocess, 'run', side_effect=fake):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=3), 1)
        self.assertIsNotNone(runner.db_lock_holder(self.root, 'test'))
        with self.assertRaises(ValueError):
            runner.execute(self.root, 'test', 'status', '', '', dry_run=True)

    def test_wait_budget_holds_on_persistent_corrupt_receipt(self):
        # review P2: malformed JSON must not extend the wait beyond its budget
        log, _ = self._background_diff()
        (log / 'receipt.json').write_text('{broken', encoding='utf-8')
        reads = []
        real = runner.read_json
        def counting(p):
            reads.append(p)
            return real(p)
        with patch.object(runner, 'read_json', side_effect=counting):
            started = time.monotonic()
            self.assertEqual(runner.wait_receipt(self.root, log / 'receipt.json', max_wait=0), runner.WAIT_FAILED)
            self.assertEqual(len(reads), 1)
            self.assertEqual(runner.wait_receipt(self.root, log / 'receipt.json', max_wait=1, poll=0.2), runner.WAIT_FAILED)
            self.assertLess(time.monotonic() - started, 3)

    # --- review of 8f634af: ownership protocol races ------------------------------------------
    def test_reconcile_refused_while_runner_alive_before_launch(self):
        # pause the validated worker immediately before launch and try to reconcile
        log, _ = self._background_diff()
        calls, refused = [], []
        real_launch = runner._launch
        def paused_launch(*a, **kw):
            with self.assertRaises(ValueError) as ctx:
                runner.reconcile(self.root, log / 'receipt.json', 'user confirmed: no 1cv8 Designer running')
            refused.append(str(ctx.exception))
            return real_launch(*a, **kw)
        with patch.object(runner, '_launch', side_effect=paused_launch):
            self.assertEqual(self._run_job(log, calls), 0)
        self.assertIn('runner', refused[0])
        final = support.read_json(log / 'receipt.json')
        self.assertEqual(final['state'], 'succeeded')
        self.assertNotIn('reconciliations', final)
        self.assertEqual(len(calls), 1)

    def test_reconcile_refused_while_runner_alive_after_platform(self):
        # pause after platform completion, before the final receipt write
        log, _ = self._background_diff()
        calls, refused = [], []
        real_finalize = runner._finalize
        def paused_finalize(*a, **kw):
            with self.assertRaises(ValueError):
                runner.reconcile(self.root, log / 'receipt.json', 'user confirmed: no 1cv8 Designer running')
            refused.append(True)
            return real_finalize(*a, **kw)
        with patch.object(runner, '_finalize', side_effect=paused_finalize):
            self.assertEqual(self._run_job(log, calls), 0)
        self.assertEqual(refused, [True])
        self.assertEqual(support.read_json(log / 'receipt.json')['state'], 'succeeded')

    def test_reconciled_pending_run_never_launches(self):
        # runner not alive yet: reconcile wins, the late worker must not start the platform
        log, _ = self._background_diff()
        self.assertEqual(runner.reconcile(self.root, log / 'receipt.json', 'user confirmed: no runner and no 1cv8'), 0)
        calls = []
        with self.assertRaises(ValueError):
            self._run_job(log, calls)
        self.assertEqual(calls, [])
        final = support.read_json(log / 'receipt.json')
        self.assertEqual(final['state'], 'reconciled')
        self.assertEqual(len(final['reconciliations']), 1)
        with self.assertRaises(ValueError):
            support.find_diff_receipt(self.root, self.task, self.profile('commit'), self.objects)

    def test_late_finalize_after_reconcile_cannot_revive_or_release_new_owner(self):
        # fence check of _finalize itself: disk says reconciled, a new owner B holds the database
        log, receipt = self._background_diff()
        lock_path = runner._db_lock_path(self.root, 'test')
        token_a = receipt['lock_token']
        # simulate: A launched, then its runner died; user reconciled; B acquired
        disk = support.read_json(log / 'receipt.json'); disk['state'] = 'launched'
        (log / 'receipt.json').write_text(json.dumps(disk), encoding='utf-8')
        self.assertEqual(runner.reconcile(self.root, log / 'receipt.json', 'user confirmed: runner and 1cv8 gone'), 0)
        b_receipt = log.parent / 'b-run' / 'receipt.json'
        with runner._DbMutex(lock_path):
            token_b = runner._acquire_owner(lock_path, 'test', b_receipt, 'status', 'foreground')
        recorded = runner._finalize(lock_path, log / 'receipt.json', token_a, {'state': 'succeeded', 'runner_exit': 0}, True)
        self.assertEqual(recorded, 'reconciled')
        final = support.read_json(log / 'receipt.json')
        self.assertEqual(final['state'], 'reconciled')
        self.assertEqual(len(final['reconciliations']), 1)
        self.assertEqual(final['late_runner_result']['state'], 'succeeded')
        self.assertEqual(runner.db_lock_holder(self.root, 'test')['token'], token_b)
        with self.assertRaises(ValueError):
            support.find_diff_receipt(self.root, self.task, self.profile('commit'), self.objects)

    def test_delayed_release_cannot_delete_next_owner_lock(self):
        log, receipt = self._background_diff()
        lock_path = runner._db_lock_path(self.root, 'test')
        a_receipt, token_a = log / 'receipt.json', receipt['lock_token']
        with runner._DbMutex(lock_path):
            self.assertTrue(runner._release_owner(lock_path, a_receipt, token_a))
            token_b = runner._acquire_owner(lock_path, 'test', log.parent / 'b' / 'receipt.json', 'status', 'foreground')
        with runner._DbMutex(lock_path):  # A's delayed duplicate release
            self.assertFalse(runner._release_owner(lock_path, a_receipt, token_a))
        self.assertEqual(runner.db_lock_holder(self.root, 'test')['token'], token_b)

    def test_reconcile_interleaved_with_owner_change_keeps_new_owner(self):
        # reconcile lists A's lock, then A is released and B acquires before reconcile's critical section
        log, receipt = self._background_diff()
        lock_path = runner._db_lock_path(self.root, 'test')
        a_receipt, token_a = log / 'receipt.json', receipt['lock_token']
        real_list = runner.locks_held_by
        state = {}
        def interleaved(root, path):
            found = real_list(root, path)
            with runner._DbMutex(lock_path):
                runner._release_owner(lock_path, a_receipt, token_a)
                state['b'] = runner._acquire_owner(lock_path, 'test', log.parent / 'b' / 'receipt.json', 'status', 'foreground')
            return found
        with patch.object(runner, 'locks_held_by', side_effect=interleaved):
            with self.assertRaises(ValueError):
                runner.reconcile(self.root, a_receipt, 'user confirmed: no 1cv8 Designer running')
        self.assertEqual(runner.db_lock_holder(self.root, 'test')['token'], state['b'])
        self.assertNotIn('reconciliations', support.read_json(a_receipt))

    def test_delayed_worker_past_deadline_does_not_launch(self):
        log, receipt = self._background_diff()
        receipt['deadline_utc'] = '2000-01-01T00:00:00+00:00'  # not part of the request digest
        (log / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
        calls = []
        with self.assertRaises(ValueError):
            self._run_job(log, calls)
        self.assertEqual(calls, [])
        final = support.read_json(log / 'receipt.json')
        self.assertEqual(final['state'], 'not_started')
        self.assertIn('deadline', final['diagnostic'])
        self.assertIsNone(runner.db_lock_holder(self.root, 'test'))

    def test_foreground_killed_runner_reconcile_after_death(self):
        # foreground timeout: outcome unknown, runner finished -> lock kept, reconcile allowed
        def fake(args, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs.get('timeout'))
        with patch.object(runner.shutil, 'which', return_value='pwsh'), patch.object(runner.subprocess, 'run', side_effect=fake):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=3), 1)
        receipt = next(support.receipt_root(self.root, self.task).glob('*/receipt.json'))
        self.assertIsNotNone(runner.db_lock_holder(self.root, 'test'))
        self.assertEqual(runner.reconcile(self.root, receipt, 'user confirmed: no 1cv8 Designer running'), 0)
        self.assertIsNone(runner.db_lock_holder(self.root, 'test'))

    def test_wait_rejects_foreign_paths(self):
        outside = self.root / 'receipt.json'
        outside.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            runner.wait_receipt(self.root, outside, max_wait=0)
        with self.assertRaises(ValueError):
            runner.reconcile(self.root, outside, 'user confirmed nothing runs')

    def _lock_receipt_file(self, lock_kind, refresh_of=None):
        path = self.root / 'pilot-control' / self.task / 'repository' / 'probe' / 'receipt.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'lock_kind': lock_kind, 'refresh_of': refresh_of}), encoding='utf-8')
        return path

    CLEAN_REFRESH = {'platform_exit': 0, 'effective_exit': 0, 'dump_result': 0, 'lock_done': True,
                     'unrecognized_count': 0, 'locked': [], 'locked_by_other': [], 'received': [], 'timed_out': False}

    def test_lock_outcome_refresh_requires_every_signal(self):
        receipt = self._lock_receipt_file('refresh', 'pilot-control/TASK-TEST/repository/0001/receipt.json')
        self.assertEqual(runner._lock_outcome(receipt, self.objects, dict(self.CLEAN_REFRESH))[0], 'succeeded')
        for field, bad in (('platform_exit', 1), ('effective_exit', 1), ('dump_result', None), ('dump_result', 2),
                           ('lock_done', False), ('unrecognized_count', 1), ('locked_by_other', [{'Name': 'x'}]),
                           ('received', ['ОбщийМодуль.Example']), ('timed_out', True)):
            with self.subTest(field=field, bad=bad):
                result = dict(self.CLEAN_REFRESH, **{field: bad})
                self.assertEqual(runner._lock_outcome(receipt, self.objects, result)[0], 'unverified')

    def test_lock_outcome_without_refresh_context_never_trusts_completion_line(self):
        receipt = self._lock_receipt_file('acquire')
        self.assertEqual(runner._lock_outcome(receipt, self.objects, dict(self.CLEAN_REFRESH))[0], 'unverified')

    def test_lock_outcome_partial_per_object_lines(self):
        objects = ['ОбщийМодуль.Example', 'Документ.X']
        acquire = self._lock_receipt_file('acquire')
        partial = dict(self.CLEAN_REFRESH, locked=['ОбщийМодуль.Example'], lock_done=False)
        self.assertEqual(runner._lock_outcome(acquire, objects, partial)[0], 'unverified')
        full = dict(partial, locked=['общиймодуль.example', 'Документ.X'])  # platform casing tolerated
        self.assertEqual(runner._lock_outcome(acquire, objects, full)[0], 'succeeded')

    def test_refresh_must_bind_to_latest_confirmed_prepare(self):
        self.receipt()  # confirmed prepare 0001
        with self.assertRaises(ValueError):
            runner.execute(self.root, 'test', 'lock', self.objects, self.task, intent='prepare', dry_run=True,
                           refresh_of=self.root / 'pilot-control/TASK-TEST/repository/9999/receipt.json')
        with self.assertRaises(ValueError):  # other objects are not covered by that prepare
            runner.execute(self.root, 'test', 'lock', ['Документ.Other'], self.task, intent='prepare', dry_run=True,
                           refresh_of=support.receipt_root(self.root, self.task) / '0001' / 'receipt.json')
        prior = support.find_lock_receipt(self.root, self.task, self.profile(), self.objects)['_receipt_path']
        self.assertEqual(runner.execute(self.root, 'test', 'lock', self.objects, self.task, intent='prepare',
                                        dry_run=True, refresh_of=prior), 0)

    def test_refresh_after_profile_change_is_refused(self):
        self.receipt()
        prior = support.find_lock_receipt(self.root, self.task, self.profile(), self.objects)['_receipt_path']
        self.db['repository']['path'] = 'tcp://fake.invalid/other'; self.save()
        with self.assertRaises(ValueError):
            runner.execute(self.root, 'test', 'lock', self.objects, self.task, intent='prepare', dry_run=True, refresh_of=prior)

    # --- review of cc14f09 -------------------------------------------------------------------
    def test_lock_outcome_explicit_negative_beats_completion_line(self):
        receipt = self._lock_receipt_file('refresh', 'pilot-control/TASK-TEST/repository/0001/receipt.json')
        for field in ('not_locked', 'unlocked'):
            with self.subTest(field=field):
                result = dict(self.CLEAN_REFRESH, **{field: ['общиймодуль.example']})
                state, note = runner._lock_outcome(receipt, self.objects, result)
                self.assertEqual(state, 'unverified')
                self.assertIn('not locked', note)
        acquire = self._lock_receipt_file('acquire')  # even with a per-object capture line
        both = dict(self.CLEAN_REFRESH, locked=self.objects, not_locked=self.objects)
        self.assertEqual(runner._lock_outcome(acquire, self.objects, both)[0], 'unverified')

    def _write_receipt(self, key, **fields):
        path = support.receipt_root(self.root, self.task) / key / 'receipt.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {'operation': 'lock', 'state': 'succeeded', 'objects': self.objects,
                 'profile_hash': support.fingerprint(self.profile())}
        value.update(fields)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return str(path)

    def test_basis_requires_confirmed_capture_of_every_object(self):
        # old succeeded receipt with empty acquired_now is not a basis
        self._write_receipt('0001', acquired_now=[])
        with self.assertRaises(ValueError):
            support.confirmed_lock_basis(self.root, self.task, self.profile(), self.objects)
        # pilot-compatible old receipt: object explicitly in acquired_now, no lock_kind at all
        origin = self._write_receipt('0002', acquired_now=self.objects)
        self.assertEqual(support.confirmed_lock_basis(self.root, self.task, self.profile(), self.objects), origin)

    def test_basis_follows_refresh_chain_to_original_capture(self):
        origin = self._write_receipt('0001', acquired_now=self.objects)
        r1 = self._write_receipt('0002', acquired_now=[], lock_kind='refresh', refresh_of=origin,
                                 lock_assessment='refresh_confirmed of x')
        r2 = self._write_receipt('0003', acquired_now=[], lock_kind='refresh', refresh_of=r1,
                                 lock_assessment='refresh_confirmed of y')
        self.assertEqual(support.confirmed_lock_basis(self.root, self.task, self.profile(), self.objects), r2)

    def test_basis_rejects_broken_or_unconfirmed_chains(self):
        empty = self._write_receipt('0001', acquired_now=[])
        self._write_receipt('0002', acquired_now=[], lock_kind='refresh', refresh_of=empty,
                            lock_assessment='refresh_confirmed of x')
        with self.assertRaises(ValueError):  # chain ends at a receipt without a capture
            support.confirmed_lock_basis(self.root, self.task, self.profile(), self.objects)
        self._write_receipt('0003', acquired_now=[], lock_kind='refresh', refresh_of=str(self.root / 'nowhere/receipt.json'),
                            lock_assessment='refresh_confirmed of x')
        with self.assertRaises(ValueError):  # dangling link
            support.confirmed_lock_basis(self.root, self.task, self.profile(), self.objects)
        origin = self._write_receipt('0004', acquired_now=self.objects)
        self._write_receipt('0005', acquired_now=[], lock_kind='refresh', refresh_of=origin)  # no refresh_confirmed
        with self.assertRaises(ValueError):
            support.confirmed_lock_basis(self.root, self.task, self.profile(), self.objects)

    def test_basis_broken_by_release_after_capture(self):
        origin = self._write_receipt('0001', acquired_now=self.objects)
        self._write_receipt('0002', operation='unlock', objects=self.objects, result={'unlocked': self.objects})
        self._write_receipt('0003', acquired_now=[], lock_kind='refresh', refresh_of=origin,
                            lock_assessment='refresh_confirmed of x')
        with self.assertRaises(ValueError):
            support.confirmed_lock_basis(self.root, self.task, self.profile(), self.objects)

    def test_basis_rechecked_under_mutex_when_reserving(self):
        # unlock finishes after the first basis check but before the database mutex
        origin = self._write_receipt('0001', acquired_now=self.objects)
        real = runner.confirmed_lock_basis
        calls = []
        def racing(root, task, db, objects):
            calls.append(1)                      # counted before: the re-check raises
            value = real(root, task, db, objects)
            if len(calls) == 1:
                self._write_receipt('0002', operation='unlock', objects=self.objects, result={'unlocked': self.objects})
            return value
        platform = []
        with patch.object(runner, 'confirmed_lock_basis', side_effect=racing), \
             patch.object(runner.shutil, 'which', return_value='pwsh'), \
             patch.object(runner.subprocess, 'run', side_effect=lambda a, **k: platform.append(a)):
            with self.assertRaises(ValueError):
                runner.execute(self.root, 'test', 'lock', self.objects, self.task, intent='prepare', refresh_of=origin)
        self.assertEqual(len(calls), 2)          # second check happened under the mutex
        self.assertEqual(platform, [])           # nothing launched
        self.assertIsNone(runner.db_lock_holder(self.root, 'test'))  # no reservation left behind

    def test_basis_invalidated_by_later_denial_in_unverified_receipt(self):
        origin = self._write_receipt('0001', acquired_now=self.objects)
        for key, result in (('0002', {'not_locked': self.objects}),
                            ('0002', {'locked_by_other': [{'Name': self.objects[0], 'Holder': 'OtherUser'}]}),
                            ('0002', {'received': self.objects})):
            with self.subTest(result=result):
                self._write_receipt(key, state='unverified', acquired_now=[], lock_kind='refresh',
                                    refresh_of=origin, result=result)
                self._write_receipt('0003', acquired_now=[], lock_kind='refresh', refresh_of=origin,
                                    lock_assessment='refresh_confirmed of x')
                with self.assertRaises(ValueError):
                    support.confirmed_lock_basis(self.root, self.task, self.profile(), self.objects)

    def test_contradictory_capture_not_a_release_basis(self):
        self._write_receipt('0001', acquired_now=self.objects, result={'locked': self.objects, 'not_locked': self.objects})
        with self.assertRaises(ValueError):
            support.find_lock_receipt(self.root, self.task, self.profile(), self.objects, acquired_only=True)

    def test_scope_mapping_and_release_of_old_locks_are_refused(self):
        self.receipt(acquired=[])
        with self.assertRaises(ValueError): runner.execute(self.root, 'test', 'unlock', self.objects, self.task, intent='release', dry_run=True)
        self.assertEqual(support.required_objects('projects/pet/src', ['projects/pet/src/Documents/X/Forms/Main/Ext/Form/Module.bsl']), ['Документ.X.Форма.Main'])
        with self.assertRaises(ValueError): support.required_objects('projects/pet/src', ['other/Module.bsl'])



class PowerShellRepositoryTests(unittest.TestCase):
    """Real runner/PowerShell, fake executable only. Never connects to a real IB."""
    save = RepositoryTests.save
    profile = RepositoryTests.profile
    receipt = RepositoryTests.receipt

    @classmethod
    def setUpClass(cls):
        import shutil
        cls.pwsh = shutil.which('pwsh') or shutil.which('powershell')
        if not cls.pwsh:
            raise unittest.SkipTest('PowerShell unavailable; pure guard fixtures still run')

    def setUp(self):
        RepositoryTests.setUp(self)
        import shutil
        scripts = Path(__file__).resolve().parent
        # Installed tests find the same resource via any supported adapter.
        from safe_apply import find_skills_dir
        source = scripts.parent / 'skills/1c-repository-manage/scripts/repo-ops.ps1'
        if not source.exists():
            source = find_skills_dir(scripts.parent) / '1c-repository-manage/scripts/repo-ops.ps1'
        self.assertTrue(source.is_file(), source)
        shutil.copyfile(source, self.root / '.agents/skills/1c-repository-manage/scripts/repo-ops.ps1')
        base = self.root / 'fake-db'; base.mkdir(); (base / '1Cv8.1CD').touch()
        self.fake = self.root / 'fake-1cv8.exe'
        if os.name == 'nt':
            compiler = shutil.which('powershell')
            if not compiler:
                self.fail('Windows PowerShell 5.1 required for the offline fixture compiler')
            cs = self.root / 'Fake.cs'
            cs.write_text(CSHARP_FAKE, encoding='utf-8-sig')
            compile_ps = self.root / 'compile.ps1'
            compile_ps.write_text('param($Source,$Output)\n$ErrorActionPreference="Stop"\nAdd-Type -TypeDefinition ([IO.File]::ReadAllText($Source)) -ReferencedAssemblies System.dll,System.Xml.dll -OutputAssembly $Output -OutputType ConsoleApplication\n', encoding='utf-8-sig')
            compile_env = dict(os.environ)
            compile_env.pop('PSModulePath', None)
            result = subprocess.run([compiler, '-NoProfile', '-File', str(compile_ps), str(cs), str(self.fake)], env=compile_env, capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            code = '#!' + sys.executable + '\n' + PYTHON_FAKE
            self.fake.write_text(code, encoding='utf-8'); self.fake.chmod(0o755)
        cfg = support.read_json(self.root / '.v8-project.json')
        cfg['v8path'] = str(self.fake)
        (self.root / '.v8-project.json').write_text(json.dumps(cfg), encoding='utf-8')

    def test_real_ps_lock_commit_comment_and_sanitized_evidence(self):
        with patch.dict(os.environ, {'REPO_FAKE_MODE': 'ok', 'REPO_FAKE_SECRET': 'fixture-secret'}):
            self.assertEqual(runner.execute(self.root, 'test', 'lock', self.objects, self.task, intent='prepare'), 0)
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=7), 0)
            self.assertEqual(runner.execute(self.root, 'test', 'commit', self.objects, self.task,
                                           explicit_comment='Тест "кавычек"\nвторая строка', intent='publish', existing_change=True), 0)
        receipts = list(support.receipt_root(self.root, self.task).glob('*/receipt.json'))
        self.assertEqual(len(receipts), 3)
        for path in support.receipt_root(self.root, self.task).rglob('*'):
            if path.is_file():
                self.assertNotIn('fixture-secret', path.read_text(encoding='utf-8-sig'))
        final = support.read_json(sorted(receipts)[-1])
        self.assertEqual(final['state'], 'succeeded')
        self.assertEqual(final['result']['committed'], self.objects)
        with self.assertRaises(ValueError): support.find_lock_receipt(self.root, self.task, self.profile(), self.objects)

    def test_real_ps_background_diff_detaches_and_completes(self):
        with patch.dict(os.environ, {'REPO_FAKE_MODE': 'ok'}):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=7, background=True), 0)
            receipt = next(support.receipt_root(self.root, self.task).glob('*/receipt.json'))
            code = 3
            for _ in range(12):
                code = runner.wait_receipt(self.root, receipt, max_wait=10, poll=1)
                if code != 3:
                    break
        self.assertEqual(code, 0)
        final = support.read_json(receipt)
        self.assertEqual(final['state'], 'succeeded')
        self.assertEqual(final['mode'], 'background')

    # --- lock refresh before safe_apply (incident 2026-11-27, anonymized fixtures) -----------
    PLAN_FILE = 'projects/pet/src/CommonModules/Example/Ext/Module.bsl'

    def _plan(self, plan_file=None):
        plan_file = plan_file or self.PLAN_FILE
        module = self.root / plan_file
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text('// fixture', encoding='utf-8')
        report = self.root / 'specs' / self.task / '06_change_report.md'
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text('## Изменённые файлы\n- ' + plan_file + '\n', encoding='utf-8')

    def _prepare(self, mode='ok'):
        with patch.dict(os.environ, {'REPO_FAKE_MODE': mode}):
            return runner.execute(self.root, 'test', 'lock', self.objects, self.task, intent='prepare')

    def _safe_apply(self, mode, plan_file=None):
        import safe_apply
        self._plan(plan_file)
        loaded = []
        argv = ['safe_apply.py', '--task', self.task, '--db', 'test', '--project-root', str(self.root)]
        with patch.dict(os.environ, {'REPO_FAKE_MODE': mode}), patch.object(sys, 'argv', argv), \
             patch.object(safe_apply, 'run_guard', return_value=0), \
             patch.object(safe_apply, 'run_skill', side_effect=lambda name, *a: loaded.append(name) or 0), \
             patch.object(support, 'record_apply', return_value=None):
            code = safe_apply.main()
        return code, loaded

    def _latest(self):
        return support.read_json(sorted(support.receipt_root(self.root, self.task).glob('*/receipt.json'))[-1])

    def test_real_ps_confirmed_refresh_reaches_load_without_new_acquisition(self):
        self.assertEqual(self._prepare('ok'), 0)
        first = self._latest()
        self.assertEqual(first['acquired_now'], self.objects)
        code, loaded = self._safe_apply('refresh')
        self.assertEqual(code, 0)
        self.assertEqual(loaded, ['db-load-xml', 'db-update'])
        refresh = self._latest()
        self.assertEqual((refresh['state'], refresh['lock_kind']), ('succeeded', 'refresh'))
        self.assertTrue(refresh['refresh_of'].endswith('receipt.json'))
        self.assertIn('refresh_confirmed', refresh['lock_assessment'])
        self.assertEqual(refresh['acquired_now'], [])  # confirmation, not a new capture
        self.assertEqual(refresh['result']['dump_result'], 0)
        self.assertTrue(refresh['result']['lock_done'])
        # release authority still comes from the original prepare only
        owner = support.find_lock_receipt(self.root, self.task, self.profile(), self.objects, acquired_only=True)
        self.assertEqual(owner['_receipt_path'], refresh['refresh_of'])
        self.assertNotIn('Already locked by you', json.dumps(refresh, ensure_ascii=False))

    def test_real_ps_same_answer_without_confirmed_prepare_is_unverified(self):
        # first prepare itself answers only with the completion line: nothing is confirmed
        self.assertEqual(self._prepare('refresh'), 1)
        first = self._latest()
        self.assertEqual(first['state'], 'unverified')
        self.assertEqual(first['acquired_now'], [])
        self.assertIn('no confirmed prepare', first['lock_assessment'])
        code, loaded = self._safe_apply('refresh')
        self.assertEqual(code, 1)
        self.assertEqual(loaded, [])

    def test_real_ps_negative_refresh_answers_stop_before_load(self):
        cases = {'empty': 'unverified', 'other': 'unverified', 'received': 'rebase_required',
                 'refresh_nodump': 'unverified', 'refresh_extra': 'unverified',
                 'refresh_notlocked': 'unverified'}
        for mode, expected in cases.items():
            with self.subTest(mode=mode):
                self.tearDown_receipts()
                self.assertEqual(self._prepare('ok'), 0)
                code, loaded = self._safe_apply(mode)
                self.assertEqual(code, 1)
                self.assertEqual(loaded, [], 'safe_apply must not reach db-load-xml')
                latest = self._latest()
                self.assertEqual(latest['state'], expected)
                self.assertEqual(latest['acquired_now'], [])

    def test_real_ps_old_empty_acquired_receipt_cannot_basis_refresh(self):
        self.receipt(acquired=[])  # succeeded lock receipt without a confirmed capture
        code, loaded = self._safe_apply('refresh')
        self.assertEqual(code, 1)
        self.assertEqual(loaded, [])

    def test_real_ps_second_refresh_follows_chain(self):
        self.assertEqual(self._prepare('ok'), 0)
        self.assertEqual(self._safe_apply('refresh'), (0, ['db-load-xml', 'db-update']))
        code, loaded = self._safe_apply('refresh')
        self.assertEqual((code, loaded), (0, ['db-load-xml', 'db-update']))
        latest = self._latest()
        self.assertEqual((latest['lock_kind'], latest['acquired_now']), ('refresh', []))

    def test_real_ps_refresh_after_unlock_is_refused(self):
        self.assertEqual(self._prepare('ok'), 0)
        with patch.dict(os.environ, {'REPO_FAKE_MODE': 'ok'}):
            self.assertEqual(runner.execute(self.root, 'test', 'unlock', self.objects, self.task, intent='release'), 0)
        code, loaded = self._safe_apply('refresh')
        self.assertEqual((code, loaded), (1, []))

    def test_real_ps_unlock_race_before_mutex_blocks_load(self):
        self.assertEqual(self._prepare('ok'), 0)
        real = runner.confirmed_lock_basis
        state = {'n': 0}
        def racing(root, task, db, objects):
            state['n'] += 1                      # counted before: the re-check raises
            value = real(root, task, db, objects)
            if state['n'] == 2:  # execute's first check passed; unlock lands before the mutex
                with patch.dict(os.environ, {'REPO_FAKE_MODE': 'ok'}):
                    self.assertEqual(runner.execute(self.root, 'test', 'unlock', self.objects, self.task, intent='release'), 0)
            return value
        with patch.object(runner, 'confirmed_lock_basis', side_effect=racing):
            code, loaded = self._safe_apply('refresh')
        self.assertEqual((code, loaded), (1, []))
        self.assertGreaterEqual(state['n'], 3)

    # --- review of 9bfc011: negative answers invalidate the prepare for that object -----------
    def test_real_ps_notlocked_then_success_line_does_not_revive_prepare(self):
        self.assertEqual(self._prepare('ok'), 0)
        self.assertEqual(self._safe_apply('refresh_notlocked'), (1, []))
        self.assertEqual(self._safe_apply('refresh'), (1, []))  # old prepare lost its force
        with self.assertRaises(ValueError):
            support.confirmed_lock_basis(self.root, self.task, self.profile(), self.objects)

    def test_real_ps_foreign_lock_then_success_line_does_not_revive_prepare(self):
        self.assertEqual(self._prepare('ok'), 0)
        self.assertEqual(self._safe_apply('other'), (1, []))
        self.assertEqual(self._safe_apply('refresh'), (1, []))

    def test_real_ps_received_version_keeps_rebase_requirement(self):
        self.assertEqual(self._prepare('ok'), 0)
        self.assertEqual(self._safe_apply('received'), (1, []))
        self.assertEqual(self._latest()['state'], 'rebase_required')
        self.assertEqual(self._safe_apply('refresh'), (1, []))
        with self.assertRaises(ValueError) as ctx:
            support.confirmed_lock_basis(self.root, self.task, self.profile(), self.objects)
        self.assertIn('reconcile the sources', str(ctx.exception))

    def test_real_ps_contradictory_capture_excluded_others_kept(self):
        self.objects = ['ОбщийМодуль.Example', 'Документ.X']
        self.assertEqual(self._prepare('contradict'), 1)  # A contradicts itself -> unverified overall
        receipt = self._latest()
        self.assertEqual(receipt['acquired_now'], ['Документ.X'])  # B's confirmed capture kept
        with self.assertRaises(ValueError):  # A is not a release basis
            runner.execute(self.root, 'test', 'unlock', ['ОбщийМодуль.Example'], self.task, intent='release', dry_run=True)
        self.assertEqual(runner.execute(self.root, 'test', 'unlock', ['Документ.X'], self.task, intent='release', dry_run=True), 0)
        with self.assertRaises(ValueError):  # A cannot be refreshed from this receipt
            support.confirmed_lock_basis(self.root, self.task, self.profile(), ['ОбщийМодуль.Example'])

    def test_real_ps_pilot_form_prepare_then_success_line_reaches_load(self):
        # pilot receipt shape: first prepare captured the form; next answer = exact one-line refresh
        plan = 'projects/pet/src/Documents/X/Forms/ФормаДокумента/Ext/Form/Module.bsl'
        self.objects = ['Документ.X.Форма.ФормаДокумента']
        self.assertEqual(self._prepare('ok'), 0)
        self.assertEqual(self._latest()['acquired_now'], self.objects)
        self.assertEqual(self._safe_apply('refresh', plan), (0, ['db-load-xml', 'db-update']))
        latest = self._latest()
        self.assertEqual((latest['state'], latest['lock_kind'], latest['acquired_now']), ('succeeded', 'refresh', []))
        log = (Path(latest['refresh_of']).parent.parent / Path(self._latest_path()).parent.name / 'platform.log')
        self.assertEqual(log.read_text(encoding='utf-8-sig').strip(), 'Захват объектов в хранилище успешно завершен')

    def _latest_path(self):
        return str(sorted(support.receipt_root(self.root, self.task).glob('*/receipt.json'))[-1])

    def tearDown_receipts(self):
        import shutil
        shutil.rmtree(self.root / 'pilot-control', ignore_errors=True)
        lock_dir = self.root / 'logs' / 'repository-locks'
        self.assertFalse(list(lock_dir.glob('*.lock')) if lock_dir.exists() else [], 'previous case left a database lock')

    def test_real_ps_partial_lock_same_names_different_types(self):
        self.objects = ['Справочник.X', 'Документ.X']
        with patch.dict(os.environ, {'REPO_FAKE_MODE': 'partial'}):
            self.assertEqual(runner.execute(self.root, 'test', 'lock', self.objects, self.task, intent='prepare'), 1)
        receipt = support.read_json(next(support.receipt_root(self.root, self.task).glob('*/receipt.json')))
        self.assertEqual(receipt['acquired_now'], ['Справочник.X'])
        self.assertTrue(receipt['result']['locked_by_other'])
        with patch.dict(os.environ, {'REPO_FAKE_MODE': 'ok'}):
            self.assertEqual(runner.execute(self.root, 'test', 'unlock', self.objects[:1], self.task, intent='release'), 0)
        with self.assertRaises(ValueError): runner.execute(self.root, 'test', 'unlock', self.objects[1:], self.task, intent='release')

    def test_real_ps_unchanged_object_has_a_distinct_no_changes_state(self):
        self.receipt()
        with patch.dict(os.environ, {'REPO_FAKE_MODE': 'nochanges'}):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=7), 0)
            self.assertEqual(runner.execute(self.root, 'test', 'commit', self.objects, self.task,
                                           summary='fixture', intent='publish', existing_change=True), 0)
        latest = support.read_json(sorted(support.receipt_root(self.root, self.task).glob('*/receipt.json'))[-1])
        self.assertEqual(latest['state'], 'no_changes')
        self.assertEqual(latest['result']['committed'], [])

    def test_real_ps_commit_preserves_preexisting_own_captures(self):
        self.receipt(acquired=[])
        capture = self.root / 'fake-args.txt'
        with patch.dict(os.environ, {'REPO_FAKE_MODE': 'ok', 'REPO_FAKE_ARGS': str(capture)}):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=7), 0)
            self.assertEqual(runner.execute(self.root, 'test', 'commit', self.objects, self.task,
                                           summary='fixture', intent='publish', existing_change=True), 0)
        latest = support.read_json(sorted(support.receipt_root(self.root, self.task).glob('*/receipt.json'))[-1])
        self.assertTrue(latest['keep_locked_requested'])
        self.assertIn('-keepLocked', capture.read_text(encoding='utf-8-sig'))
        with self.assertRaises(ValueError): runner.execute(self.root, 'test', 'unlock', self.objects, self.task, intent='release')

    def test_real_ps_web_addresses_reach_platform_unchanged(self):
        capture = self.root / 'fake-args.txt'
        for address in ('http://example.invalid/repo/main.1ccr/dev',
                        'https://example.invalid:8443/repo/main.1ccr/dev%20team',
                        'HTTP://example.invalid/repo/main.1ccr/dev/',
                        'https://example.invalid/repo/main.1ccr/dev/'):
            cfg = support.read_json(self.root / '.v8-project.json')
            cfg['databases'][0]['repository']['path'] = address
            (self.root / '.v8-project.json').write_text(json.dumps(cfg), encoding='utf-8')
            with patch.dict(os.environ, {'REPO_FAKE_MODE': 'ok', 'REPO_FAKE_ARGS': str(capture)}):
                self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=7), 0)
            args = capture.read_text(encoding='utf-8-sig').splitlines()
            self.assertEqual(args[args.index('/ConfigurationRepositoryF') + 1], address)
            self.assertEqual(args[args.index('-SecondVersion') + 1], '7')

    def test_real_ps_silent_commit_is_not_claimed_as_success(self):
        self.receipt()
        with patch.dict(os.environ, {'REPO_FAKE_MODE': 'silent'}):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task, version=7), 0)
            self.assertEqual(runner.execute(self.root, 'test', 'commit', self.objects, self.task,
                                           summary='fixture', intent='publish', existing_change=True), 1)
        latest = support.read_json(sorted(support.receipt_root(self.root, self.task).glob('*/receipt.json'))[-1])
        self.assertEqual(latest['state'], 'unverified')


PYTHON_FAKE = r'''
import os, sys, pathlib, xml.etree.ElementTree as ET
args = sys.argv[1:]
if os.environ.get('REPO_FAKE_ARGS'): pathlib.Path(os.environ['REPO_FAKE_ARGS']).write_text('\n'.join(args), encoding='utf-8')
def val(key):
    return args[args.index(key)+1] if key in args else None
names = []
if val('-Objects'):
    names = [n.attrib['fullName'] for n in ET.parse(val('-Objects')).getroot() if 'fullName' in n.attrib]
mode = os.environ.get('REPO_FAKE_MODE', 'ok')
# Lock answers without per-object lines (incident 2026-11-27, anonymized): the platform wrote only
# its completion line. Variants: empty /Out, foreign lock, newer object version, unknown extra line.
LOCK_MODES = {
    'refresh': lambda names: ['Захват объектов в хранилище успешно завершен'],
    'refresh_nodump': lambda names: ['Захват объектов в хранилище успешно завершен'],
    'empty': lambda names: [],
    'other': lambda names: ['Объект захвачен для редактирования другим пользователем: '+n+' (OtherUser)' for n in names],
    'received': lambda names: ['Объект получен из хранилища: '+n for n in names] + ['Захват объектов в хранилище успешно завершен'],
    'refresh_extra': lambda names: ['Захват объектов в хранилище успешно завершен', 'Непредвиденная строка платформы'],
    'refresh_notlocked': lambda names: ['Объект не захвачен для редактирования: '+n for n in names] + ['Захват объектов в хранилище успешно завершен'],
    # first object: capture AND denial in the same answer; the others: clean capture
    'contradict': lambda names: ['Объект захвачен для редактирования: '+n for n in names] + ['Объект не захвачен для редактирования: '+names[0]],
}
log = ['----- Начало операции с хранилищем -----']
DONE = 'Захват объектов в хранилище успешно завершен'
if '/ConfigurationRepositoryLock' in args and mode in LOCK_MODES:
    log = LOCK_MODES[mode](names)
elif '/ConfigurationRepositoryLock' in args:
    for i,n in enumerate(names):
        log.append(('Объект захвачен для редактирования другим пользователем: '+n+' (OtherUser)') if mode == 'partial' and i else 'Объект захвачен для редактирования: '+n)
if '/ConfigurationRepositoryCommit' in args and mode != 'silent':
    log += [('Объект не был изменен: ' if mode == 'nochanges' else 'Объект помещен в хранилище: ')+n for n in names]
if '/ConfigurationRepositoryUnLock' in args:
    log += ['Захват объекта отменен: '+n for n in names]
if os.environ.get('REPO_FAKE_SECRET'):
    log += ['diagnostic '+os.environ['REPO_FAKE_SECRET']]
if val('/Out'): pathlib.Path(val('/Out')).write_text('\n'.join(log),encoding='utf-8-sig')
if val('/DumpResult') and mode != 'refresh_nodump': pathlib.Path(val('/DumpResult')).write_text('0',encoding='utf-8')
report = val('/ConfigurationRepositoryReport') or val('-ReportFile')
if report: pathlib.Path(report).write_text('Fixture report',encoding='utf-8')
'''

CSHARP_FAKE = r'''
using System;
using System.IO;
using System.Text;
using System.Xml;
using System.Collections.Generic;
class Fake {
 static string V(string[] a,string key) { int i=Array.IndexOf(a,key);return i>=0&&i+1<a.Length?a[i+1]:null; }
 static int Main(string[] a) {
  string capture=Environment.GetEnvironmentVariable("REPO_FAKE_ARGS");if(capture!=null)File.WriteAllLines(capture,a);
  var names=new List<string>(); string x=V(a,"-Objects");
  if(x!=null) { var d=new XmlDocument();d.Load(x);foreach(XmlNode n in d.SelectNodes("//*[local-name()='Object']"))names.Add(n.Attributes["fullName"].Value); }
  string mode=Environment.GetEnvironmentVariable("REPO_FAKE_MODE");
  var lines=new List<string>();lines.Add("----- Начало операции с хранилищем -----");
  string done="Захват объектов в хранилище успешно завершен";
  bool isLock=Array.IndexOf(a,"/ConfigurationRepositoryLock")>=0;
  if(isLock&&(mode=="refresh"||mode=="refresh_nodump")){lines.Clear();lines.Add(done);}
  else if(isLock&&mode=="empty"){lines.Clear();}
  else if(isLock&&mode=="other"){lines.Clear();foreach(string n in names)lines.Add("Объект захвачен для редактирования другим пользователем: "+n+" (OtherUser)");}
  else if(isLock&&mode=="received"){lines.Clear();foreach(string n in names)lines.Add("Объект получен из хранилища: "+n);lines.Add(done);}
  else if(isLock&&mode=="refresh_extra"){lines.Clear();lines.Add(done);lines.Add("Непредвиденная строка платформы");}
  else if(isLock&&mode=="contradict"){lines.Clear();foreach(string n in names)lines.Add("Объект захвачен для редактирования: "+n);if(names.Count>0)lines.Add("Объект не захвачен для редактирования: "+names[0]);}
  else if(isLock&&mode=="refresh_notlocked"){lines.Clear();foreach(string n in names)lines.Add("Объект не захвачен для редактирования: "+n);lines.Add(done);}
  else if(isLock)for(int i=0;i<names.Count;i++)lines.Add(mode=="partial"&&i>0?"Объект захвачен для редактирования другим пользователем: "+names[i]+" (OtherUser)":"Объект захвачен для редактирования: "+names[i]);
  if(Array.IndexOf(a,"/ConfigurationRepositoryCommit")>=0&&mode!="silent")foreach(string n in names)lines.Add((mode=="nochanges"?"Объект не был изменен: ":"Объект помещен в хранилище: ")+n);
  if(Array.IndexOf(a,"/ConfigurationRepositoryUnLock")>=0)foreach(string n in names)lines.Add("Захват объекта отменен: "+n);
  string secret=Environment.GetEnvironmentVariable("REPO_FAKE_SECRET");if(secret!=null)lines.Add("diagnostic "+secret);
  if(V(a,"/Out")!=null)File.WriteAllText(V(a,"/Out"),String.Join("\n",lines),new UTF8Encoding(true));
  if(V(a,"/DumpResult")!=null&&mode!="refresh_nodump")File.WriteAllText(V(a,"/DumpResult"),"0");
  string report=V(a,"/ConfigurationRepositoryReport")??V(a,"-ReportFile");if(report!=null)File.WriteAllText(report,"Fixture report");
  return 0;
 }
}
'''

if __name__ == '__main__':
    unittest.main(verbosity=2)
