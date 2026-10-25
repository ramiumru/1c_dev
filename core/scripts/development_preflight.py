#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only SDD checks before review; no database access or approval changes."""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from applier_guard import _parse_yaml_block, _validate_iso_timestamp
from plan_parser import parse_plan_from_file
from scope_hash import compute_scope_hash


def check_metadata(text: str, label: str) -> tuple[dict, list[str]]:
    """Check the simple scalar contract consumed by the existing apply guard."""
    blocks = re.findall(r"```yaml\s*\r?\n(.*?)```", text, re.S)
    errors = []
    if not blocks:
        return {}, [f"{label}: отсутствует машиночитаемый yaml-блок"]
    keys = set()
    for line in blocks[0].splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([a-z_]+):\s*(.*?)\s*", line)
        if not match or "#" in line or match.group(2).startswith(("|", ">", "{", "[", "&", "*", "!")):
            errors.append(f"{label}: нужно простое поле без комментариев: {line}")
            continue
        key = match.group(1)
        if key in keys:
            errors.append(f"{label}: повтор поля {key}")
        keys.add(key)
    return _parse_yaml_block(text), errors


def check(root: Path, task: str, config_src: str) -> tuple[list[str], list[str]]:
    """Validate developer artifacts. Existing review/apply gates remain mandatory."""
    errors, notes = [], []
    base = root / 'specs' / task
    try:
        spec_text = (base / '03_solution_spec.md').read_text(encoding='utf-8-sig')
        report_text = (base / '06_change_report.md').read_text(encoding='utf-8-sig')
    except OSError as exc:
        return [f"SDD: {exc}"], notes
    spec, problems = check_metadata(spec_text, 'spec')
    errors.extend(problems)
    report, problems = check_metadata(report_text, 'report')
    errors.extend(problems)
    if spec.get('status') != 'approved':
        errors.append('spec: требуется status: approved; разработчик не меняет approval')
    if spec.get('risk') not in ('low', 'medium', 'high'):
        errors.append('spec: risk должен быть low/medium/high')
    if not spec.get('approved_by') or not _validate_iso_timestamp(spec.get('approved_at', '')):
        errors.append('spec: нужны approved_by и approved_at ISO 8601 с timezone')
    version = str(spec.get('spec_version', ''))
    if not version.isdigit() or int(version) < 1 or report.get('spec_version') != version:
        errors.append('SDD: нужна одинаковая положительная spec_version в spec и report')
    digest = compute_scope_hash(spec_text)
    if not digest or any(meta.get('scope_hash') != digest for meta in (spec, report)):
        errors.append('SDD: scope_hash spec/report не совпадает с пересчитанным')
    files, problems = parse_plan_from_file(base / '06_change_report.md', config_src, root)
    errors.extend(problems)
    source = (root / config_src).resolve()
    for name in files:
        path = (root / name).resolve()
        if source not in path.parents or not path.is_file():
            errors.append(f'план: не обычный файл внутри configSrc: {name}')
            continue
        if path.suffix.lower() != '.xml':
            continue
        try:
            tree = ET.parse(path).getroot()
        except (ET.ParseError, OSError) as exc:
            errors.append(f'XML: {name}: {exc}')
            continue
        # Top-level metadata descriptors in a hierarchical dump are siblings of
        # the same-name directory, e.g. CommonCommands/Name.xml + Name/Ext/...
        if tree.tag.rsplit('}', 1)[-1] != 'MetaDataObject':
            continue  # form, rights, templates and other nested XML have other roots
        objects = list(tree)
        if len(objects) != 1:
            continue  # semantic validation belongs to the relevant *-validate skill
        obj = objects[0]
        kind = obj.tag.rsplit('}', 1)[-1]
        collection = kind + 's'
        relative = path.relative_to(source)
        if not relative.parts or relative.parts[0] != collection:
            continue  # nested forms/commands are not independent top-level objects
        props = next((n for n in obj if n.tag.rsplit('}', 1)[-1] == 'Properties'), None)
        obj_name = next((n.text for n in props if n.tag.rsplit('}', 1)[-1] == 'Name'), None) if props is not None else None
        if obj_name and relative.as_posix() != f'{collection}/{obj_name}.xml':
            errors.append(f'XML layout: {name}; ожидается {collection}/{obj_name}.xml')
    notes.append(f'Проверено файлов плана: {len(files)}; БД и review не запускались')
    return errors, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path.cwd())
    parser.add_argument('--task', required=True)
    parser.add_argument('--config-src', required=True, help='Project-relative configSrc from the chosen project profile')
    args = parser.parse_args()
    if not re.fullmatch(r'TASK-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', args.task):
        parser.error('invalid TASK-ID')
    config = args.config_src.replace('\\', '/')
    if not config.startswith('projects/') or '..' in config.split('/') or any(c in config for c in '*?:'):
        parser.error('configSrc must be a concrete project-relative projects/... path')
    errors, notes = check(args.project_root.resolve(), args.task, config.rstrip('/'))
    for note in notes:
        print(note)
    for error in errors:
        print(f'FAIL {error}')
    print(f'development-preflight: FAIL={len(errors)}')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
