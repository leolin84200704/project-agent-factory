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

    def test_negative_grep_no_match_is_not_a_failed_command(self):
        # grep exits 1 with no output when nothing matches; for a negative claim that is the answer.
        none = f"grep -n no-such-text {self.dir / 'one.txt'}"
        self.assertEqual(self.check([c(category="C", kind="negative", command=none, control_command=HAS_DATA)])[0], 0)
        # The same exit code on a positive claim still fails.
        code, r = self.check([c(command=none)])
        self.assertEqual(code, 1)
        self.assertTrue(any("exited 1" in p for p in self.problems(r, "c1")))
        # Exit 1 with an error message (missing file) is a real failure even for a negative claim.
        missing = f"grep -n x {self.dir / 'missing.txt'}"
        code, r = self.check([c(category="C", kind="negative", command=f"{missing}; exit 1", control_command=HAS_DATA)])
        self.assertEqual(code, 1)
        # Exit codes other than 1 still fail.
        code, r = self.check([c(category="C", kind="negative", command=FAILS, control_command=HAS_DATA)])
        self.assertEqual(code, 1)

    def test_negative_claim_fails_when_command_returns_data(self):
        # grep finds a match: the claim says "absent", the data says present.
        found = f"grep -n 1 {self.dir / 'one.txt'}"
        code, r = self.check([c(category="C", kind="negative", command=found, control_command=HAS_DATA)])
        self.assertEqual(code, 1)
        self.assertTrue(any("contradicts the claim" in p for p in self.problems(r, "c1")))
        # grep -c prints 0 and exits 1 when nothing matches; that output still counts as data.
        count = f"grep -c no-such-text {self.dir / 'one.txt'}"
        self.assertEqual(self.check([c(category="C", kind="negative", command=count, control_command=HAS_DATA)])[0], 1)

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

    def test_table_level_citation_of_derived_column_fails(self):
        code, r = self.check([c(source="lis_emr.ehr_integrations")], registry=REGISTRY)
        self.assertEqual(code, 1)
        self.assertTrue(any("not ground truth" in p for p in self.problems(r, "c1")))

    def test_other_column_of_same_table_is_not_derived(self):
        status, _ = fc.classify_source("lis_emr.ehr_integrations.integration_status", REGISTRY)
        self.assertEqual(status, "unregistered")

    def test_table_listed_as_authoritative_is_not_derived(self):
        reg = json.loads(json.dumps(REGISTRY))
        reg["entities"].append({"entity": "integration", "authoritative": [{"source": "lis_emr.ehr_integrations"}]})
        self.assertEqual(fc.classify_source("lis_emr.ehr_integrations", reg)[0], "authoritative")


class SourceMatchTest(unittest.TestCase):
    def test_tables(self):
        self.assertTrue(fc.sources_match("lis_core_v7.clinic", "LIS_CORE_V7.clinic.clinic_name"))
        self.assertFalse(fc.sources_match("lis_core_v7.clinic", "lis_emr.ehr_integrations"))

    def test_paths_compare_whole_path_not_basename(self):
        self.assertFalse(fc.sources_match("/repo/framework/templates/settings.json", "/inst/.claude/settings.json"))
        self.assertTrue(fc.sources_match("/repo/AGENTS.md", "/repo/./AGENTS.md"))
        self.assertTrue(fc.sources_match("AGENTS.md", "/repo/AGENTS.md", Path("/repo")))


