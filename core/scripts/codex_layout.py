#!/usr/bin/env python3
"""Read-only checks for installed Codex discovery files. No Codex service calls."""
from pathlib import Path

AGENT_NAMES = ('1c-do', '1c-analyst', '1c-developer', '1c-reviewer',
               '1c-applier', '1c-tools', '1c-git-reviewer')


def load_toml(path):
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError as exc:
            raise ImportError('TOML checks require Python 3.11+ or tomli; install requirements-dev.txt') from exc
    return tomllib.loads(Path(path).read_text(encoding='utf-8'))


def check_layout(root):
    root = Path(root)
    errors = []
    if not (root / 'AGENTS.md').is_file():
        errors.append('Codex: missing AGENTS.md')
    for name in AGENT_NAMES:
        path = root / '.codex' / 'agents' / (name + '.toml')
        try:
            data = load_toml(path)
        except (OSError, ValueError) as exc:
            errors.append(f'Codex: {path.name}: {exc}')
            continue
        if data.get('name') != name or not all(isinstance(data.get(k), str) and data[k].strip()
                                              for k in ('description', 'developer_instructions')):
            errors.append(f'Codex: {path.name}: invalid name/description/developer_instructions')
        if not (root / 'agents' / (name + '.md')).is_file():
            errors.append(f'Codex: missing agents/{name}.md')
    if not any((root / '.agents' / 'skills').glob('*/SKILL.md')):
        errors.append('Codex: missing native .agents/skills/*/SKILL.md')
    return errors
