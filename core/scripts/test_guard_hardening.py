#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регрессии аудита 2026-11: потоковый SHA-256 backup, запрет само-ревью,
fail-closed для профиля базы с полем extension."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import applier_guard
import safe_backup
from scope_hash import compute_scope_hash


SPEC_BODY = """# Spec

## Границы изменения
Только комментарий в модуле.

## Затрагиваемые файлы
- projects/pet/src/CommonModules/M/Ext/Module.bsl
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class StreamingHashTests(unittest.TestCase):
    def test_chunked_hash_equals_full_hash(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "big.dt"
            data = b"0123456789abcdef" * 200_000  # > 1 chunk
            p.write_bytes(data)
            expected = hashlib.sha256(data).hexdigest()
            self.assertEqual(applier_guard._sha256_file(p, chunk=4096), expected)
            self.assertEqual(safe_backup._sha256_file(p, chunk=4096), expected)


class ReviewerIndependenceTests(unittest.TestCase):
    def _guard(self, root: Path, reviewed_by: str) -> applier_guard.Guard:
        h = compute_scope_hash(SPEC_BODY)
        meta = ("```yaml\nstatus: approved\nrisk: low\napproved_by: user\n"
                "approved_at: 2026-11-20T10:00:00+03:00\nspec_version: 1\n"
                f"scope_hash: {h}\n```\n")
        _write(root / "specs/TASK-1/03_solution_spec.md", meta + SPEC_BODY)
        _write(root / "specs/TASK-1/06_change_report.md",
               f"```yaml\nspec_version: 1\nscope_hash: {h}\n```\n")
        _write(root / "pilot-control/TASK-1/review.md",
               f"```yaml\nverdict: approved\nreviewed_by: {reviewed_by}\n"
               f"reviewed_at: 2026-11-20T11:00:00+03:00\nspec_version: 1\nscope_hash: {h}\n```\n")
        return applier_guard.Guard(root / ".v8-project.json", root / "specs",
                                   root / "pilot-control", project_root=root)

    def test_self_review_roles_rejected(self):
        for role in ("1c-developer", "1C-Developer", "1c-analyst", "1c-do", "1c-applier"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as td:
                g = self._guard(Path(td), role)
                g.check_sdd("TASK-1")
                self.assertTrue(any("само-ревью" in f for f in g.failures), g.failures)

    def test_reviewer_role_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._guard(Path(td), "1c-reviewer")
            g.check_sdd("TASK-1")
            self.assertEqual(g.failures, [])


class ExtensionProfileTests(unittest.TestCase):
    def test_extension_field_blocks_apply(self):
        g = applier_guard.Guard(Path("x"), Path("s"), Path("c"))
        cfg = {"databases": [{"id": "t", "type": "file", "path": "db", "environment": "test",
                              "password_mode": "none", "extension": "Ext1"}]}
        g.check_environment(cfg, "t")
        self.assertTrue(any("extension" in f for f in g.failures), g.failures)

    def test_main_configuration_profile_passes(self):
        g = applier_guard.Guard(Path("x"), Path("s"), Path("c"))
        cfg = {"databases": [{"id": "t", "type": "file", "path": "db", "environment": "test",
                              "password_mode": "none"}]}
        g.check_environment(cfg, "t")
        self.assertEqual(g.failures, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
