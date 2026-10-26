#!/usr/bin/env python3
"""Codex real-installer regressions; --source-only checks source contracts on Linux."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from codex_layout import AGENT_NAMES, check_layout, load_toml

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which('pwsh') or shutil.which('powershell')


def digest(data):
    return hashlib.sha256(data).hexdigest().upper()


def check_installed(target):
    target = Path(target)
    errors = check_layout(target)
    if errors:
        raise AssertionError('\n'.join(errors))
    replacements = {'{{CONTEXT_DIR}}': 'context', '{{LOGS_DIR}}': 'logs',
                    '{{SKILLS_DIR}}': '.agents/skills', '{{AGENTS_DIR}}': 'agents'}
    runtime = (ROOT / 'adapters/codex/runtime.md').read_bytes().decode('utf-8-sig')
    roles = json.loads((ROOT / 'adapters/codex/roles.json').read_text(encoding='utf-8'))
    for name in AGENT_NAMES:
        body = (ROOT / 'core/agents' / (name + '.md')).read_bytes().decode('utf-8-sig')
        for old, new in replacements.items():
            body = body.replace(old, new)
        actual = (target / 'agents' / (name + '.md')).read_bytes().decode('utf-8')
        if actual != body:
            raise AssertionError(f'{name}: changed canonical role instructions')
        native = load_toml(target / '.codex/agents' / (name + '.toml'))
        if native['description'] != roles[name] or native['developer_instructions'] != runtime + '\n\n' + body:
            raise AssertionError(f'{name}: wrong TOML instructions/escaping')
        if set(native) - {'name', 'description', 'developer_instructions', 'features'}:
            raise AssertionError(f'{name}: overrides model/permissions/provider/MCP')
    # Parse every installed skill and check that its command points to real files.
    for source in (ROOT / 'core/skills').glob('*/SKILL.md'):
        installed = target / '.agents/skills' / source.parent.name / 'SKILL.md'
        raw = installed.read_bytes()
        if raw.startswith(b'\xef\xbb\xbf'):
            raise AssertionError(f'{source.parent.name}: BOM before native frontmatter')
        text = raw.decode('utf-8')
        meta = yaml.safe_load(text.split('---', 2)[1])
        if meta.get('name') != source.parent.name or not meta.get('description'):
            raise AssertionError(f'{source.parent.name}: invalid discovery metadata')
        if '{{SKILL' in text:
            raise AssertionError(f'{source.parent.name}: unresolved command path')
        if (source.parent / 'scripts').is_dir():
            for script in (source.parent / 'scripts').iterdir():
                if script.is_file() and not (installed.parent / 'scripts' / script.name).is_file():
                    raise AssertionError(f'missing skill script {script.name}')


class SourceTests(unittest.TestCase):
    def test_configuration_example_uses_current_schema(self):
        data = load_toml(ROOT / 'adapters/codex/config.toml.example')
        self.assertEqual(data, {'approval_policy': 'on-request', 'sandbox_mode': 'workspace-write'})

    def test_all_seven_roles_declared(self):
        roles = json.loads((ROOT / 'adapters/codex/roles.json').read_text(encoding='utf-8'))
        self.assertEqual(set(roles), set(AGENT_NAMES))
        self.assertTrue(all(isinstance(v, str) and v for v in roles.values()))


class InstalledTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise AssertionError('PowerShell required; use --source-only explicitly for source contracts')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex_адаптер_')
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / 'pet project'
        self.target.mkdir()

    def install(self, *extra):
        result = subprocess.run([PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                 str(ROOT / 'install/install.ps1'), '-Tool', 'codex',
                                 '-Target', str(self.target), *extra], cwd=ROOT,
                                capture_output=True, text=True, encoding='utf-8', timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_clean_install_native_files_and_read_only_skill_command(self):
        self.install()
        check_installed(self.target)
        self.assertFalse((self.target / '.codex/config.toml').exists())
        self.assertFalse((self.target / 'skills').exists())
        self.assertFalse((self.target / '.v8-project.json').exists())
        command = self.target / '.agents/skills/meta-info/scripts/meta-info.ps1'
        xml = self.target / 'fixture.xml'
        xml.write_text('<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"><CommonCommand><Properties><Name>Example</Name></Properties></CommonCommand></MetaDataObject>', encoding='utf-8')
        result = subprocess.run([PWSH, '-NoProfile', '-File', str(command), '-ObjectPath', str(xml)],
                                cwd=self.target, capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for script in ('doctor.py', 'validate.py'):
            args = [os.sys.executable, str(self.target / 'scripts' / script)]
            if script == 'validate.py':
                args.append('--skip-smoke')
            env = dict(os.environ, PYTHONPYCACHEPREFIX=str(Path(self.temp.name) / 'pycache'))
            result = subprocess.run(args, cwd=self.target, env=env, capture_output=True,
                                    text=True, encoding='utf-8', timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout[-6000:] + result.stderr)
        manifest = json.loads((self.target / '.ai-rules.json').read_bytes().decode('utf-8-sig'))
        entries = {f['path']: f for f in manifest['files']}
        for rel in ('.codex/agents/1c-developer.toml', '.agents/skills/meta-info/SKILL.md'):
            self.assertEqual(entries[rel]['installedHash'], digest((self.target / rel).read_bytes()))

    def test_user_config_and_modifications_survive_repeated_updates(self):
        self.install()
        config = self.target / '.codex/config.toml'
        config.write_text('model = "personal-model"\n', encoding='utf-8')
        paths = ['AGENTS.md', '.codex/agents/1c-developer.toml',
                 'agents/1c-developer.md', '.agents/skills/meta-info/SKILL.md']
        for rel in paths:
            p = self.target / rel
            p.write_bytes(p.read_bytes() + b'\n# personal change\n')
        before = {rel: (self.target / rel).read_bytes() for rel in paths}
        for _ in range(2):
            self.install('-Mode', 'update')
            self.assertEqual(before, {rel: (self.target / rel).read_bytes() for rel in paths})
            self.assertEqual(config.read_text(), 'model = "personal-model"\n')
        self.install('-Mode', 'update', '-Force')
        self.assertTrue((self.target / '.codex/agents/1c-developer.toml.bak').exists())
        self.assertEqual(config.read_text(), 'model = "personal-model"\n')
        check_installed(self.target)

    def test_preexisting_files_are_not_adopted_as_public_content(self):
        values = {'AGENTS.md': b'Personal instructions', '.codex/agents/1c-developer.toml': b'personal = true\n',
                  '.agents/skills/meta-info/SKILL.md': b'Personal skill'}
        for rel, raw in values.items():
            path = self.target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.install()
        self.install('-Mode', 'update')
        self.install('-Mode', 'update')
        for rel, raw in values.items():
            self.assertEqual((self.target / rel).read_bytes(), raw)

    def test_legacy_install_migration_keeps_old_and_unrelated_files(self):
        legacy = self.target / 'skills/meta-info/SKILL.md'
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'Legacy resource')
        guidance = self.target / 'AGENTS.md'
        guidance.write_bytes(b'Legacy managed instructions')
        (self.target / '.ai-rules.json').write_text(json.dumps({'files': [
            {'path': 'AGENTS.md', 'installedHash': digest(guidance.read_bytes())},
            {'path': 'skills/meta-info/SKILL.md', 'installedHash': digest(legacy.read_bytes())}]}))
        custom = self.target / '.codex/agents/personal.toml'
        custom.parent.mkdir(parents=True)
        custom.write_bytes(b'personal = true\n')
        self.install('-Mode', 'update')
        check_installed(self.target)
        self.assertEqual(legacy.read_bytes(), b'Legacy resource')
        self.assertEqual(custom.read_bytes(), b'personal = true\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true')
    args = parser.parse_args()
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(SourceTests))
    if not args.source_only:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(InstalledTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
