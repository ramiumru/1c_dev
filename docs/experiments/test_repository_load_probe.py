#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe script masking: credentials (including a quote) never reach command.txt, out.txt,
console.txt, meta.json or reports; the platform still receives them. Fake platform only."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROBE = Path(__file__).resolve().parent / 'repository-load-probe.ps1'
IB_PASS = 'ib-pa"ss\'w0rd'
REPO_PASS = 'repo-"quoted"-secret'
IB_USER = 'probe-ib-user'
REPO_USER = 'probe-repo-user'

FAKE = r'''
import os, sys, shutil, pathlib
a = sys.argv[1:]
pathlib.Path(os.environ['PROBE_ARGV']).open('a', encoding='utf-8').write(repr(a) + '\n')
def v(k):
    for i, x in enumerate(a):
        if x == k and i + 1 < len(a): return a[i + 1]
        if x.startswith(k) and len(x) > len(k): return x[len(k):]
out, res = v('/Out'), v('/DumpResult')
repo = any(x.startswith('/ConfigurationRepositoryF') for x in a)
# a platform that echoes credentials into its log must still be masked
echo = 'login ' + (v('/N') or '') + ' pass ' + (v('/P') or '') + ' repo ' + (v('/ConfigurationRepositoryP') or '')
pathlib.Path(out).write_text(('' if repo else 'Соединение с хранилищем конфигурации не установлено\n') + echo, encoding='utf-8-sig')
pathlib.Path(res).write_text('0' if repo or '/DumpConfigToFiles' in a or '/CompareCfg' in a else '1')
if '/DumpConfigToFiles' in a:
    rel = 'CommonModules/A/Ext/Module.bsl'
    dst = os.path.join(v('/DumpConfigToFiles'), rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy(os.path.join(os.environ['PROBE_SRC'], rel), dst)
if '/CompareCfg' in a:
    pathlib.Path(v('-ReportFile')).write_text('Объекты совпадают ' + echo, encoding='utf-8')
print(echo)
'''


@unittest.skipIf(os.name == 'nt', 'fake platform is a POSIX script')
@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell 7 required')
class ProbeMaskingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        t = Path(self.temp.name)
        self.bin = t / 'Program Files' / '1cv8' / '8.3.27.1111' / 'bin'
        self.bin.mkdir(parents=True)
        fake = self.bin / '1cv8'
        fake.write_text('#!' + sys.executable + '\n' + FAKE, encoding='utf-8'); fake.chmod(0o755)
        self.src = t / 'src'
        (self.src / 'CommonModules/A/Ext').mkdir(parents=True)
        (self.src / 'CommonModules/A/Ext/Module.bsl').write_text('// A', encoding='utf-8')
        self.list = t / 'list.txt'; self.list.write_text('CommonModules/A/Ext/Module.bsl\n', encoding='utf-8')
        self.out = t / 'evidence'
        self.argv = t / 'argv.txt'
        self.env = dict(os.environ, PROBE_ARGV=str(self.argv), PROBE_SRC=str(self.src),
                        T_IB_U=IB_USER, T_IB_P=IB_PASS, T_R_U=REPO_USER, T_R_P=REPO_PASS)

    def run_probe(self, variant, *extra):
        cmd = [shutil.which('pwsh'), '-NoProfile', '-File', str(PROBE), '-Variant', variant,
               '-V8Path', str(self.bin / '1cv8'), '-InfoBaseServer', 'srv.invalid', '-InfoBaseRef', 'demo_copy',
               '-IbUserEnv', 'T_IB_U', '-IbPasswordEnv', 'T_IB_P', '-RepositoryPath', 'http://repo.invalid/test',
               '-RepoUserEnv', 'T_R_U', '-RepoPasswordEnv', 'T_R_P', '-ConfigDir', str(self.src),
               '-ListFile', str(self.list), '-ObjectNames', 'ОбщийМодуль.A', '-OutDir', str(self.out),
               '-TestCopyConfirmed', *extra]
        r = subprocess.run(cmd, env=self.env, capture_output=True, text=True, encoding='utf-8', timeout=120)
        return r.returncode, r.stdout + r.stderr

    def assert_no_secret(self, text, where):
        for secret in (IB_PASS, REPO_PASS, IB_USER, REPO_USER):
            for form in {secret, secret.replace('"', '""'), secret.replace('"', '\\"')}:
                self.assertNotIn(form, text, f'{where}: {form!r} leaked')

    def test_all_variants_masked_with_quoted_passwords(self):
        for variant in ('load-without-repo', 'load-with-repo', 'verify', 'verify-db',
                        'update-without-repo', 'update-with-repo'):
            with self.subTest(variant=variant):
                code, console = self.run_probe(variant)
                self.assertEqual(code, 0, console)
                self.assert_no_secret(console, f'{variant} console')
        for path in self.out.rglob('*'):
            if path.is_file():
                self.assert_no_secret(path.read_text(encoding='utf-8-sig'), str(path.relative_to(self.out)))
        commands = [p.read_text(encoding='utf-8') for p in self.out.rglob('command.txt')]
        self.assertTrue(all('/P"***"' in c for c in commands), commands)
        self.assertTrue(any('/ConfigurationRepositoryP"***"' in c for c in commands))
        received = self.argv.read_text(encoding='utf-8')
        self.assertIn('ib-pa', received)  # the platform got the real credential
        metas = {p.parent.name.rsplit('-', 1)[0]: json.loads(p.read_text(encoding='utf-8')) for p in self.out.rglob('meta.json')}
        self.assertTrue(metas['verify']['verify_all_equal'])
        self.assertTrue(metas['verify-db']['compare_report_present'])
        self.assertFalse(metas['load-without-repo']['dump_result_value'] == 0)

    def test_refuses_without_test_copy_confirmation(self):
        cmd = [shutil.which('pwsh'), '-NoProfile', '-File', str(PROBE), '-Variant', 'update-with-repo',
               '-V8Path', str(self.bin / '1cv8'), '-InfoBasePath', '/tmp/x', '-OutDir', str(self.out)]
        r = subprocess.run(cmd, env=self.env, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.argv.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