class DiscoveryTest(Base):
    def test_registry_found_in_repo_reference_dir(self):
        repo = self.dir / "inst"
        (repo / "reference").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        reg = repo / "reference" / "ground-truth-sources.json"
        reg.write_text(json.dumps(REGISTRY))
        (repo / "sub").mkdir()
        self.assertEqual(fc.discover_sources(repo / "sub").resolve(), reg.resolve())
        self.assertIsNone(fc.discover_sources(self.dir))

    def test_env_overrides_discovery(self):
        reg = self.dir / "explicit.json"
        reg.write_text(json.dumps(REGISTRY))
        os.environ["PAF_FACTCHECK_SOURCES"] = str(reg)
        try:
            self.assertEqual(fc.load_sources(None)["_path"], str(reg))
        finally:
            os.environ.pop("PAF_FACTCHECK_SOURCES", None)


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
        self.ok('grep -n "a|b;c" file.txt')
        self.ok('q "SELECT 1;"')
        self.ok("rg -n 'x$' src/")

    def test_unspaced_operators_and_newlines_refused(self):
        for cmd in ("cat a;rm -rf /", "cat a>b", "cat a|sh", "cat a&&rm -rf /", "cat a\\;rm x",
                    "cat one.txt\nrm -rf /", "cat one.txt\rrm x", "cat ${HOME}/x", "cat `id`"):
            self.bad(cmd)

    def test_sql_runner_single_read_statement(self):
        self.bad('q "SELECT 1; SELECT 2"')
        self.bad('q "SELECT 1; SET autocommit=0"')
        self.bad("q \"SELECT * INTO OUTFILE '/tmp/x' FROM t\"")
        self.bad("q \"SELECT * FROM t INTO DUMPFILE '/tmp/x'\"")

    def test_write_flags_on_allowed_tools_refused(self):
        for cmd in ("gh api -XDELETE repos/o/r", "gh api --method=POST repos/o/r", "gh api -iXPOST repos/o/r",
                    "gh api repos/o/r -fname=x", "gh api repos/o/r --field=name=x", "gh api graphql --input=q.json",
                    "find . -fprint0 /tmp/x", "find . -fprintf /tmp/x %p", "find . -fls /tmp/x",
                    "find . -okdir rm {} +", "rg --pre=sh x", "rg --pre sh x",
                    "git log --output=/tmp/x", "git diff --ext-diff", "git grep -Osh x",
                    "/tmp/planted/cat one.txt"):
            self.bad(cmd)

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
        self.assertEqual(r["verification"]["status"], "pass")
        self.assertEqual(r["verification"]["claims"]["c1"]["by"], "blind-equivalent")
        self.assertEqual([p["step"] for p in self.prompts_seen()], ["blind"])
        # The fake verifier replaces the model CLI, so the receipt must not claim dual-model.
        self.assertFalse(r["dual_model"])
        self.assertTrue(r["verifier_override"])

    def test_negative_claim_with_blind_expecting_data_goes_to_audit(self):
        # The author says "nothing exists"; the verifier wrote a presence probe (expect=data) that came
        # back empty. That is the verifier's probe failing, not agreement.
        self.set_scenario(blind={"claims": [{"id": "c1", "source": SRC, "command": "cat empty.txt", "expect": "data"}]},
                          audit={"claims": []})
        code, r = self.check([c(category="C", kind="negative", command=EMPTY, control_command=HAS_DATA)], verify=True)
        self.assertEqual(code, 1)
        e = r["verification"]["claims"]["c1"]
        self.assertFalse(e["result_match"])
        self.assertNotEqual(e.get("by"), "blind-equivalent")
        self.assertEqual([p["step"] for p in self.prompts_seen()], ["blind", "audit"])

    def test_positive_claim_with_blind_expecting_empty_goes_to_audit(self):
        self.set_scenario(blind={"claims": [{"id": "c1", "source": SRC, "command": "cat one.txt", "expect": "empty"}]},
                          audit={"claims": []})
        code, r = self.check([c(command="cat one.txt")], verify=True)
        self.assertEqual(code, 1)
        self.assertFalse(r["verification"]["claims"]["c1"]["result_match"])

    def test_verifier_commands_run_without_a_shell(self):
        run = fc.run_command("echo $HOME", 5, self.dir, shell=False)
        self.assertEqual(run["output_excerpt"], "$HOME")
        run = fc.run_command("cat one.txt;touch pwned", 5, self.dir, shell=False)
        self.assertNotEqual(run["exit_code"], 0)
        self.assertFalse((self.dir / "pwned").exists())
        self.assertEqual(fc.run_command("no-such-binary-xyz", 5, self.dir, shell=False)["exit_code"], 127)

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

    def test_negative_grep_no_match_is_blind_equivalent(self):
        # Both sides run a grep that finds nothing (exit 1, no output); that agrees without an audit.
        grep = "grep -n no-such-text one.txt"
        self.set_scenario(blind={"claims": [{"id": "c1", "source": SRC, "command": grep, "expect": "empty"}]})
        code, r = self.check([c(category="C", kind="negative", command=f"grep -n no-such-text {self.dir / 'one.txt'}",
                                control_command=HAS_DATA)], verify=True)
        self.assertEqual(code, 0, r)
        self.assertEqual(r["verification"]["claims"]["c1"]["by"], "blind-equivalent")

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


