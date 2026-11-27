#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single rule for choosing the 1C platform executable for one database.

Order (same for safe_apply, safe_backup and the repository wrapper):
  1. databases[].v8path of the selected database;
  2. root v8path of .v8-project.json;
  3. nothing — the skill keeps its previous behaviour (project lookup / auto-select).

An explicitly configured path (1 or 2) must point to an existing 1cv8/ibcmd executable or
to a bin directory that contains 1cv8.exe/1cv8; otherwise a clear error is returned and no
other installed release is substituted. The chosen value is passed unchanged as -V8Path.
"""
from __future__ import annotations

from pathlib import Path

EXECUTABLES = ('1cv8.exe', '1cv8')


def resolve_v8path(cfg: dict, db: dict, check_exists: bool = True) -> tuple[str, str]:
    """Return (path, source) where source is 'database', 'root' or '' (not configured).
    Raises ValueError for an invalid explicit choice. check_exists=False keeps only the
    priority and syntax checks (repository dry-run on a machine without the platform)."""
    for source, owner in (('database', db), ('root', cfg)):
        if not isinstance(owner, dict) or 'v8path' not in owner or owner.get('v8path') is None:
            continue
        value = owner.get('v8path')
        if not isinstance(value, str):
            raise ValueError(f"v8path ({_label(source, db)}) must be a string")
        value = value.strip()
        if not value:
            continue  # empty = not configured, keep the previous fallback
        _validate(value, source, db, check_exists)
        return value, source
    return '', ''


def _label(source: str, db: dict) -> str:
    return f"databases[{db.get('id')!r}].v8path" if source == 'database' else 'root v8path'


def _validate(value: str, source: str, db: dict, check_exists: bool = True) -> None:
    label = _label(source, db)
    if any(c == '"' or ord(c) < 32 for c in value):
        raise ValueError(f'{label}: invalid character (quote/control) in {value!r}')
    if not check_exists:
        return
    path = Path(value)
    if path.is_dir():
        if not any((path / exe).is_file() for exe in EXECUTABLES):
            raise ValueError(f'{label}: no 1cv8 executable in {value!r}; '
                             'another installed release is NOT substituted automatically')
        return
    if not path.is_file():
        raise ValueError(f'{label}: platform not found at {value!r}; '
                         'another installed release is NOT substituted automatically')
