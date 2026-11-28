#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Platform path choice for apply/backup (pilot incident 2026-11-27, fictional fixtures):
databases[].v8path wins over root v8path; root is the fallback; nothing configured keeps
the skills' previous behaviour; an invalid explicit path is an error, never a substitution.
Never starts a real 1C platform."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import safe_apply
import safe_backup
from platform_path import resolve_v8path

PLAN = 'projects/demo/src/CommonModules/Demo/Ext/Module.bsl'

# Fake skills: bind every argument safe_apply/safe_backup pass, record -V8Path verbatim.
FAKE_SKILL = r'''param(
  [string]$V8Path, [string]$InfoBasePath, [string]$InfoBaseServer, [string]$InfoBaseRef,
  [string]$UserName, [string]$UserNameEnv, [string]$PasswordEnv, [string]$ConfigDir,
  [string]$Mode, [string]$Files, [string]$Extension, [string]$OutputFile)
# HARNESS_STAGE_PROTOCOL: 1
if ($env:HARNESS_STAGE_MARKER) { [IO.File]::WriteAllText($env:HARNESS_STAGE_MARKER, 'ack') }
$name = Split-Path -Leaf $PSCommandPath
if ($env:HARNESS_STAGE_MARKER) { [IO.File]::WriteAllText($env:HARNESS_STAGE_MARKER, 'platform-started') }
[IO.File]::AppendAllText((Join-Path $env:V8_CAPTURE_DIR 'calls.txt'), "$name|$V8Path`n")
if ($OutputFile) { [IO.File]::WriteAllText($OutputFile, 'fixture dt') }
$code = [Environment]::GetEnvironmentVariable('FAKE_EXIT_' + ($name -replace '\.ps1$','' -replace '-','_'))
if ($code) { exit [int]$code }
exit 0
'''


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        # two fictional installed releases; the database one lives under a path with spaces
        self.db_platform = self._platform('Program Files/1cv8/8.3.27.1111/bin')
        self.root_platform = self._platform('Other Releases/1cv8/8.3.25.2222/bin')
        self.db = {'id': 'demo-test', 'type': 'server', 'server': 'srv.invalid', 'ref': 'demo_test',
                   'environment': 'test', 'allow_apply': True, 'password_mode': 'none',
                   'configSrc': 'projects/demo/src'}
        self.cfg = {'v8path': str(self.root_platform), 'databases': [self.db]}
        self.capture = self.root / 'capture'
        self.capture.mkdir()

    def _platform(self, rel):
        bin_dir = self.root / rel
        bin_dir.mkdir(parents=True)
        (bin_dir / '1cv8.exe').write_text('fixture', encoding='utf-8')
        return bin_dir

    def save(self):
        (self.root / '.v8-project.json').write_text(json.dumps(self.cfg, ensure_ascii=False), encoding='utf-8')

    def calls(self):
        path = self.capture / 'calls.txt'
        if not path.exists():
            return []
        return [tuple(line.split('|', 1)) for line in path.read_text(encoding='utf-8-sig').splitlines() if line]


class ResolveTests(Fixture):
    def test_database_path_wins_over_root(self):
        self.db['v8path'] = str(self.db_platform)
        self.assertEqual(resolve_v8path(self.cfg, self.db), (str(self.db_platform), 'database'))

    def test_root_used_without_database_setting(self):
        self.assertEqual(resolve_v8path(self.cfg, self.db), (str(self.root_platform), 'root'))
        self.db['v8path'] = ''  # empty = not configured
        self.assertEqual(resolve_v8path(self.cfg, self.db), (str(self.root_platform), 'root'))

    def test_nothing_configured_keeps_previous_behaviour(self):
        self.assertEqual(resolve_v8path({'databases': [self.db]}, self.db), ('', ''))

    def test_executable_file_accepted(self):
        self.db['v8path'] = str(self.db_platform / '1cv8.exe')
        self.assertEqual(resolve_v8path(self.cfg, self.db)[1], 'database')

    def test_invalid_database_path_is_error_not_substitution(self):
        for bad in (str(self.root / 'missing/bin'), str(self._platform('empty/bin').parent), 42):
            with self.subTest(bad=bad):
                self.db['v8path'] = bad
                with self.assertRaises(ValueError) as ctx:
                    resolve_v8path(self.cfg, self.db)  # valid root must NOT be used instead
                self.assertIn('databases', str(ctx.exception))

    def test_invalid_root_path_is_error(self):
        self.cfg['v8path'] = str(self.root / 'nowhere')
        with self.assertRaises(ValueError):
            resolve_v8path(self.cfg, self.db)

    def test_repository_wrapper_uses_same_priority(self):
        import repository_support
        self.db['v8path'] = str(self.db_platform)
        self.db['repository'] = {'enabled': True, 'path': 'tcp://repo.invalid/demo', 'environment': 'dev',
                                 'username_env': 'DEMO_REPO_USER', 'password_mode': 'none'}
        self.save()
        with patch.dict(os.environ, {'DEMO_REPO_USER': 'fixture-user'}):
            db = repository_support.profile(self.root, 'demo-test', 'status')
        self.assertEqual(db['_repository_v8path'], str(self.db_platform))


