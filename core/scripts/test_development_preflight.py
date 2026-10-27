#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression fixtures for early SDD checks, including the three reported failures."""
from __future__ import annotations
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from development_preflight import check, check_metadata
from scope_hash import compute_scope_hash


class DevelopmentPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = 'projects/example/src'
        self.task = 'TASK-TEST'
        self.base = self.root / 'specs' / self.task
        self.base.mkdir(parents=True)
        self.name = self.src + '/CommonCommands/Create.xml'
        self.xml = '<MetaDataObject xmlns="urn:test"><CommonCommand><Properties><Name>Create</Name></Properties></CommonCommand></MetaDataObject>'
        self.put(self.name, self.xml)
        self.spec = '''```yaml
status: approved
risk: low
approved_by: user
approved_at: 2026-10-25T12:00:00+03:00
spec_version: 1
scope_hash: HASH
```
## Границы изменения
One command only.
## Затрагиваемые файлы
CommonCommands/Create.xml
'''
        self.digest = compute_scope_hash(self.spec)
        self.spec = self.spec.replace('HASH', self.digest)
        self.report = f'''```yaml
scope_hash: {self.digest}
spec_version: 1
```
## Изменённые файлы
- `{self.name}` — ОбщаяКоманда.Create
'''
        self.save()

    def put(self, name, text):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')

    def save(self):
        self.put(f'specs/{self.task}/03_solution_spec.md', self.spec)
        self.put(f'specs/{self.task}/06_change_report.md', self.report)

    def errors(self):
        return check(self.root, self.task, self.src)[0]

    def test_valid_without_review_or_database_and_no_writes(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.errors(), [])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_inline_approval_comment(self):
        self.spec = self.spec.replace('+03:00', '+03:00 # human approved')
        self.save()
        self.assertTrue(any('без комментариев' in e for e in self.errors()))

    def test_mixed_full_and_abbreviated_paths_not_silently_ignored(self):
        self.report += '- `CommonCommands/{A,B}.xml` — shortened\n'
        self.save()
        self.assertTrue(any('полный путь' in e for e in self.errors()))

    def test_wrong_nested_top_level_descriptor(self):
        nested = self.src + '/CommonCommands/Create/Create.xml'
        self.put(nested, self.xml)
        self.report = self.report.replace(self.name, nested)
        self.save()
        self.assertTrue(any('XML layout' in e for e in self.errors()))

    def test_nested_form_and_command_modules_allowed(self):
        form = self.src + '/Documents/Invoice/Forms/Main.xml'
        module = self.src + '/CommonCommands/Create/Ext/CommandModule.bsl'
        self.put(form, '<MetaDataObject><Form><Properties><Name>Main</Name></Properties></Form></MetaDataObject>')
        self.put(module, '// module')
        self.report += f'- `{form}` — форма\n- `{module}` — модуль\n'
        self.save()
        self.assertEqual(self.errors(), [])

    def test_nested_subsystem_descriptors(self):
        for relative in ('Subsystems/Child.xml',
                         'Subsystems/Parent/Subsystems/Child.xml',
                         'Subsystems/Parent/Subsystems/Middle/Subsystems/Child.xml'):
            with self.subTest(path=relative):
                name = self.src + '/' + relative
                self.put(name, self.xml.replace('CommonCommand', 'Subsystem').replace('Create', 'Child'))
                self.report = self.report.replace(self.name, name)
                # Reset the plan explicitly for each depth.
                prefix = self.report.split('## Изменённые файлы')[0]
                self.report = prefix + '## Изменённые файлы\n- `' + name + '` — Подсистема.Child\n'
                self.save()
                self.assertEqual(self.errors(), [])

    def test_bad_subsystem_leaf_and_container_are_still_rejected(self):
        for relative in ('Subsystems/Parent/Child.xml',
                         'Subsystems/Parent/Subsystems/Wrong.xml',
                         'Subsystems/Parent/Subsystems/Child/Child.xml'):
            with self.subTest(path=relative):
                name = self.src + '/' + relative
                self.put(name, self.xml.replace('CommonCommand', 'Subsystem').replace('Create', 'Child'))
                prefix = self.report.split('## Изменённые файлы')[0]
                self.report = prefix + '## Изменённые файлы\n- `' + name + '` — Подсистема.Child\n'
                self.save()
                self.assertTrue(any('XML layout' in e for e in self.errors()))

    def test_bad_xml(self):
        self.put(self.name, '<MetaDataObject>')
        self.assertTrue(any('XML:' in e for e in self.errors()))

    def test_missing_file(self):
        (self.root / self.name).unlink()
        self.assertTrue(any('отсутствует' in e for e in self.errors()))

    def test_scope_drift(self):
        self.spec = self.spec.replace('One command only.', 'Another scope.')
        self.save()
        self.assertTrue(any('scope_hash' in e for e in self.errors()))

    def test_report_version_drift(self):
        self.report = self.report.replace('spec_version: 1', 'spec_version: 2')
        self.save()
        self.assertTrue(any('spec_version' in e for e in self.errors()))

    def test_timestamp_without_timezone(self):
        self.spec = self.spec.replace('+03:00', '')
        self.save()
        self.assertTrue(any('timezone' in e for e in self.errors()))

    def test_draft_approval(self):
        self.spec = self.spec.replace('status: approved', 'status: draft')
        self.save()
        self.assertTrue(any('status: approved' in e for e in self.errors()))

    def test_duplicate_and_nested_metadata(self):
        for text in ('status: approved\nstatus: draft', 'status: approved\n  risk: low', 'status: |\napproved'):
            with self.subTest(text=text):
                self.assertTrue(check_metadata('```yaml\n' + text + '\n```', 'spec')[1])

    def test_cli(self):
        cmd = [sys.executable, str(Path(__file__).with_name('development_preflight.py')),
               '--project-root', str(self.root), '--task', self.task, '--config-src', self.src]
        good = subprocess.run(cmd, capture_output=True, encoding='utf-8')
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
        self.spec = self.spec.replace('+03:00', ' # comment')
        self.save()
        bad = subprocess.run(cmd, capture_output=True, encoding='utf-8')
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        invalid = subprocess.run(cmd[:-1] + ['../other'], capture_output=True, encoding='utf-8')
        self.assertEqual(invalid.returncode, 2)


if __name__ == '__main__':
    unittest.main()
