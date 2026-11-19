#!/usr/bin/env python3
"""Offline runtime diagnostics, no installations or client config changes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings

SCRIPTS = Path(__file__).resolve().parent


class RuntimeTests(unittest.TestCase):
    def test_git_fixture_compiles_without_invalid_escape_warnings(self):
        with warnings.catch_warnings():
            warnings.simplefilter('error', SyntaxWarning)
            warnings.simplefilter('error', DeprecationWarning)
            compile((SCRIPTS / 'test_git_reviewer.py').read_text(encoding='utf-8-sig'),
                    'test_git_reviewer.py', 'exec')

    def probe(self, *args, env=None, shell=None):
        pwsh = shell or shutil.which('pwsh') or shutil.which('powershell')
        if not pwsh:
            self.skipTest('PowerShell unavailable; compilation test still runs')
        run = subprocess.run([pwsh, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                              str(SCRIPTS / 'check-python.ps1'), *args], env=env,
                             capture_output=True, text=True, encoding='utf-8', timeout=40)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout)
        self.assertFalse(result['changed_settings'])
        self.assertIn('not tested', result['permissions'])
        return result

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell 5.1 compatibility')
    def test_windows_powershell_51_probe(self):
        shell = shutil.which('powershell')
        self.assertIsNotNone(shell)
        env = dict(os.environ)
        env.pop('PSModulePath', None)
        result = self.probe('-PythonPath', sys.executable, env=env, shell=shell)
        self.assertEqual(result['runtimes'][0]['state'], 'available')

    def test_real_interpreter_is_identified(self):
        result = self.probe('-PythonPath', sys.executable)
        runtime = result['runtimes'][0]
        self.assertEqual(runtime['state'], 'available')
        self.assertTrue(Path(runtime['interpreter']).samefile(sys.executable))
        self.assertIn('not proof', result['process'])

    def test_missing_process_path_does_not_claim_python_uninstalled(self):
        env = dict(os.environ, PATH='')
        result = self.probe(env=env)
        self.assertEqual([r['state'] for r in result['runtimes']],
                         ['not_on_process_path', 'not_on_process_path'])
        self.assertIn('not determined', result['installed_on_machine'])

    def test_process_launch_failure_is_not_permission_diagnosis(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'invalid-python.exe'
            path.write_bytes(b'not an executable')
            result = self.probe('-PythonPath', str(path))
        self.assertEqual(result['runtimes'][0]['state'], 'launch_failed')
        self.assertIn('not tested', result['permissions'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