class ClaudeVerifierConfinementTest(Base):
    def test_claude_verifier_is_confined_to_an_empty_directory(self):
        argv_log = self.dir / "argv.json"
        fake = self.dir / "fake_claude"
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "sys.stdin.read()\n"
            f"json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd()}}, open({str(argv_log)!r}, 'w'))\n"
            "print(json.dumps({'structured_output': {'claims': []}}))\n"
        )
        fake.chmod(0o755)
        os.environ["PAF_CLAUDE_BIN"] = str(fake)
        try:
            payload, meta = fc.call_verifier("claude", "opus", None, "prompt", fc.BLIND_SCHEMA, None)
        finally:
            os.environ.pop("PAF_CLAUDE_BIN", None)
        self.assertEqual(payload, {"claims": []})
        self.assertEqual(meta["cli"], "claude")
        seen = json.loads(argv_log.read_text())
        argv = seen["argv"]
        self.assertIn("--allowedTools", argv)
        self.assertIn("--settings", argv)
        allowed = argv[argv.index("--allowedTools") + 1]
        self.assertIn("paf-factcheck-verifier-", allowed)
        self.assertNotIn(str(self.dir.resolve()), allowed)
        settings = json.loads(argv[argv.index("--settings") + 1])
        deny = settings["permissions"]["deny"]
        self.assertIn("Read(~/.config/**)", deny)
        self.assertIn("paf-factcheck-verifier-", seen["cwd"])


class AgyVerifierConfinementTest(Base):
    def test_agy_verifier_gets_key_deny_settings(self):
        argv_log = self.dir / "agy_argv.json"
        fake = self.dir / "fake_agy"
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            f"json.dump(sys.argv[1:], open({str(argv_log)!r}, 'w'))\n"
            "print(json.dumps({'status': 'SUCCESS', 'structured_output': {'claims': []}}))\n"
        )
        fake.chmod(0o755)
        key = self.dir / "consensus.key"
        os.environ["PAF_AGY_BIN"] = str(fake)
        os.environ["PAF_CONSENSUS_KEY_FILE"] = str(key)
        try:
            fc.call_verifier("gemini", "gemini-test", None, "prompt", fc.BLIND_SCHEMA, None)
        finally:
            os.environ.pop("PAF_AGY_BIN", None)
            os.environ["PAF_CONSENSUS_KEY_FILE"] = self.env["PAF_CONSENSUS_KEY_FILE"]
        argv = json.loads(argv_log.read_text())
        self.assertIn("--sandbox", argv)
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertNotIn("allow", settings["permissions"])
        self.assertIn(f"read_file({key.resolve()})", settings["permissions"]["deny"])


class HookIntegrationTest(Base):
    HOOK = ROOT / "framework" / "hooks" / "require-factcheck-receipt.sh"

    def hook(self, require_dual: str) -> int:
        payload = json.dumps({"session_id": "s", "tool_name": "mcp__claude_ai_Slack__slack_send_message",
                              "tool_input": {"channel_id": "C1", "message": "x"}})
        env = dict(os.environ, PAF_FACTCHECK_DIR=str(self.dir / "receipts"), PAF_FACTCHECK_MODE="block",
                   PAF_FACTCHECK_REQUIRE_DUAL=require_dual, TMPDIR=str(self.dir))
        return subprocess.run(["bash", str(self.HOOK)], input=payload, env=env,
                              capture_output=True, text=True).returncode

    def test_signed_override_receipt_refused_when_dual_required(self):
        key = self.dir / "consensus.key"
        key.write_text("s3cret-for-test-0123456789abcdef\n")
        key.chmod(0o600)
        os.environ["PAF_CONSENSUS_KEY_FILE"] = str(key)
        self.scenario.write_text(json.dumps({"blind": {"claims": [
            {"id": "c1", "source": SRC, "command": "cat one.txt", "expect": "data"}]}, "audit": {"claims": []}}))
        code, r = self.check([c()], target="slack:C1", verify=True)
        self.assertEqual(code, 0, r)
        self.assertTrue(r["signed"])
        self.assertEqual(r["verification"]["status"], "pass")
        # The same receipt is accepted when dual is not required, so a refusal below is about dual.
        self.assertEqual(self.hook("0"), 0)
        self.assertEqual(self.hook("1"), 2)


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

    def test_unsigned_without_key_says_why(self):
        code, r = self.check([c()])
        self.assertFalse(r["signed"])
        self.assertIn("no consensus key", r["unsigned_reason"])

    def test_shielded_key_is_waited_for_then_reported(self):
        key = self.dir / "consensus.key"
        key.write_text("s3cret-for-test-0123456789abcdef\n")
        key.chmod(0o000)
        os.environ["PAF_CONSENSUS_KEY_FILE"] = str(key)
        os.environ["PAF_FACTCHECK_KEY_WAIT_SEC"] = "0"
        try:
            code, r = self.check([c()])
        finally:
            key.chmod(0o600)
            os.environ.pop("PAF_FACTCHECK_KEY_WAIT_SEC", None)
        self.assertFalse(r["signed"])
        self.assertTrue(r["unsigned_reason"])


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
