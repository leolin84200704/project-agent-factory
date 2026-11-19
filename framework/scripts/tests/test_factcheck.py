#!/usr/bin/env python3
"""Tests for framework/scripts/factcheck.py (`paf factcheck`)."""

from __future__ import annotations

import importlib.util
import json
import os
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
SRC = "db.table"

# A stand-in for the verifier model. It reads {prompt, schema} on stdin, records the prompt,
# and answers from the scenario file: {"blind": {...payload...}, "audit": {...payload...}}.
FAKE_VERIFIER = r'''
import json, os, sys
req = json.load(sys.stdin)
step = "audit" if "source_is_ground_truth" in json.dumps(req["schema"]) else "blind"
with open(os.environ["FAKE_PROMPTS"], "a") as fh:
    fh.write(json.dumps({"step": step, "prompt": req["prompt"]}) + "\n")
scenario = json.load(open(os.environ["FAKE_SCENARIO"]))
if scenario.get("crash"):
    sys.exit(7)
print(json.dumps({"structured_output": scenario[step]}))
'''


def c(cid="c1", **kw):
    claim = {"id": cid, "text": kw.pop("text", "x"), "source": kw.pop("source", SRC), "command": kw.pop("command", HAS_DATA)}
    claim.update(kw)
    return claim


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.prompts = self.dir / "prompts.jsonl"
        self.scenario = self.dir / "scenario.json"
        (self.dir / "fake_verifier.py").write_text(FAKE_VERIFIER)
        (self.dir / "one.txt").write_text("1\n")
        (self.dir / "two.txt").write_text("2\n")
        (self.dir / "empty.txt").write_text("")
        (self.dir / "found.txt").write_text("found-a-row\n")
        (self.dir / "phi.txt").write_text("PATIENT-JANE-DOE\n")
        self.env = {"FAKE_PROMPTS": str(self.prompts), "FAKE_SCENARIO": str(self.scenario),
                    "PAF_CONSENSUS_KEY_FILE": str(self.dir / "no-key")}
        self.old_env = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)

    def tearDown(self) -> None:
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def check(self, claims, target="slack:C1", registry=None, **kw):
        path = self.dir / "claims.json"
        path.write_text(json.dumps({"target": target, "claims": claims}))
        sources = None
        if registry is not None:
            sources = self.dir / "sources.json"
            sources.write_text(json.dumps(registry))
        if kw.get("verify"):
            kw.setdefault("verifier_cmd", f"{sys.executable} {self.dir / 'fake_verifier.py'}")
        return fc.run(path, timeout=10, out_dir=self.dir / "receipts", sources=sources, **kw)

    def problems(self, receipt, cid):
        return next(x for x in receipt["claims"] if x["id"] == cid)["problems"]

    def prompts_seen(self):
        if not self.prompts.exists():
            return []
        return [json.loads(line) for line in self.prompts.read_text().splitlines()]


