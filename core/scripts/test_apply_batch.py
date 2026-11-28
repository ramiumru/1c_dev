#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch verdict of apply stages (pilot incident 2026-11-28, fictional fixtures).

Real db-load-xml.ps1 / db-update.ps1 run through safe_apply.main() against a fake 1cv8:
- a repository connection failure line in /Out blocks db-update even with exit 0;
- a missing /DumpResult is not success;
- a stage that outlives its timeout is journaled as started/unknown, db-update does not run,
  and the next apply refuses until a human resolves the unknown outcome;
- infobase credentials never appear in safe_apply output or the stage journal.
No real 1C platform is started.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import safe_apply

SCRIPTS = Path(__file__).resolve().parent
PLAN = 'projects/demo/src/CommonModules/Demo/Ext/Module.bsl'
SECRET = 'fixture-db-secret-42'

FAKE_V8 = r'''
import os, sys, time, pathlib
args = sys.argv[1:]
def val(key):
    return args[args.index(key) + 1] if key in args else None
pathlib.Path(os.environ['FAKE_V8_CAPTURE']).open('a', encoding='utf-8').write(repr(args) + '\n')
stage = 'load' if '/LoadConfigFromFiles' in args else 'update' if '/UpdateDBCfg' in args else 'other'
mode = os.environ.get('FAKE_V8_' + stage.upper(), 'ok')
if mode == 'hang':
    time.sleep(30)
lines = {'ok': [], 'repo_conn': ['Соединение с хранилищем конфигурации не установлено'],
         'nodump': [], 'hang': []}[mode]
if val('/Out'):
    pathlib.Path(val('/Out')).write_text('\n'.join(lines), encoding='utf-8-sig')
if val('/DumpResult') and mode != 'nodump':
    pathlib.Path(val('/DumpResult')).write_text('0', encoding='utf-8')
sys.exit(0)
'''


def _clock(*values):
    """Fake monotonic clock: the given readings in order, then the last one forever."""
    state = list(values)
    def tick():
        return state.pop(0) if len(state) > 1 else state[0]
    return tick


def _skill_source(name):
    path = SCRIPTS.parent / 'skills' / name / 'scripts' / f'{name}.ps1'
    if not path.exists():  # installed layout
        path = safe_apply.find_skills_dir(SCRIPTS.parent) / name / 'scripts' / f'{name}.ps1'
    return path


