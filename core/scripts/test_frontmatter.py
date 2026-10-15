#!/usr/bin/env python3
"""YAML regression tests: python core/scripts/test_frontmatter.py [-h].

Requires requirements-dev.txt and PowerShell. Tests real installer output,
including all metadata and substituted agent bodies, without contacting 1C.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from validate import _parse_frontmatter

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ('1c-do', '1c-analyst', '1c-developer', '1c-reviewer',
          '1c-applier', '1c-tools', '1c-git-reviewer')
PATHS = {
    'kilo': ('.kilo/agent', '.kilo/context', '.kilo/logs', '.kilo/skills'),
    'openworks': ('.opencode/agents', '.opencode/context', '.opencode/logs', '.opencode/skills'),
    'claude': ('.claude/agents', '.claude/context', '.claude/logs', '.claude/skills'),
}


def source_metadata(path):
    text = path.read_text(encoding='utf-8-sig').strip()
    # Some Claude source fragments include delimiters; installer strips them.
    if text.startswith('---\n'):
        text = text[4:]
    if text.endswith('\n---'):
        text = text[:-4]
    return yaml.safe_load(text)


def check_installed_agents(repo, target, tool):
    """Raise with file context on invalid YAML or installer data loss."""
    agents, context, logs, skills = PATHS[tool]
    for name in AGENTS:
        path = target / agents / (name + '.md')
        assert path.is_file(), f'{path}: missing agent'
        text = path.read_text(encoding='utf-8-sig')
        actual = _parse_frontmatter(text)
        assert actual, f'{path}: invalid YAML frontmatter'
        assert isinstance(actual.get('description'), str), f'{path}: description must be a string'
        assert actual['description'].strip(), f'{path}: empty description'
        expected = source_metadata(repo / 'adapters' / tool / 'frontmatter' / (name + '.yml'))
        assert actual == expected, f'{path}: metadata changed (model/permissions/tools included)'
        if name == '1c-developer':
            assert 'status: approved' in actual['description'], f'{path}: gate text lost'
            assert '// ++ #<TASK-ID>.' in actual['description'], f'{path}: description truncated'
        lines = text.splitlines(keepends=True)
        close = next(i for i in range(1, len(lines)) if lines[i].strip() == '---')
        body = ''.join(lines[close + 1:]).lstrip('\n')
        expected_body = (repo / 'core/agents' / (name + '.md')).read_text(encoding='utf-8-sig')
        for key, value in [('AGENTS_DIR', agents), ('CONTEXT_DIR', context),
                           ('LOGS_DIR', logs), ('SKILLS_DIR', skills)]:
            expected_body = expected_body.replace('{{' + key + '}}', value)
        assert body == expected_body, f'{path}: agent body changed'


class FrontmatterTests(unittest.TestCase):
    def test_original_colon_space_is_rejected(self):
        # Includes the second original hazard: # starts a comment in plain scalars.
        bad = '---\ndescription: gate, status: approved, // ++ #<TASK-ID>.\nmode: all\n---\nbody'
        with self.assertRaises(yaml.YAMLError):
            yaml.safe_load(bad.split('---')[1])
        self.assertEqual(_parse_frontmatter(bad), {})

    def test_quoted_description_preserves_colon_and_hash(self):
        value = 'gate, status: approved, // ++ #<TASK-ID>.'
        for newline in ('\n', '\r\n'):
            text = "\ufeff---\ndescription: '" + value + "'\nmode: all\n---\nbody"
            self.assertEqual(_parse_frontmatter(text.replace('\n', newline))['description'], value)

    def test_missing_empty_or_nonmapping_is_rejected(self):
        for text in ('body', '---\n---\nbody', '---\n- item\n---',
                     '---\ndescription: text', '---oops\ndescription: text\n---'):
            with self.subTest(text=text):
                self.assertEqual(_parse_frontmatter(text), {})

    def test_all_source_fragments(self):
        for tool in PATHS:
            for name in AGENTS:
                with self.subTest(tool=tool, agent=name):
                    data = source_metadata(ROOT / 'adapters' / tool / 'frontmatter' / (name + '.yml'))
                    self.assertIsInstance(data, dict)
                    self.assertIsInstance(data['description'], str)

    def test_real_installs_all_agents(self):
        pwsh = shutil.which('powershell') or shutil.which('pwsh')
        self.assertIsNotNone(pwsh, 'PowerShell required; installed-file tests must not silently skip')
        for tool in PATHS:
            with self.subTest(tool=tool), tempfile.TemporaryDirectory(prefix='yaml_install_') as td:
                result = subprocess.run(
                    [pwsh, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                     str(ROOT / 'install/install.ps1'), '-Tool', tool, '-Target', td],
                    cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8', timeout=180)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                check_installed_agents(ROOT, Path(td), tool)


if __name__ == '__main__':
    unittest.main(verbosity=2)