class DeterministicTest(Base):
    def test_verified_with_source_and_data_passes(self):
        code, r = self.check([c(category="A")])
        self.assertEqual(code, 0)
        saved = json.loads(Path(r["receipt_path"]).read_text())
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["claims"][0]["runs"]["command"]["output_lines"], 1)
        self.assertNotIn("_stdout", saved["claims"][0]["runs"]["command"])

    def test_verified_needs_source(self):
        code, r = self.check([c(source="")])
        self.assertEqual(code, 1)
        self.assertTrue(any("needs a source" in p for p in self.problems(r, "c1")))

    def test_verified_needs_command(self):
        code, r = self.check([{"id": "c1", "text": "from memory", "source": SRC}])
        self.assertEqual(code, 1)
        self.assertTrue(any("not receipts" in p for p in self.problems(r, "c1")))

    def test_empty_and_failing_commands(self):
        self.assertEqual(self.check([c(command=EMPTY)])[0], 1)
        code, r = self.check([c(command=FAILS)])
        self.assertTrue(any("exited 3" in p for p in self.problems(r, "c1")))

    def test_expect_regex(self):
        self.assertEqual(self.check([c(command="echo clinic=20600", expect_regex="20600")])[0], 0)
        self.assertEqual(self.check([c(command="echo clinic=56180", expect_regex="^clinic=20600$")])[0], 1)

    def test_negative_needs_control_with_data(self):
        self.assertEqual(self.check([c(category="C", kind="negative", command=EMPTY)])[0], 1)
        self.assertEqual(self.check([c(category="C", kind="negative", command=EMPTY, control_command=EMPTY)])[0], 1)
        self.assertEqual(self.check([c(category="C", kind="negative", command=EMPTY, control_command=HAS_DATA)])[0], 0)

    def test_identity_needs_cross_check(self):
        self.assertEqual(self.check([c(category="D")])[0], 1)
        self.assertEqual(self.check([c(category="D", cross_check_command=EMPTY)])[0], 1)
        self.assertEqual(self.check([c(category="D", cross_check_command=HAS_DATA)])[0], 0)

    def test_all_claim_needs_full_set(self):
        self.assertEqual(self.check([c(category="F")])[0], 1)
        self.assertEqual(self.check([c(category="F", full_set_command=HAS_DATA)])[0], 0)

    def test_inferred_and_unchecked(self):
        self.assertEqual(self.check([c("v"), {"id": "i", "label": "inferred", "from": ["v"]}])[0], 0)
        self.assertEqual(self.check([c("v", command=EMPTY), {"id": "i", "label": "inferred", "from": ["v"]}])[0], 1)
        self.assertEqual(self.check([{"id": "i", "label": "inferred"}])[0], 1)
        code, r = self.check([c("v"), {"id": "u", "label": "unchecked", "text": "guess"}])
        self.assertEqual((code, r["unchecked"]), (0, ["u"]))

    def test_target_claims_label_category(self):
        self.assertEqual(self.check([c()], target="")[0], 1)
        self.assertEqual(self.check([])[0], 1)
        self.assertEqual(self.check([c(label="probably")])[0], 1)
        self.assertEqual(self.check([c(category="Z")])[0], 1)

    def test_commands_run_in_claims_file_directory(self):
        (self.dir / "evidence.txt").write_text("row\n")
        self.assertEqual(self.check([c(command="cat evidence.txt")])[0], 0)


REGISTRY = {
    "phi": True,
    "runners": [{"prefix": "q", "kind": "sql", "usage": "q \"<SQL>\""}],
    "entities": [{
        "entity": "practice",
        "authoritative": [{"source": "lis_core_v7.clinic", "how": "clinic_id, clinic_name"}],
        "derived": [{"source": "lis_emr.ehr_integrations.clinic_name", "note": "label typed at setup"}],
    }],
}


class RegistryTest(Base):
    def test_derived_source_fails(self):
        code, r = self.check([c(source="lis_emr.ehr_integrations.clinic_name")], registry=REGISTRY)
        self.assertEqual(code, 1)
        self.assertTrue(any("not ground truth" in p and "lis_core_v7.clinic" in p for p in self.problems(r, "c1")))

    def test_authoritative_source_passes(self):
        code, r = self.check([c(source="lis_core_v7.clinic")], registry=REGISTRY)
        self.assertEqual(code, 0)
        self.assertEqual(r["claims"][0]["source_status"], "authoritative")

    def test_unregistered_source_is_a_note_not_a_failure(self):
        code, r = self.check([c(source="other.table")], registry=REGISTRY)
        self.assertEqual(code, 0)
        self.assertEqual(r["claims"][0]["source_status"], "unregistered")


class SourceMatchTest(unittest.TestCase):
    def test_tables(self):
        self.assertTrue(fc.sources_match("lis_core_v7.clinic", "LIS_CORE_V7.clinic.clinic_name"))
        self.assertFalse(fc.sources_match("lis_core_v7.clinic", "lis_emr.ehr_integrations"))

    def test_paths_compare_whole_path_not_basename(self):
        self.assertFalse(fc.sources_match("/repo/framework/templates/settings.json", "/inst/.claude/settings.json"))
        self.assertTrue(fc.sources_match("/repo/AGENTS.md", "/repo/./AGENTS.md"))
        self.assertTrue(fc.sources_match("AGENTS.md", "/repo/AGENTS.md", Path("/repo")))


