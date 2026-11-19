#!/usr/bin/env python3
"""Repository pilot regressions. No real 1C or repository operations."""
import json
import os
import subprocess
import sys
import tempfile
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
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task), 0)
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
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task), 0)
            self.assertEqual(runner.execute(self.root, 'test', 'commit', self.objects, self.task,
                                           summary='fixture', intent='publish', existing_change=True), 0)
        latest = support.read_json(sorted(support.receipt_root(self.root, self.task).glob('*/receipt.json'))[-1])
        self.assertEqual(latest['state'], 'no_changes')
        self.assertEqual(latest['result']['committed'], [])

    def test_real_ps_commit_preserves_preexisting_own_captures(self):
        self.receipt(acquired=[])
        capture = self.root / 'fake-args.txt'
        with patch.dict(os.environ, {'REPO_FAKE_MODE': 'ok', 'REPO_FAKE_ARGS': str(capture)}):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task), 0)
            self.assertEqual(runner.execute(self.root, 'test', 'commit', self.objects, self.task,
                                           summary='fixture', intent='publish', existing_change=True), 0)
        latest = support.read_json(sorted(support.receipt_root(self.root, self.task).glob('*/receipt.json'))[-1])
        self.assertTrue(latest['keep_locked_requested'])
        self.assertIn('-keepLocked', capture.read_text(encoding='utf-8-sig'))
        with self.assertRaises(ValueError): runner.execute(self.root, 'test', 'unlock', self.objects, self.task, intent='release')

    def test_real_ps_silent_commit_is_not_claimed_as_success(self):
        self.receipt()
        with patch.dict(os.environ, {'REPO_FAKE_MODE': 'silent'}):
            self.assertEqual(runner.execute(self.root, 'test', 'diff', self.objects, self.task), 0)
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
log = ['----- Начало операции с хранилищем -----']
if '/ConfigurationRepositoryLock' in args:
    for i,n in enumerate(names):
        log.append(('Объект захвачен для редактирования другим пользователем: '+n+' (OtherUser)') if mode == 'partial' and i else 'Объект захвачен для редактирования: '+n)
if '/ConfigurationRepositoryCommit' in args and mode != 'silent':
    log += [('Объект не был изменен: ' if mode == 'nochanges' else 'Объект помещен в хранилище: ')+n for n in names]
if '/ConfigurationRepositoryUnLock' in args:
    log += ['Захват объекта отменен: '+n for n in names]
if os.environ.get('REPO_FAKE_SECRET'):
    log += ['diagnostic '+os.environ['REPO_FAKE_SECRET']]
if val('/Out'): pathlib.Path(val('/Out')).write_text('\n'.join(log),encoding='utf-8-sig')
if val('/DumpResult'): pathlib.Path(val('/DumpResult')).write_text('0',encoding='utf-8')
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
  if(Array.IndexOf(a,"/ConfigurationRepositoryLock")>=0)for(int i=0;i<names.Count;i++)lines.Add(mode=="partial"&&i>0?"Объект захвачен для редактирования другим пользователем: "+names[i]+" (OtherUser)":"Объект захвачен для редактирования: "+names[i]);
  if(Array.IndexOf(a,"/ConfigurationRepositoryCommit")>=0&&mode!="silent")foreach(string n in names)lines.Add((mode=="nochanges"?"Объект не был изменен: ":"Объект помещен в хранилище: ")+n);
  if(Array.IndexOf(a,"/ConfigurationRepositoryUnLock")>=0)foreach(string n in names)lines.Add("Захват объекта отменен: "+n);
  string secret=Environment.GetEnvironmentVariable("REPO_FAKE_SECRET");if(secret!=null)lines.Add("diagnostic "+secret);
  if(V(a,"/Out")!=null)File.WriteAllText(V(a,"/Out"),String.Join("\n",lines),new UTF8Encoding(true));
  if(V(a,"/DumpResult")!=null)File.WriteAllText(V(a,"/DumpResult"),"0");
  string report=V(a,"/ConfigurationRepositoryReport")??V(a,"-ReportFile");if(report!=null)File.WriteAllText(report,"Fixture report");
  return 0;
 }
}
'''

if __name__ == '__main__':
    unittest.main(verbosity=2)
