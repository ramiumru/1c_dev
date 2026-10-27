#!/usr/bin/env python3
"""Read-only fixtures for Codex skills lookup, async BSL and report-name aliases."""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import safe_apply
import safe_backup
import build_summaries

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('bsl_check', SCRIPTS / 'bsl-check.py')
bsl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bsl)


class SkillsTests(unittest.TestCase):
    def test_lookup_each_adapter_and_legacy(self):
        for layout in ('.kilo/skills', '.claude/skills', '.opencode/skills', '.agents/skills', 'skills'):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                expected = root / layout
                expected.mkdir(parents=True)
                self.assertEqual(safe_apply.find_skills_dir(root), expected)
                self.assertEqual(safe_backup.find_skills_dir(root), expected)

    def test_apply_passes_native_skills_to_both_runners_after_guard(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skills = root / '.agents/skills'
            skills.mkdir(parents=True)
            (root / '.v8-project.json').write_text(json.dumps({'databases': [{
                'id': 'test', 'type': 'file', 'path': str(root / 'fake-db'),
                'configSrc': 'projects/pet/src'}]}), encoding='utf-8')
            module = root / 'projects/pet/src/Reports/Report/Ext/ObjectModule.bsl'
            module.parent.mkdir(parents=True)
            module.write_text('// fixture', encoding='utf-8')
            report = root / 'specs/TASK-TEST/06_change_report.md'
            report.parent.mkdir(parents=True)
            report.write_text('## Изменённые файлы\n- projects/pet/src/Reports/Report/Ext/ObjectModule.bsl\n', encoding='utf-8')
            events = []
            def guard(*a, **kw):
                events.append('guard')
                return 0
            def runner(name, args, path):
                events.append(name)
                self.assertEqual(path.resolve(), skills.resolve())
                return 0
            argv = ['safe_apply.py', '--task', 'TASK-TEST', '--db', 'test', '--project-root', str(root)]
            with patch.object(sys, 'argv', argv), patch.object(safe_apply, 'run_guard', side_effect=guard), patch.object(safe_apply, 'run_skill', side_effect=runner):
                self.assertEqual(safe_apply.main(), 0)
            self.assertEqual(events, ['guard', 'db-load-xml', 'db-update'])
            # No actual PowerShell/1C command is executed by this fixture.


class AsyncTests(unittest.TestCase):
    def check_code(self, text):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'Module.bsl'
            p.write_bytes(b'\xef\xbb\xbf' + text.replace('\n', '\r\n').encode('utf-8'))
            return bsl.check_file(str(p))

    def test_async_procedures_and_functions_both_languages(self):
        for prefix, proc, end_proc, func, end_func, ret in (
            ('Асинх', 'Процедура', 'КонецПроцедуры', 'Функция', 'КонецФункции', 'Возврат'),
            ('Async', 'Procedure', 'EndProcedure', 'Function', 'EndFunction', 'Return'),
            ('аСиНх', 'пРоЦеДуРа', 'КонецПроцедуры', 'фУнКцИя', 'КонецФункции', 'Возврат')):
            with self.subTest(prefix=prefix):
                code = f'{prefix} {proc} Выполнить()\n    X = 1;\n{end_proc}\n\n{prefix} {func} Получить()\n    {ret} 1;\n{end_func}\n'
                self.assertEqual(self.check_code(code), [])

    def test_async_does_not_mask_missing_end_or_orphan(self):
        cases = (
            ('Асинх Процедура Тест()\n    X = 1;\n', 'ERROR', ''),
            ('КонецПроцедуры\n', 'ERROR', ''),
            ('// Асинх Процедура Тест()\nX = 1;\n', 'WARN', 'orphaned'),
            ('Асинх Функция Тест()\nВозврат 1;\nКонецФункции\nX = 1;\n', 'WARN', 'orphaned'))
        for code, expected_level, marker in cases:
            with self.subTest(code=code):
                self.assertTrue(any(level == expected_level and marker in message
                                    for level, _, message in self.check_code(code)))



class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ('build_summaries.py', '_root.py'):
            shutil.copyfile(SCRIPTS / name, scripts / name)
        (self.root / '.v8-project.json').write_text('{}', encoding='utf-8')
        ctx = self.root / 'context/projects/pet'
        ctx.mkdir(parents=True)
        (ctx / 'context.md').write_text('# Pet\n', encoding='utf-8')
        self.index = ctx / 'objects-index.md'
        self.report = self.root / 'projects/pet/src/Reports/Ёлка'
        self.report.mkdir(parents=True)
        (self.report / 'Ёлка.xml').write_text('<MetaDataObject><Report><Properties><Name>Ёлка</Name></Properties></Report></MetaDataObject>', encoding='utf-8')

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(self.root / 'scripts/build_summaries.py'),
                               '--project', 'pet', '--context-dir', str(self.root / 'context/projects'), *args],
                              cwd=self.root, capture_output=True, text=True, encoding='utf-8', timeout=30)

    def test_normalizes_type_not_object_name(self):
        for kind in ('Report', 'Отчет', 'Отчёт'):
            self.assertEqual(build_summaries.normalize_object_title(kind + '.Ёлка'), 'Отчет.Ёлка')
        self.assertEqual(build_summaries.normalize_object_title('Документ.Ёлка'), 'Документ.Ёлка')

    def test_cli_aliases_scan_and_existing_index_without_duplicates(self):
        for kind in ('Отчёт', 'Report', 'Отчет'):
            r = self.run_cli('--scan', '--objects', kind + '.Ёлка')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.index.read_text(encoding='utf-8').count('# Отчет.Ёлка'), 1)
        for flag in ('--objects', '--object'):
            r = self.run_cli(flag, 'Report.Ёлка')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_mixed_found_missing_is_nonzero_without_partial_index_writes(self):
        r = self.run_cli('--scan', '--objects', 'Отчёт.Ёлка,Report.Missing')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('requested-objects-not-found', r.stdout)
        self.assertFalse(self.index.exists())
        self.assertEqual(self.run_cli('--scan', '--objects', 'Report.Ёлка').returncode, 0)
        before = self.index.read_bytes()
        for flags in (('--scan', '--objects'), ('--objects',)):
            r = self.run_cli(*flags, 'Отчет.Ёлка,Отчёт.Missing')
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.index.read_bytes(), before)

    def test_old_accented_index_is_reused(self):
        self.assertEqual(self.run_cli('--scan', '--objects', 'Report.Ёлка').returncode, 0)
        self.index.write_text(self.index.read_text(encoding='utf-8').replace('Отчет.', 'Отчёт.'), encoding='utf-8')
        r = self.run_cli('--scan', '--objects', 'Отчет.Ёлка')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.index.read_text(encoding='utf-8')
        self.assertEqual(text.count('# Отчёт.Ёлка'), 1)
        self.assertNotIn('# Отчет.Ёлка', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