class ReadOnlyTest(unittest.TestCase):
    def ok(self, cmd):
        self.assertIsNone(fc.readonly_problem(cmd, REGISTRY), cmd)

    def bad(self, cmd):
        self.assertIsNotNone(fc.readonly_problem(cmd, REGISTRY), cmd)

    def test_allowed(self):
        self.ok('q "SELECT clinic_name FROM lis_core_v7.clinic WHERE clinic_id=56180"')
        self.ok('q "WITH x AS (SELECT 1) SELECT * FROM x"')
        self.ok("grep -rn clinic_id src/")
        self.ok("git log --oneline -5")
        self.ok("gh pr view 12")
        self.ok("gh api repos/o/r/pulls")
        self.ok("kubectl get pods -n x")

    def test_refused(self):
        self.bad('q "UPDATE t SET a=1"')
        self.bad('q "SELECT 1; DELETE FROM t"')
        self.bad('q "SELECT * FROM t FOR UPDATE"')
        self.bad("rm -rf /")
        self.bad("cat a > b")
        self.bad("grep x f | sh")
        self.bad("echo $(whoami)")
        self.bad("git push origin main")
        self.bad("gh api -X DELETE repos/o/r")
        self.bad("gh pr merge 3")
        self.bad("kubectl delete pod x")
        self.bad("find . -delete")
        self.bad("")


