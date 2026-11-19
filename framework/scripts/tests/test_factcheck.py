#!/usr/bin/env python3
"""Tests for framework/scripts/factcheck.py (`paf factcheck`)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("factcheck", ROOT / "framework" / "scripts" / "factcheck.py")
fc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fc)

HAS_DATA = "echo 1"
EMPTY = "true"
FAILS = "echo boom >&2; exit 3"


class FactCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def check(self, claims, target="slack:C1"):
        path = self.dir / "claims.json"
        path.write_text(json.dumps({"target": target, "claims": claims}))
        return fc.run(path, timeout=10, out_dir=self.dir / "receipts")

    def problems(self, receipt, cid):
        return next(c for c in receipt["claims"] if c["id"] == cid)["problems"]

    def test_verified_with_data_passes_and_writes_receipt(self):
        code, r = self.check([{"id": "c1", "text": "x", "category": "A", "command": HAS_DATA}])
        self.assertEqual(code, 0)
        self.assertEqual(r["status"], "pass")
        saved = json.loads(Path(r["receipt_path"]).read_text())
        self.assertEqual(saved["target"], "slack:C1")
        self.assertEqual(saved["claims"][0]["runs"]["command"]["output_lines"], 1)
        self.assertNotIn("_stdout", saved["claims"][0]["runs"]["command"])

    def test_verified_without_command_fails(self):
        code, r = self.check([{"id": "c1", "text": "from memory"}])
        self.assertEqual(code, 1)
        self.assertIn("not receipts", self.problems(r, "c1")[0])

    def test_empty_result_is_not_evidence(self):
        code, r = self.check([{"id": "c1", "command": EMPTY}])
        self.assertEqual(code, 1)
        self.assertTrue(any("no data" in p for p in self.problems(r, "c1")))

    def test_failing_command_fails(self):
        code, r = self.check([{"id": "c1", "command": FAILS}])
        self.assertEqual(code, 1)
        self.assertTrue(any("exited 3" in p for p in self.problems(r, "c1")))

    def test_expect_regex(self):
        self.assertEqual(self.check([{"id": "c1", "command": "echo clinic=20600", "expect_regex": "20600"}])[0], 0)
        self.assertEqual(self.check([{"id": "c1", "command": "echo clinic=56180", "expect_regex": "^clinic=20600$"}])[0], 1)

    def test_negative_needs_control_with_data(self):
        self.assertEqual(self.check([{"id": "c1", "category": "C", "kind": "negative", "command": EMPTY}])[0], 1)
        self.assertEqual(self.check([{"id": "c1", "category": "C", "kind": "negative", "command": EMPTY, "control_command": EMPTY}])[0], 1)
        self.assertEqual(self.check([{"id": "c1", "category": "C", "kind": "negative", "command": EMPTY, "control_command": HAS_DATA}])[0], 0)

    def test_identity_needs_cross_check(self):
        self.assertEqual(self.check([{"id": "c1", "category": "D", "command": HAS_DATA}])[0], 1)
        self.assertEqual(self.check([{"id": "c1", "category": "D", "command": HAS_DATA, "cross_check_command": EMPTY}])[0], 1)
        self.assertEqual(self.check([{"id": "c1", "category": "D", "command": HAS_DATA, "cross_check_command": HAS_DATA}])[0], 0)

    def test_all_claim_needs_full_set(self):
        self.assertEqual(self.check([{"id": "c1", "category": "F", "command": HAS_DATA}])[0], 1)
        self.assertEqual(self.check([{"id": "c1", "category": "F", "command": HAS_DATA, "full_set_command": HAS_DATA}])[0], 0)

    def test_inferred_must_come_from_passing_verified(self):
        ok = [{"id": "v", "command": HAS_DATA}, {"id": "i", "label": "inferred", "from": ["v"]}]
        self.assertEqual(self.check(ok)[0], 0)
        bad_src = [{"id": "v", "command": EMPTY}, {"id": "i", "label": "inferred", "from": ["v"]}]
        code, r = self.check(bad_src)
        self.assertEqual(code, 1)
        self.assertTrue(self.problems(r, "i"))
        self.assertEqual(self.check([{"id": "i", "label": "inferred"}])[0], 1)

    def test_unchecked_is_allowed_and_listed(self):
        code, r = self.check([{"id": "v", "command": HAS_DATA}, {"id": "u", "label": "unchecked", "text": "guess"}])
        self.assertEqual(code, 0)
        self.assertEqual(r["unchecked"], ["u"])

    def test_target_and_claims_required(self):
        self.assertEqual(self.check([{"id": "c1", "command": HAS_DATA}], target="")[0], 1)
        self.assertEqual(self.check([])[0], 1)

    def test_unknown_label_and_category(self):
        self.assertEqual(self.check([{"id": "c1", "label": "probably", "command": HAS_DATA}])[0], 1)
        self.assertEqual(self.check([{"id": "c1", "category": "Z", "command": HAS_DATA}])[0], 1)

    def test_commands_run_in_claims_file_directory(self):
        (self.dir / "evidence.txt").write_text("row\n")
        self.assertEqual(self.check([{"id": "c1", "command": "cat evidence.txt"}])[0], 0)

    def test_paf_cli_entrypoint(self):
        path = self.dir / "claims.json"
        path.write_text(json.dumps({"target": "VP-1", "claims": [{"id": "c1", "command": HAS_DATA}]}))
        proc = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "paf"), "factcheck", str(path), "--out-dir", str(self.dir / "r")],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("fact-check PASS", proc.stdout)


if __name__ == "__main__":
    unittest.main()