@unittest.skipUnless(shutil.which('pwsh') or shutil.which('powershell'), 'PowerShell required')
class EntryPointTests(Fixture):
    def setUp(self):
        super().setUp()
        for skill in ('db-load-xml', 'db-update', 'db-dump-dt'):
            script = self.root / '.kilo/skills' / skill / 'scripts' / f'{skill}.ps1'
            script.parent.mkdir(parents=True)
            script.write_text(FAKE_SKILL, encoding='utf-8-sig')
        module = self.root / PLAN
        module.parent.mkdir(parents=True)
        module.write_text('// fixture', encoding='utf-8')
        report = self.root / 'specs/TASK-DEMO/06_change_report.md'
        report.parent.mkdir(parents=True)
        report.write_text('## Изменённые файлы\n- ' + PLAN + '\n', encoding='utf-8')
        self.env = patch.dict(os.environ, {'PWSH': shutil.which('pwsh') or shutil.which('powershell'),
                                           'V8_CAPTURE_DIR': str(self.capture)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def apply(self):
        argv = ['safe_apply.py', '--task', 'TASK-DEMO', '--db', 'demo-test', '--project-root', str(self.root)]
        with patch.object(sys, 'argv', argv), patch.object(safe_apply, 'run_guard', return_value=0):
            return safe_apply.main()

    def backup(self):
        argv = ['safe_backup.py', '--task', 'TASK-DEMO', '--db', 'demo-test', '--project-root', str(self.root)]
        with patch.object(sys, 'argv', argv):
            return safe_backup.main()

    def test_apply_database_path_beats_root_for_load_and_update(self):
        self.db['v8path'] = str(self.db_platform); self.save()
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.calls(), [('db-load-xml.ps1', str(self.db_platform)),
                                        ('db-update.ps1', str(self.db_platform))])  # one path, with spaces, one argument

    def test_apply_root_path_without_database_setting(self):
        self.save()
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.calls(), [('db-load-xml.ps1', str(self.root_platform)),
                                        ('db-update.ps1', str(self.root_platform))])

    def test_apply_nothing_configured_passes_no_v8path(self):
        del self.cfg['v8path']; self.save()
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.calls(), [('db-load-xml.ps1', ''), ('db-update.ps1', '')])

    def test_apply_invalid_database_path_stops_before_any_skill(self):
        self.db['v8path'] = str(self.root / 'Program Files/1cv8/8.3.27.9999/bin'); self.save()
        self.assertEqual(self.apply(), 2)
        self.assertEqual(self.calls(), [])

    def test_apply_failed_load_still_blocks_update(self):
        self.db['v8path'] = str(self.db_platform); self.save()
        with patch.dict(os.environ, {'FAKE_EXIT_db_load_xml': '1'}):
            self.assertEqual(self.apply(), 1)
        self.assertEqual(self.calls(), [('db-load-xml.ps1', str(self.db_platform))])

    def test_backup_database_path_beats_root(self):
        self.db['v8path'] = str(self.db_platform); self.save()
        self.assertEqual(self.backup(), 0)
        self.assertEqual(self.calls(), [('db-dump-dt.ps1', str(self.db_platform))])

    def test_backup_root_path_and_invalid_database_path(self):
        self.save()
        self.assertEqual(self.backup(), 0)
        self.assertEqual(self.calls(), [('db-dump-dt.ps1', str(self.root_platform))])
        shutil.rmtree(self.root / 'pilot-control')
        (self.capture / 'calls.txt').unlink()
        self.db['v8path'] = str(self.root / 'missing'); self.save()
        self.assertEqual(self.backup(), 2)
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