class DualModelTest(Base):
    def set_scenario(self, blind=None, audit=None, **extra):
        self.scenario.write_text(json.dumps({"blind": blind or {"claims": []}, "audit": audit or {"claims": []}, **extra}))

    def test_blind_equivalent_passes_without_audit(self):
        self.set_scenario(blind={"claims": [{"id": "c1", "source": SRC, "command": "cat one.txt", "expect": "data"}]})
        code, r = self.check([c()], verify=True)
        self.assertEqual(code, 0, r)
        self.assertTrue(r["dual_model"])
        self.assertEqual(r["verification"]["claims"]["c1"]["by"], "blind-equivalent")
        self.assertEqual([p["step"] for p in self.prompts_seen()], ["blind"])

    def test_blind_prompt_hides_author_evidence(self):
        self.set_scenario(blind={"claims": [{"id": "c1", "source": SRC, "command": "cat one.txt", "expect": "data"}]})
        self.check([c(command="echo secret-author-output-123", source="secret.authortable")], verify=True)
        blind = self.prompts_seen()[0]["prompt"]
        self.assertIn(f"working directory {self.dir}", blind)
        self.assertNotIn("secret-author-output-123", blind)
        self.assertNotIn("secret.authortable", blind)

    def test_divergence_goes_to_audit_and_passes_when_approved(self):
        self.set_scenario(
            blind={"claims": [{"id": "c1", "source": "other.place", "command": "cat two.txt", "expect": "data"}]},
            audit={"claims": [{"id": "c1", "source_is_ground_truth": True, "command_proves_statement": True,
                               "counterexample_commands": [{"command": "cat empty.txt", "why": "should be empty"}],
                               "verdict": "approve"}]},
        )
        code, r = self.check([c()], verify=True)
        self.assertEqual(code, 0, r)
        self.assertEqual(r["verification"]["claims"]["c1"]["by"], "audit")
        self.assertEqual([p["step"] for p in self.prompts_seen()], ["blind", "audit"])

    def test_counterexample_with_data_disproves(self):
        self.set_scenario(
            blind={"claims": [{"id": "c1", "source": "other.place", "command": "cat two.txt", "expect": "data"}]},
            audit={"claims": [{"id": "c1", "source_is_ground_truth": True, "command_proves_statement": True,
                               "counterexample_commands": [{"command": "cat found.txt"}], "verdict": "approve"}]},
        )
        code, r = self.check([c()], verify=True)
        self.assertEqual(code, 1)
        self.assertTrue(any("counterexample" in p for p in self.problems(r, "c1")))

    def test_audit_rejects_source(self):
        self.set_scenario(
            blind={"claims": [{"id": "c1", "source": "master.table", "command": "cat two.txt", "expect": "data"}]},
            audit={"claims": [{"id": "c1", "source_is_ground_truth": False, "command_proves_statement": True,
                               "verdict": "reject"}]},
        )
        code, r = self.check([c(source="label.column")], verify=True)
        self.assertEqual(code, 1)
        self.assertTrue(any("not ground truth" in p for p in self.problems(r, "c1")))

    def test_write_command_from_verifier_is_refused_not_run(self):
        marker = self.dir / "should-not-exist"
        self.set_scenario(
            blind={"claims": [{"id": "c1", "source": SRC, "command": f"touch {marker}", "expect": "data"}]},
            audit={"claims": [{"id": "c1", "source_is_ground_truth": True, "command_proves_statement": True,
                               "counterexample_commands": [{"command": f"rm -rf {self.dir}"}], "verdict": "approve"}]},
        )
        code, r = self.check([c()], verify=True)
        self.assertFalse(marker.exists())
        self.assertTrue(self.dir.exists())
        e = r["verification"]["claims"]["c1"]
        self.assertIn("refused", e["blind"])
        self.assertIn("refused", e["counterexamples"][0])
        self.assertEqual(code, 0)

    def test_phi_mode_keeps_output_out_of_audit_prompt(self):
        self.set_scenario(
            blind={"claims": [{"id": "c1", "source": "x.y", "command": "cat two.txt", "expect": "data"}]},
            audit={"claims": [{"id": "c1", "source_is_ground_truth": True, "command_proves_statement": True, "verdict": "approve"}]},
        )
        self.check([c(command="cat phi.txt")], verify=True, registry=REGISTRY)
        audit = [p for p in self.prompts_seen() if p["step"] == "audit"][0]["prompt"]
        self.assertNotIn("PATIENT-JANE-DOE", audit)
        self.prompts.unlink()
        self.check([c(command="cat phi.txt")], verify=True, phi=False)
        audit = [p for p in self.prompts_seen() if p["step"] == "audit"][0]["prompt"]
        self.assertIn("PATIENT-JANE-DOE", audit)

    def test_negative_claim_equivalent_when_both_empty(self):
        self.set_scenario(blind={"claims": [{"id": "c1", "source": SRC, "command": "cat empty.txt", "expect": "empty"}]})
        code, r = self.check([c(category="C", kind="negative", command=EMPTY, control_command=HAS_DATA)], verify=True)
        self.assertEqual(code, 0, r)

    def test_same_family_refused_and_verifier_error_fails_closed(self):
        self.set_scenario(blind={"claims": []})
        code, r = self.check([c()], verify=True, author_family="claude", verifier_family="claude")
        self.assertEqual(code, 1)
        self.assertTrue(any("different model family" in p for p in r["problems"]))
        self.set_scenario(crash=True)
        code, r = self.check([c()], verify=True)
        self.assertEqual(code, 1)
        self.assertEqual(r["verification"]["status"], "error")

    def test_missing_blind_answer_requires_audit(self):
        self.set_scenario(blind={"claims": []}, audit={"claims": []})
        code, r = self.check([c()], verify=True)
        self.assertEqual(code, 1)


class SigningTest(Base):
    def test_receipt_signed_with_consensus_key(self):
        key = self.dir / "consensus.key"
        key.write_text("s3cret-for-test-0123456789abcdef\n")
        key.chmod(0o600)
        os.environ["PAF_CONSENSUS_KEY_FILE"] = str(key)
        code, r = self.check([c()])
        self.assertEqual(code, 0)
        saved = json.loads(Path(r["receipt_path"]).read_text())
        self.assertTrue(saved["signed"])
        import hashlib, hmac
        expected = hmac.new(b"s3cret-for-test-0123456789abcdef", fc.canonical_receipt(saved), hashlib.sha256).hexdigest()
        self.assertEqual(saved["sig"], expected)

    def test_unsigned_without_key(self):
        code, r = self.check([c()])
        self.assertFalse(r["signed"])


class CliTest(Base):
    def test_paf_cli_passes_options_through(self):
        path = self.dir / "claims.json"
        path.write_text(json.dumps({"target": "VP-1", "claims": [c()]}))
        proc = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "paf"), "factcheck", str(path), "--out-dir", str(self.dir / "r"), "--timeout", "5"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("fact-check PASS", proc.stdout)


if __name__ == "__main__":
    unittest.main()
