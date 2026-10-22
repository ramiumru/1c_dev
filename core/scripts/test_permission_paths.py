#!/usr/bin/env python3
"""Real installer regression: scoped Kilo permissions with and without Git.

Uses disposable directories/repositories only; does not run a live Kilo model.
python core/scripts/test_permission_paths.py [-h]
"""
import fnmatch
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which('pwsh') or shutil.which('powershell')


def permission_root(target):
    """Independent model of Kilo Project.fromDirectory (git-common-dir or /)."""
    target = Path(os.path.abspath(target))
    for candidate in (target, *target.parents):
        if (candidate / '.git').exists():
            if shutil.which('git'):
                result = subprocess.run(['git', '-C', str(candidate), 'rev-parse', '--git-common-dir'],
                                        capture_output=True, text=True, encoding='utf-8')
                if result.returncode == 0:
                    return os.path.dirname(os.path.abspath(os.path.join(candidate, result.stdout.strip())))
            return str(candidate)
    return target.anchor


def action(metadata, permission, filename):
    rules = metadata['permission'].get(permission)
    if not isinstance(rules, dict):
        return rules or metadata['permission'].get('*', 'ask')
    result = metadata['permission'].get('*', 'ask')
    for pattern, value in rules.items():
        target = filename.replace('\\', '/')
        if os.name == 'nt':
            target, pattern = target.casefold(), pattern.casefold()
        if fnmatch.fnmatchcase(target, pattern):
            result = value
    return result


class PermissionPathTests(unittest.TestCase):
    def install(self, target, update=False):
        self.assertIsNotNone(PWSH, 'PowerShell is required; do not silently skip installer tests')
        cmd = [PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
               str(ROOT / 'install/install.ps1'), '-Tool', 'kilo', '-Target', str(target)]
        if update:
            cmd += ['-Mode', 'update']
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        from test_frontmatter import check_installed_agents
        check_installed_agents(ROOT, target, 'kilo')

    def metadata(self, target, agent):
        from validate import _parse_frontmatter
        return _parse_frontmatter((target / '.kilo/agent' / (agent + '.md')).read_text(encoding='utf-8-sig'))

    def assert_scope(self, target):
        base = permission_root(target)
        def relative(p):
            return os.path.relpath(os.path.abspath(p), base).replace(os.sep, '/')
        analyst = self.metadata(target, '1c-analyst')
        for artifact in ('01_context.md', '03_solution_spec.md', '05_test_scenarios.md'):
            self.assertEqual(action(analyst, 'edit', relative(target / 'specs/TASK-test' / artifact)), 'allow')
        self.assertEqual(action(analyst, 'edit', relative(target / '.kilo/logs/1c-analyst/test.md')), 'allow')
        self.assertEqual(action(analyst, 'edit', relative(target / '.kilo/context/projects/test/context.md')), 'allow')
        for filename in ('specs/TASK-test/03_solution_spec.md', 'projects/test/src/Module.bsl',
                         '.kilo/logs/1c-analyst/test.md', '.kilo/context/projects/test/context.md'):
            sibling = relative(target.parent / 'neighbour' / filename)
            self.assertNotEqual(action(analyst, 'edit', sibling), 'allow', sibling)
        self.assertEqual(action(analyst, 'edit', relative(target / 'projects/test/src/Module.bsl')), 'deny')
        self.assertEqual(action(analyst, 'edit', relative(target / '.v8-project.json')), 'deny')
        if os.path.abspath(base) != os.path.abspath(target):
            self.assertEqual(action(analyst, 'edit', relative(Path(base) / 'specs/TASK-test/03_solution_spec.md')), 'deny')
        self.assertEqual(action(analyst, 'edit', relative(target.parent / (target.name + '-other') / 'specs/TASK-test/03_solution_spec.md')), 'deny')
        developer = self.metadata(target, '1c-developer')
        self.assertEqual(action(developer, 'edit', relative(target / 'projects/test/src/Module.bsl')), 'allow')
        self.assertEqual(action(developer, 'edit', relative(target.parent / 'neighbour/projects/test/src/Module.bsl')), 'deny')
        reviewer = self.metadata(target, '1c-reviewer')
        self.assertEqual(action(reviewer, 'edit', relative(target / 'specs/TASK-test/03_solution_spec.md')), 'deny')
        do = self.metadata(target, '1c-do')
        self.assertEqual(action(do, 'edit', relative(target / 'specs/TASK-test/00_request.md')), 'allow')
        self.assertEqual(action(do, 'edit', relative(target.parent / 'neighbour/specs/TASK-test/00_request.md')), 'deny')
        git_reviewer = self.metadata(target, '1c-git-reviewer')
        self.assertEqual(action(git_reviewer, 'read', relative(target / 'projects/test/src/Module.bsl')), 'allow')
        for agent in ('1c-do', '1c-analyst', '1c-reviewer', '1c-git-reviewer', '1c-developer', '1c-applier', '1c-tools'):
            for permission in ('read', 'edit'):
                rules = self.metadata(target, agent)['permission'].get(permission, {})
                if isinstance(rules, dict):
                    self.assertFalse(any(key.startswith('**/') for key in rules), (agent, permission))

    def git(self, cwd, *args):
        result = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_non_git_workspace_and_update(self):
        with tempfile.TemporaryDirectory(prefix='kilo_non_git_') as td:
            target = Path(td) / 'Workspace Пример с пробелом'
            self.install(target)
            self.assertFalse((target / '.git').exists())
            self.assert_scope(target)
            before = {p.name: p.read_bytes() for p in (target / '.kilo/agent').glob('*.md')}
            self.install(target, update=True)
            self.assertEqual(before, {p.name: p.read_bytes() for p in (target / '.kilo/agent').glob('*.md')})
            self.assert_scope(target)

    def test_wildcard_target_is_rejected(self):
        self.assertIsNotNone(PWSH, 'PowerShell required')
        with tempfile.TemporaryDirectory(prefix='kilo_bad_path_') as td:
            target = Path(td) / 'unsafe*workspace'
            result = subprocess.run([PWSH, '-NoProfile', '-File', str(ROOT / 'install/install.ps1'),
                                     '-Tool', 'kilo', '-Target', str(target)],
                                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(target.exists())

    def test_git_root_and_nested_workspace(self):
        with tempfile.TemporaryDirectory(prefix='kilo_git_') as td:
            repo = Path(td)
            self.git(repo, 'init')
            self.install(repo)
            self.assert_scope(repo)
            nested = repo / 'nested/workspace'
            self.install(nested)
            self.assert_scope(nested)

    def test_linked_git_worktree(self):
        with tempfile.TemporaryDirectory(prefix='kilo_worktree_') as td:
            repo = Path(td) / 'main'
            repo.mkdir()
            self.git(repo, 'init')
            self.git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                     'commit', '--allow-empty', '-m', 'Fixture only')
            target = Path(td) / 'linked'
            self.git(repo, 'worktree', 'add', '-b', 'fixture-linked', str(target))
            self.install(target)
            self.assert_scope(target)


if __name__ == '__main__':
    unittest.main(verbosity=2)
