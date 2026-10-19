#!/usr/bin/env python3
"""Git review contract and multi-commit evidence tests (no network/LLM).

Checks instruction/permission regressions, not compliance of a live model.
Run: python core/scripts/test_git_review_contract.py [-h]
"""
import fnmatch
import tempfile
import unittest
from pathlib import Path

import yaml
from test_git_reviewer import run_git, commit_file, wrap

ROOT = Path(__file__).resolve().parents[2]


class ReviewContractTests(unittest.TestCase):
    def test_plain_report_and_complete_addresses(self):
        text = (ROOT / 'core/agents/1c-git-reviewer.md').read_text()
        self.assertNotIn('```', text)
        for marker in ('без fenced code blocks', 'Module.bsl', 'полный адрес',
                       'Конфигуратор:', 'Файл:', 'Место:', 'review_sha',
                       'Номера строк — 1-based', 'Каждое замечание самодостаточно',
                       'Документ.<Имя>.Форма.<ИмяФормы>.Модуль'):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        self.assertIn('Применимость', (ROOT / 'core/rules/review-checklist.md').read_text())

    def test_mcp_denied_and_local_capabilities_preserved(self):
        for tool in ('kilo', 'openworks'):
            data = yaml.safe_load((ROOT / 'adapters' / tool /
                                   'frontmatter/1c-git-reviewer.yml').read_text(encoding='utf-8-sig'))
            permission = data['permission']
            self.assertEqual(permission['*'], 'deny')
            for name in ('v8std_lookup', 'github_search', 'metadata_get', 'code_search',
                         'standards_lookup', 'unknown_mcp_tool'):
                actions = [action for pattern, action in permission.items()
                           if isinstance(action, str) and fnmatch.fnmatchcase(name, pattern)]
                self.assertEqual(actions[-1], 'deny', (tool, name))
            self.assertEqual(permission['bash']['python scripts/git_readonly.py *'], 'allow')
            self.assertEqual(permission['read']['projects/**'], 'allow')
            self.assertEqual(permission['task'], 'deny')
        claude = (ROOT / 'adapters/claude/frontmatter/1c-git-reviewer.yml').read_text(encoding='utf-8-sig')
        self.assertIn('tools: Read, Glob, Grep, Bash', claude)
        text = (ROOT / 'core/agents/1c-git-reviewer.md').read_text()
        for marker in ('**MCP запрещён.**', 'включая discovery/list',
                       'MCP-шаги', 'полный модуль', 'другими коммитами задачи',
                       'merge-base --is-ancestor', 'невозможно проверить'):
            self.assertIn(marker, text)

    def test_call_and_definition_in_separate_commits(self):
        with tempfile.TemporaryDirectory(prefix='review_evidence_') as td:
            repo = Path(td)
            run_git(repo, 'init')
            run_git(repo, 'config', 'user.name', 'Fixture')
            run_git(repo, 'config', 'user.email', 'fixture@example.invalid')
            # Fixture commits are confined to this disposable test repository.
            path = 'Documents/Example/Forms/Main/Ext/Form/Module.bsl'
            old = ('Процедура Выполнить()\n    Рассчитать();\n'
                   '    УстаревшийВызов();\nКонецПроцедуры\n')
            call_sha = commit_file(repo, path, old, '#731 call')
            commit_file(repo, 'Documents/Other/Ext/Module.bsl', '// unrelated\n', '#999 other task')
            helper_path = 'CommonModules/ExampleShared/Ext/Module.bsl'
            (repo / helper_path).parent.mkdir(parents=True, exist_ok=True)
            (repo / helper_path).write_text('Функция ОбщийРасчёт() Экспорт\nКонецФункции\n', encoding='utf-8')
            run_git(repo, 'add', helper_path)
            final = ('Процедура Выполнить()\n    Рассчитать();\nКонецПроцедуры\n\n'
                     'Процедура Рассчитать()\nКонецПроцедуры\n')
            tip = commit_file(repo, path, final, '#731 definition and cleanup')
            history = wrap(repo, 'log', tip, '--oneline', '--grep=#731')
            self.assertEqual(history.returncode, 0, history.stderr)
            self.assertEqual(len(history.stdout.splitlines()), 2)
            # A later unrelated branch must not establish presence in the reviewed ref.
            run_git(repo, 'checkout', '-b', 'unrelated')
            foreign = commit_file(repo, 'CommonModules/Other/Ext/Module.bsl',
                                  '// чужой код\n', '#731 candidate from another branch')
            ancestor = wrap(repo, 'merge-base', '--is-ancestor', foreign, tip)
            self.assertEqual(ancestor.returncode, 1)
            (repo / path).write_text('// dirty working tree; no definitions\n', encoding='utf-8')
            blob = wrap(repo, 'show', tip + ':' + path)
            self.assertEqual(blob.returncode, 0, blob.stderr)
            self.assertEqual(blob.stdout, final)
            self.assertEqual(blob.stdout.splitlines()[4], 'Процедура Рассчитать()')
            helper = wrap(repo, 'show', tip + ':' + helper_path)
            self.assertEqual(helper.returncode, 0, helper.stderr)
            self.assertIn('Функция ОбщийРасчёт() Экспорт', helper.stdout)
            self.assertNotEqual(wrap(repo, 'show', call_sha + ':' + helper_path).returncode, 0)
            files = wrap(repo, 'show', '--name-only', tip)
            self.assertIn(helper_path, files.stdout)
            self.assertIn(path, files.stdout)
            historical = wrap(repo, 'show', call_sha + ':' + path)
            self.assertNotIn('Процедура Рассчитать()', historical.stdout)
            removed = wrap(repo, 'show', tip + '~1:' + path)
            self.assertEqual(removed.stdout.splitlines()[2], '    УстаревшийВызов();')
            self.assertNotIn('УстаревшийВызов', blob.stdout)
            diff = wrap(repo, 'diff', '-U0', tip + '~1', tip, '--', path)
            self.assertEqual(diff.returncode, 0, diff.stderr)
            self.assertIn('-    УстаревшийВызов();', diff.stdout)
            self.assertEqual((repo / path).read_text(encoding='utf-8'),
                             '// dirty working tree; no definitions\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