@unittest.skipIf(os.name == 'nt', 'fake 1cv8 is a POSIX script; Windows coverage via test_mock_apply')
@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell 7 required')
class ApplyBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        bin_dir = self.root / 'Program Files/1cv8/8.3.27.1111/bin'
        bin_dir.mkdir(parents=True)
        fake = bin_dir / '1cv8'
        fake.write_text('#!' + sys.executable + '\n' + FAKE_V8, encoding='utf-8')
        fake.chmod(0o755)
        self.db = {'id': 'demo-test', 'type': 'file', 'path': str(self.root / 'demo-base'),
                   'environment': 'test', 'password_mode': 'env', 'username': 'fixture-user',
                   'password_env': 'DEMO_DB_PASSWORD', 'configSrc': 'projects/demo/src', 'v8path': str(fake)}
        (self.root / 'demo-base').mkdir()
        (self.root / '.v8-project.json').write_text(json.dumps({'databases': [self.db]}), encoding='utf-8')
        for skill in ('db-load-xml', 'db-update'):
            dst = self.root / '.kilo/skills' / skill / 'scripts' / f'{skill}.ps1'
            dst.parent.mkdir(parents=True)
            shutil.copyfile(_skill_source(skill), dst)
        module = self.root / PLAN
        module.parent.mkdir(parents=True)
        module.write_text('// fixture', encoding='utf-8')
        report = self.root / 'specs/TASK-DEMO/06_change_report.md'
        report.parent.mkdir(parents=True)
        report.write_text('## Изменённые файлы\n- ' + PLAN + '\n', encoding='utf-8')
        self.capture = self.root / 'v8-calls.txt'
        self.env = patch.dict(os.environ, {'PWSH': shutil.which('pwsh'), 'TEMP': str(self.root / 'tmp'),
                                           'FAKE_V8_CAPTURE': str(self.capture), 'DEMO_DB_PASSWORD': SECRET})
        (self.root / 'tmp').mkdir()
        self.env.start()
        self.addCleanup(self.env.stop)

    def apply(self, extra=(), task='TASK-DEMO', db='demo-test', **modes):
        report = self.root / 'specs' / task / '06_change_report.md'
        if not report.exists():
            report.parent.mkdir(parents=True)
            report.write_text('## Изменённые файлы\n- ' + PLAN + '\n', encoding='utf-8')
        argv = ['safe_apply.py', '--task', task, '--db', db, '--project-root', str(self.root), *extra]
        out, err = io.StringIO(), io.StringIO()
        env = {f'FAKE_V8_{k.upper()}': v for k, v in modes.items()}
        with patch.dict(os.environ, env), patch.object(sys, 'argv', argv), \
             patch.object(safe_apply, 'run_guard', return_value=0), redirect_stdout(out), redirect_stderr(err):
            code = safe_apply.main()
        return code, out.getvalue() + err.getvalue()

    def stages(self):
        return [line for line in self.capture.read_text(encoding='utf-8').splitlines()] if self.capture.exists() else []

    def journal(self):
        return json.loads(self.journal_file().read_text(encoding='utf-8'))

    def journal_file(self, task='TASK-DEMO', db='demo-test'):
        return safe_apply.journal_path(self.root, task, db)

    def test_clean_run_loads_then_updates_and_journals_both(self):
        code, output = self.apply()
        self.assertEqual(code, 0, output)
        calls = self.stages()
        self.assertEqual(len(calls), 2)
        self.assertIn('/LoadConfigFromFiles', calls[0])
        self.assertIn('/UpdateDBCfg', calls[1])
        self.assertIn('/DumpResult', calls[0])
        stages = self.journal()['runs'][-1]['stages']
        self.assertEqual([(s['name'], s['outcome']) for s in stages], [('db-load-xml', 'succeeded'), ('db-update', 'succeeded')])

    def test_repository_connection_line_with_exit_zero_blocks_update(self):
        code, output = self.apply(load='repo_conn')
        self.assertEqual(code, 1, output)
        calls = self.stages()
        self.assertEqual(len(calls), 1)  # db-update never started
        self.assertIn('/LoadConfigFromFiles', calls[0])
        self.assertIn('repository connection failure', output)
        self.assertIn('update не запускается', output)
        self.assertEqual(self.journal()['runs'][-1]['stages'][0]['outcome'], 'failed_after_launch')

    def test_update_connection_line_is_failure_too(self):
        code, output = self.apply(update='repo_conn')
        self.assertEqual(code, 1, output)
        self.assertEqual([s['outcome'] for s in self.journal()['runs'][-1]['stages']], ['succeeded', 'failed_after_launch'])

    def test_missing_dump_result_is_not_success(self):
        code, output = self.apply(load='nodump')
        self.assertEqual(code, 1, output)
        self.assertEqual(len(self.stages()), 1)
        self.assertIn('/DumpResult was not written', output)

    def test_timeout_reports_stage_unknown_and_next_apply_refuses(self):
        with patch.object(safe_apply, 'stage_timeout_seconds', return_value=3):
            code, output = self.apply(load='hang')
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        self.assertIn("этап 'db-load-xml' запущен, ЗАВЕРШЕНИЕ НЕ ПОДТВЕРЖДЕНО", output)
        self.assertIn('update НЕ запускался', output)
        self.assertEqual(len(self.stages()), 1)
        stage = self.journal()['runs'][-1]['stages'][0]
        self.assertEqual((stage['name'], stage['outcome'], stage['exit']), ('db-load-xml', 'unknown', safe_apply.STAGE_TIMEOUT_EXIT))
        # no automatic retry: the next call refuses before any stage
        code, output = self.apply()
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        self.assertIn('завершение не подтверждено', output)
        self.assertEqual(len(self.stages()), 1)
        # an explicit human resolution is recorded and only then the stages run again
        code, output = self.apply(extra=('--resolve-unknown-outcome', 'checked: no 1cv8 process, base state verified'))
        self.assertEqual(code, 0, output)
        runs = self.journal()['runs']
        self.assertIn('checked', runs[0]['resolution']['reason'])

    def test_killed_runner_leaves_started_stage_that_blocks(self):
        # the shell killed safe_apply mid-stage: the journal still says "started"
        journal = safe_apply.ApplyJournal(self.root, 'TASK-DEMO', 'demo-test')
        journal.begin_run(); journal.start('db-load-xml', 540)
        code, output = self.apply()
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        self.assertEqual(self.stages(), [])

    # --- review of 56bc04d: whole-operation state, checked before lock-refresh ---------------
    def _repo_profile(self):
        self.db['repository'] = {'enabled': True, 'path': 'tcp://repo.invalid/demo', 'environment': 'dev'}
        (self.root / '.v8-project.json').write_text(json.dumps({'databases': [self.db]}), encoding='utf-8')

    def _apply_repo(self, extra=(), **modes):
        import safe_repository
        import repository_support
        refreshes = []
        with patch.object(safe_repository, 'before_apply', side_effect=lambda *a, **k: refreshes.append(a)), \
             patch.object(repository_support, 'record_apply', return_value=None):
            code, output = self.apply(extra, **modes)
        return code, output, refreshes

    def test_failed_update_blocks_reload_before_lock_refresh(self):
        self._repo_profile()
        code, output, refreshes = self._apply_repo(update='repo_conn')
        self.assertEqual((code, len(refreshes)), (1, 1), output)
        calls_before = len(self.stages())
        code, output, refreshes = self._apply_repo()
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        self.assertIn('обновление БД не подтверждено (failed_after_launch)', output)
        self.assertEqual(refreshes, [])                      # no platform lock-refresh
        self.assertEqual(len(self.stages()), calls_before)   # no load either

    def test_interruption_between_stages_blocks_reload_before_lock_refresh(self):
        self._repo_profile()
        journal = safe_apply.ApplyJournal(self.root, 'TASK-DEMO', 'demo-test')
        journal.begin_run(); journal.start('db-load-xml', 540); journal.finish(0)  # killed before update
        code, output, refreshes = self._apply_repo()
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        self.assertIn('обновление БД не подтверждено (не запускался)', output)
        self.assertEqual(refreshes, [])
        self.assertEqual(self.stages(), [])
        # after the human check the same call proceeds and refreshes the lock first
        code, output, refreshes = self._apply_repo(extra=('--resolve-unknown-outcome', 'main vs DB config compared: equal'))
        self.assertEqual((code, len(refreshes)), (0, 1), output)

    def test_refusal_before_launch_allows_a_new_run(self):
        # the skill refuses before 1cv8 starts (credential variable missing): IB untouched
        with patch.dict(os.environ, {'DEMO_DB_PASSWORD': ''}):
            code, output = self.apply()
        self.assertEqual(code, 1, output)
        self.assertEqual(self.stages(), [])                       # platform never started
        stage = self.journal()['runs'][-1]['stages'][0]
        self.assertEqual((stage['outcome'], stage['signal']), ('failed_before_launch', 'ack'))
        self.assertIn('явный отказ до запуска платформы', output)
        code, output = self.apply()
        self.assertEqual(code, 0, output)

    def test_platform_without_dump_result_blocks_reload_before_lock_refresh(self):
        self._repo_profile()
        code, output, refreshes = self._apply_repo(load='nodump')
        self.assertEqual((code, len(refreshes)), (1, 1), output)
        stage = self.journal()['runs'][-1]['stages'][0]
        self.assertEqual((stage['outcome'], stage['signal']), ('failed_after_launch', 'platform-started'))
        self.assertIn('после запуска платформы', output)
        calls = len(self.stages())
        code, output, refreshes = self._apply_repo()
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        self.assertIn('завершилась без подтверждения', output)
        self.assertEqual(refreshes, [])
        self.assertEqual(len(self.stages()), calls)

    def test_connection_failure_after_launch_blocks_reload(self):
        code, _ = self.apply(load='repo_conn')
        self.assertEqual(code, 1)
        code, output = self.apply()
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)

    def test_parallel_attempts_on_one_database(self):
        # controlled order: A holds the apply lock inside its journal check; B starts then
        import threading
        in_check, release = threading.Event(), threading.Event()
        real = safe_apply.unresolved_for_database
        def paused(root, db_id):
            if threading.current_thread().name == 'A':
                in_check.set()
                release.wait(10)
            return real(root, db_id)
        results = {}
        def attempt(name):
            results[name] = self.apply()
        with patch.object(safe_apply, 'unresolved_for_database', side_effect=paused):
            a = threading.Thread(target=attempt, args=('A',), name='A'); a.start()
            self.assertTrue(in_check.wait(10))
            b = threading.Thread(target=attempt, args=('B',), name='B'); b.start(); b.join(20)
            journal_during = self.journal_file().exists()
            release.set(); a.join(60)
        self.assertEqual(results['B'][0], safe_apply.BUSY_EXIT, results['B'][1])
        self.assertIn('уже выполняется другой safe_apply', results['B'][1])
        self.assertFalse(journal_during)            # B wrote nothing while A was checking
        self.assertEqual(results['A'][0], 0, results['A'][1])
        self.assertEqual(len(self.journal()['runs']), 1)   # one run: A's
        self.assertEqual(len(self.stages()), 2)            # A's load + update only

    def test_dry_run_never_records_a_resolution(self):
        journal = safe_apply.ApplyJournal(self.root, 'TASK-DEMO', 'demo-test')
        journal.begin_run(); journal.start('db-load-xml', 540); journal.finish(0)
        code, output = self.apply(extra=('--dry-run', '--resolve-unknown-outcome', 'only a dry run, nothing checked'))
        self.assertEqual(code, 0, output)
        self.assertNotIn('resolution', self.journal()['runs'][0])

    def test_shell_budget_stops_before_load_without_blocking(self):
        with patch.object(safe_apply.time, 'monotonic', side_effect=_clock(0.0, 200.0)):
            code, output = self.apply(extra=('--shell-budget-seconds', '250'))
        self.assertEqual(code, safe_apply.BUDGET_EXIT, output)
        self.assertIn("этап 'db-load-xml' НЕ запущен", output)
        self.assertEqual(self.stages(), [])
        self.assertEqual(self.journal()['runs'][-1]['stages'][0]['outcome'], 'not_started')
        code, output = self.apply()  # nothing was loaded: not an unresolved state
        self.assertEqual(code, 0, output)

    def test_shell_budget_exhausted_after_load_blocks_until_resolved(self):
        # readings: call start, apply lock, load budget, update budget
        with patch.object(safe_apply.time, 'monotonic', side_effect=_clock(0.0, 0.0, 10.0, 500.0)):
            code, output = self.apply(extra=('--shell-budget-seconds', '570'))
        self.assertEqual(code, safe_apply.BUDGET_EXIT, output)
        self.assertIn("этап 'db-update' НЕ запущен", output)
        self.assertEqual(len(self.stages()), 1)
        code, output = self.apply()
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        self.assertIn('обновление БД не подтверждено (not_started)', output)

    def test_shell_budget_caps_stage_timeout(self):
        seen = []
        real = safe_apply.run_skill
        def spy(name, args, path):
            seen.append(safe_apply.STAGE_TIMEOUT_SECONDS)
            return real(name, args, path)
        with patch.object(safe_apply, 'run_skill', side_effect=spy):
            code, output = self.apply(extra=('--shell-budget-seconds', '200'))
        self.assertEqual(code, 0, output)
        self.assertTrue(all(60 <= t <= 180 for t in seen), seen)

    # --- review of b49ae4b ---------------------------------------------------------------------
    def test_unknown_outcome_of_task_a_blocks_task_b_on_same_database(self):
        self._repo_profile()
        with patch.object(safe_apply, 'stage_timeout_seconds', return_value=3):
            code, output, _ = self._apply_repo(load='hang')           # task A times out and exits
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        calls = len(self.stages())
        import safe_repository, repository_support
        refreshes = []
        with patch.object(safe_repository, 'before_apply', side_effect=lambda *a, **k: refreshes.append(a)), \
             patch.object(repository_support, 'record_apply', return_value=None):
            code, output = self.apply(task='TASK-OTHER')               # task B, same database
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        self.assertIn('задача TASK-DEMO', output)
        self.assertEqual(refreshes, [])                                 # no lock-refresh
        self.assertEqual(len(self.stages()), calls)                     # no platform start
        self.assertFalse(self.journal_file('TASK-OTHER').exists())
        # resolving from task B closes A's run in A's journal, then B proceeds
        with patch.object(safe_repository, 'before_apply', side_effect=lambda *a, **k: refreshes.append(a)), \
             patch.object(repository_support, 'record_apply', return_value=None):
            code, output = self.apply(task='TASK-OTHER', extra=('--resolve-unknown-outcome', 'checked: no 1cv8, configs compared'))
        self.assertEqual((code, len(refreshes)), (0, 1), output)
        a_runs = self.journal()['runs']
        self.assertEqual(a_runs[0]['resolution']['resolved_from_task'], 'TASK-OTHER')

    def test_unknown_outcome_does_not_block_another_database(self):
        other = dict(self.db, id='other-test', path=str(self.root / 'other-base'))
        (self.root / 'other-base').mkdir()
        (self.root / '.v8-project.json').write_text(json.dumps({'databases': [self.db, other]}), encoding='utf-8')
        with patch.object(safe_apply, 'stage_timeout_seconds', return_value=3):
            code, _ = self.apply(load='hang')
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT)
        code, output = self.apply(task='TASK-OTHER', db='other-test')
        self.assertEqual(code, 0, output)

    def _install_skill(self, name, body):
        script = self.root / '.kilo/skills' / name / 'scripts' / f'{name}.ps1'
        script.write_text(body, encoding='utf-8-sig')

    OLD_SKILL = r"""param([string]$V8Path, [string]$InfoBasePath, [string]$InfoBaseServer, [string]$InfoBaseRef,
  [string]$UserName, [string]$UserNameEnv, [string]$PasswordEnv, [string]$ConfigDir, [string]$Mode,
  [string]$Files, [string]$Extension)
[IO.File]::WriteAllText((Join-Path $env:TEMP 'old-skill-ran.txt'), 'touched')
exit 1
"""

    def test_mixed_install_old_skill_is_not_started(self):
        # new safe_apply + old db-load-xml without the stage protocol: refused before it runs
        self._repo_profile()
        self._install_skill('db-load-xml', self.OLD_SKILL)
        code, output, refreshes = self._apply_repo()
        self.assertEqual(code, 1, output)
        self.assertIn('не поддерживает протокол этапов', output)
        self.assertFalse((self.root / 'tmp/old-skill-ran.txt').exists())   # the old skill never ran
        self.assertEqual(self.stages(), [])
        stage = self.journal()['runs'][-1]['stages'][0]
        self.assertEqual((stage['outcome'], stage['signal']), ('failed_before_launch', 'incompatible'))

    def test_skill_without_signal_is_unconfirmed_and_blocks(self):
        # claims the protocol but sends no signal, touches a file and fails: nothing confirmed
        self._repo_profile()
        self._install_skill('db-load-xml', '# HARNESS_STAGE_PROTOCOL: 1\n' + self.OLD_SKILL)
        code, output, refreshes = self._apply_repo()
        self.assertEqual(code, 1, output)
        self.assertTrue((self.root / 'tmp/old-skill-ran.txt').exists())
        self.assertIn('не прислал сигнал протокола', output)
        stage = self.journal()['runs'][-1]['stages'][0]
        self.assertEqual((stage['outcome'], stage['signal']), ('failed_no_signal', 'missing'))
        self._install_skill('db-load-xml', _skill_source('db-load-xml').read_text(encoding='utf-8-sig'))
        code, output, refreshes = self._apply_repo()
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        self.assertEqual(refreshes, [])

    def test_success_without_signal_is_not_success(self):
        self._install_skill('db-load-xml', '# HARNESS_STAGE_PROTOCOL: 1\n' + self.OLD_SKILL.replace('exit 1', 'exit 0'))
        code, output = self.apply()
        self.assertEqual(code, 1, output)
        self.assertEqual(self.stages(), [])   # db-update never ran (fake platform untouched)
        self.assertEqual(self.journal()['runs'][-1]['stages'][0]['outcome'], 'failed_no_signal')

    # --- review of 41ff301 ---------------------------------------------------------------------
    ACK_ONLY_SKILL = r"""# HARNESS_STAGE_PROTOCOL: 1
param([string]$V8Path, [string]$InfoBasePath, [string]$InfoBaseServer, [string]$InfoBaseRef,
  [string]$UserName, [string]$UserNameEnv, [string]$PasswordEnv, [string]$ConfigDir, [string]$Mode,
  [string]$Files, [string]$Extension)
if ($env:HARNESS_STAGE_MARKER) { [IO.File]::WriteAllText($env:HARNESS_STAGE_MARKER, 'ack') }
exit 0
"""

    def test_ack_with_exit_zero_is_unconfirmed_and_blocks(self):
        self._repo_profile()
        self._install_skill('db-load-xml', self.ACK_ONLY_SKILL)
        code, output, refreshes = self._apply_repo()
        self.assertEqual((code, len(refreshes)), (1, 1), output)
        self.assertIn('exit 0 без сигнала platform-started', output)
        stage = self.journal()['runs'][-1]['stages']
        self.assertEqual([(st['name'], st['outcome']) for st in stage], [('db-load-xml', 'unconfirmed_without_launch')])
        self.assertEqual(self.stages(), [])                 # db-update never started
        self._install_skill('db-load-xml', _skill_source('db-load-xml').read_text(encoding='utf-8-sig'))
        code, output, refreshes = self._apply_repo()
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        self.assertEqual(refreshes, [])

    def _second_database(self):
        other = dict(self.db, id='second-test', path=str(self.root / 'second-base'))
        (self.root / 'second-base').mkdir()
        (self.root / '.v8-project.json').write_text(json.dumps({'databases': [self.db, other]}), encoding='utf-8')

    def test_same_task_on_two_databases_keeps_both_records(self):
        # controlled order: B (second db) loads its journal and pauses; A (first db) times out and
        # records an unknown outcome; then B writes its run. A's record must survive.
        import threading
        self._second_database()
        loaded, release = threading.Event(), threading.Event()
        real_init = safe_apply.ApplyJournal.__init__
        def init(journal, project_root, task, db_id, path=None):
            real_init(journal, project_root, task, db_id, path)
            if path is None and threading.current_thread().name == 'B':
                loaded.set()
                release.wait(20)
        results = {}
        with patch.object(safe_apply.ApplyJournal, '__init__', init):
            b = threading.Thread(target=lambda: results.__setitem__('B', self.apply(db='second-test')), name='B')
            b.start()
            self.assertTrue(loaded.wait(20))
            with patch.object(safe_apply, 'stage_timeout_seconds', return_value=3):
                results['A'] = self.apply(load='hang')          # first db, same TASK-DEMO
            release.set(); b.join(60)
        self.assertEqual(results['A'][0], safe_apply.UNRESOLVED_EXIT, results['A'][1])
        self.assertEqual(results['B'][0], 0, results['B'][1])
        first = json.loads(self.journal_file(db='demo-test').read_text(encoding='utf-8'))
        second = json.loads(self.journal_file(db='second-test').read_text(encoding='utf-8'))
        self.assertEqual(first['runs'][-1]['stages'][0]['outcome'], 'unknown')
        self.assertEqual([st['outcome'] for st in second['runs'][-1]['stages']], ['succeeded', 'succeeded'])
        code, output = self.apply()                              # next apply on the FIRST db: blocked
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        code, output = self.apply(db='second-test')              # the second db is not blocked
        self.assertEqual(code, 0, output)

    def test_legacy_shared_journal_is_still_read_and_resolvable(self):
        legacy = self.root / 'pilot-control/TASK-DEMO/apply-run.json'
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({'runs': [
            {'run_id': 'old1', 'db': 'demo-test', 'stages': [{'name': 'db-load-xml', 'outcome': 'started'}]},
            {'run_id': 'old2', 'db': 'elsewhere', 'stages': [{'name': 'db-load-xml', 'outcome': 'started'}]}]}),
            encoding='utf-8')
        code, output = self.apply()
        self.assertEqual(code, safe_apply.UNRESOLVED_EXIT, output)
        self.assertIn('old1', output)
        self.assertNotIn('old2', output)                         # another database's record
        code, output = self.apply(extra=('--resolve-unknown-outcome', 'legacy run checked: no 1cv8'))
        self.assertEqual(code, 0, output)
        runs = {r['run_id']: r for r in json.loads(legacy.read_text(encoding='utf-8'))['runs']}
        self.assertIn('resolution', runs['old1'])
        self.assertNotIn('resolution', runs['old2'])
        self.assertTrue(self.journal_file().exists())            # new runs go to the per-db file

    def test_credentials_never_in_output_or_journal(self):
        for modes in ({}, {'load': 'repo_conn'}):
            with self.subTest(modes=modes):
                code, output = self.apply(**modes)
                self.assertNotIn(SECRET, output)
                self.assertNotIn(SECRET, self.journal_file().read_text(encoding='utf-8'))
        self.assertIn(SECRET, self.capture.read_text(encoding='utf-8'))  # reached the platform only


if __name__ == '__main__':
    unittest.main(verbosity=2)
