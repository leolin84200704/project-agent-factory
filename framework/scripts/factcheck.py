#!/usr/bin/env python3
"""Fact-check runner behind `paf factcheck` (framework/FACT-CHECK.md).

Every fact that a piece of outbound text, a DB write or a conclusion depends on
is written as a triple: the statement, the source it comes from, and the command
that proves it. This script runs each command itself and writes a receipt. A
claim passes only when the command actually returned data; memory, notes,
reasoning, an empty result, or a failing command never count.

With --verify, a model from a different family checks the claims in two steps
(Leo, 2026-11-19):
  1. Blind: the verifier sees only the statements and the sources registry,
     never the author's commands or results. It proposes its own source and
     read-only command for each statement. The script runs them and compares
     sources and results with the author's.
  2. Audit, for claims that did not match: the verifier sees both triples and
     the run summaries, judges whether the author's source is ground truth and
     whether the command proves the statement, and proposes counterexample
     commands that must return nothing if the statement is true. The script
     runs them.
The verifier never runs anything itself; every command it proposes passes a
read-only check first. In hashes-only mode (the default) query output never
reaches the verifier, only exit codes, line counts and hashes.

Claims file (JSON):
{
  "target": "slack:C0B8YU8JZG8",
  "claims": [
    {"id": "c1", "text": "56180 is an inactive NPI-type account", "category": "D",
     "source": "lis_core_v7.clinic",
     "command": "q \\"SELECT clinic_name, clinic_type FROM lis_core_v7.clinic WHERE clinic_id=56180\\"",
     "cross_check_command": "q \\"SELECT isActive FROM lis_core_v7.customer WHERE customer_id=12243\\""},
    {"id": "c2", "text": "56180 never had an order", "category": "C", "kind": "negative",
     "source": "lis_core_v7.order_info",
     "command": "...returns nothing...", "control_command": "...same query on a clinic with orders..."},
    {"id": "c3", "text": "Use 20600 for Genesis", "label": "inferred", "from": ["c1", "c2"]},
    {"id": "c4", "text": "Khurana used to practice at Genesis", "label": "unchecked"}
  ]
}

Sources registry (JSON), kept per instance. Lookup order: --sources, then
$PAF_FACTCHECK_SOURCES, then ground-truth-sources.json or
reference/ground-truth-sources.json at the root of the git repo containing the
current directory:
{
  "phi": true,
  "runners": [{"prefix": "python3 /path/lis-readonly-query.py", "kind": "sql",
               "usage": "python3 /path/lis-readonly-query.py \\"<SQL>\\""}],
  "entities": [
    {"entity": "practice (clinic)",
     "authoritative": [{"source": "lis_core_v7.clinic", "how": "clinic_id, clinic_name, clinic_type"}],
     "derived": [{"source": "lis_emr.ehr_integrations.clinic_name", "note": "label typed at integration setup"}]}
  ]
}

Deterministic rules:
- verified claim: needs `source` and `command`; the command must exit 0 with
  output, and an optional `expect_regex` must match.
- a source listed as derived in the registry fails the claim.
- kind=negative (category C): `control_command` must return data.
- category D: `cross_check_command` must return data.
- category F: `full_set_command` must return data.
- inferred claim: `from` must list passing verified claims.
- unchecked claim: allowed and listed, so the text says it was not checked.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

CATEGORIES = set("ABCDEFGHIJ")
LABELS = ("verified", "inferred", "unchecked")
FAMILIES = ("claude", "gemini")
DEFAULT_TIMEOUT = 120
OUTPUT_EXCERPT = 400
DEFAULT_VERIFIER_MODEL = {"gemini": "gemini-4-argon-high", "claude": "opus"}
WATCHER_PATH = Path(__file__).resolve().parent / "paf_watcher.py"

# Verifier-proposed commands never reach a shell (run_command(..., shell=False)); these checks are a
# second layer. Characters a shell would treat as operators are refused when they appear unquoted,
# and the sequences below are refused anywhere in the command.
SHELL_OPERATOR_CHARS = set(";&|<>()")
FORBIDDEN_ANYWHERE = ("\n", "\r", "\x00", "`", "$(", "${")
SQL_READ_RE = re.compile(r"^\s*(select|show|describe|desc|explain|with)\b", re.I)
SQL_WRITE_RE = re.compile(
    r"\b(insert|update|delete|replace|alter|drop|create|truncate|grant|revoke|rename|lock|call|load|handler)\b"
    r"|\binto\s+(outfile|dumpfile)\b",
    re.I,
)


def _long_flag(tok: str, names: tuple[str, ...]) -> bool:
    return tok.startswith("--") and tok.split("=", 1)[0] in names


def _short_flag_has(tok: str, letters: str) -> bool:
    """True if a short-flag token (or a combined group such as -iXPOST) contains one of `letters`."""
    return tok.startswith("-") and not tok.startswith("--") and any(ch in letters for ch in tok[1:])


def _gh_readonly(a: list[str]) -> bool:
    if not a:
        return False
    if a[0] in ("pr", "issue", "run", "release"):
        return len(a) > 1 and a[1] in ("view", "list", "diff", "checks")
    if a[0] == "api":
        # Fields or --input make gh send a POST; -X/--method set the verb explicitly.
        return not any(
            _long_flag(x, ("--method", "--field", "--raw-field", "--input")) or _short_flag_has(x, "XfF")
            for x in a[1:]
        )
    return False


def _git_readonly(a: list[str]) -> bool:
    if not a or a[0] not in ("log", "show", "diff", "grep", "ls-files", "rev-parse", "blame", "status", "cat-file"):
        return False
    # --output writes a file; -O/--open-files-in-pager and --ext-diff run other programs.
    return not any(
        _long_flag(x, ("--output", "--open-files-in-pager", "--ext-diff")) or (a[0] == "grep" and x.startswith("-O"))
        for x in a[1:]
    )


def _find_readonly(a: list[str]) -> bool:
    return not any(
        x in ("-exec", "-execdir", "-delete", "-ok", "-okdir", "-fls") or x.startswith("-fprint") for x in a
    )


READONLY_TOOLS: dict[str, Callable[[list[str]], bool]] = {
    "grep": lambda a: True,
    "rg": lambda a: not any(_long_flag(x, ("--pre",)) for x in a),  # --pre runs a program per file
    "cat": lambda a: True,
    "head": lambda a: True,
    "tail": lambda a: True,
    "wc": lambda a: True,
    "ls": lambda a: True,
    "jq": lambda a: True,
    "find": _find_readonly,
    "git": _git_readonly,
    "gh": _gh_readonly,
    "kubectl": lambda a: bool(a) and a[0] in ("get", "describe", "logs", "top"),
}

BLIND_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "source": {"type": "string"},
                    "command": {"type": "string"},
                    "expect": {"type": "string", "enum": ["data", "empty"]},
                    "reasoning": {"type": "string"},
                },
                "required": ["id", "source", "command", "expect"],
            },
        }
    },
    "required": ["claims"],
}
AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "source_is_ground_truth": {"type": "boolean"},
                    "command_proves_statement": {"type": "boolean"},
                    "counterexample_commands": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}, "why": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                    "verdict": {"type": "string", "enum": ["approve", "reject"]},
                    "reasoning": {"type": "string"},
                },
                "required": ["id", "source_is_ground_truth", "command_proves_statement", "verdict"],
            },
        }
    },
    "required": ["claims"],
}


# ---------------------------------------------------------------- helpers


def receipts_dir() -> Path:
    override = os.environ.get("PAF_FACTCHECK_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "paf" / "factcheck"


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")
    return (s or "target")[:80]


def norm_source(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower().strip("`'\""))


DOTTED_RE = r"[a-z0-9_]+\.[a-z0-9_]+(?:\.[a-z0-9_]+)?"


def source_tables(s: str) -> set[str]:
    """schema.table tokens in a source string (a third dotted part is a column)."""
    return {".".join(m.split(".")[:2]) for m in re.findall(DOTTED_RE, norm_source(s))}


def table_level_refs(s: str) -> set[str]:
    """schema.table tokens cited without a column part."""
    return {m for m in re.findall(DOTTED_RE, norm_source(s)) if m.count(".") == 1}


def normalized_output_sha(out: str) -> str:
    lines = sorted(line.strip() for line in out.strip().splitlines() if line.strip())
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def run_command(cmd: str, timeout: int, cwd: Path | None, *, shell: bool = True) -> dict[str, Any]:
    """Run a command and summarize it. The author's own commands run through the shell; commands
    proposed by the verifier pass shell=False and run as a parsed argument list, so no shell ever
    interprets verifier output."""
    started = dt.datetime.now(dt.timezone.utc)
    try:
        args: str | list[str] = cmd if shell else shlex.split(cmd)
        proc = subprocess.run(args, shell=shell, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        code, out, err = 124, "", f"timeout after {timeout}s"
    except (OSError, ValueError, IndexError) as exc:  # missing binary, unparsable or empty argv without a shell
        code, out, err = 127, "", str(exc)
    stripped = out.strip()
    return {
        "command": cmd,
        "ran_at": started.isoformat(timespec="seconds"),
        "exit_code": code,
        "output_lines": len(stripped.splitlines()) if stripped else 0,
        "output_sha256": hashlib.sha256(out.encode()).hexdigest(),
        "normalized_sha256": normalized_output_sha(out),
        "output_excerpt": stripped[:OUTPUT_EXCERPT],
        "stderr_excerpt": err.strip()[:OUTPUT_EXCERPT],
        "has_data": code == 0 and bool(stripped),
        "_stdout": out,
    }


def strip_private(run: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in run.items() if not k.startswith("_")}


def run_summary(run: dict[str, Any] | None, phi: bool) -> dict[str, Any]:
    """What the verifier may see about a run. Output text only when phi is off."""
    if not run:
        return {"ran": False}
    out = {
        "exit_code": run["exit_code"],
        "output_lines": run["output_lines"],
        "normalized_sha256": run["normalized_sha256"][:16],
    }
    if not phi:
        out["output_excerpt"] = run["output_excerpt"]
    return out


# ---------------------------------------------------------------- registry


SOURCES_CANDIDATES = ("ground-truth-sources.json", "reference/ground-truth-sources.json")


def discover_sources(start: Path | None = None) -> Path | None:
    """Convention: the git repo containing the current directory holds its registry at one of
    SOURCES_CANDIDATES. Lets an instance ship its registry without touching settings files."""
    try:
        top = subprocess.run(["git", "-C", str(start or Path.cwd()), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    for rel in SOURCES_CANDIDATES:
        cand = Path(top) / rel
        if cand.is_file():
            return cand
    return None


def load_sources(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        env = os.environ.get("PAF_FACTCHECK_SOURCES", "").strip()
        path = Path(env).expanduser() if env else discover_sources()
    if path is None or not path.is_file():
        return None
    data = json.loads(path.read_text())
    data["_path"] = str(path)
    return data


def classify_source(source: str, registry: dict[str, Any] | None) -> tuple[str, str]:
    """Return (status, detail): derived | authoritative | unregistered | no-registry."""
    if registry is None:
        return "no-registry", ""
    ns = norm_source(source)
    exact_authoritative = any(
        norm_source(a.get("source", "")) == ns
        for ent in registry.get("entities", []) for a in ent.get("authoritative", [])
    )
    cited_tables = table_level_refs(source)
    for ent in registry.get("entities", []):
        for d in ent.get("derived", []):
            dn = norm_source(d.get("source", ""))
            if not dn:
                continue
            # A table cited without a column is ambiguous when the registry marks a column of that
            # table as derived; it fails unless the registry lists that exact source as authoritative.
            table_hit = (not exact_authoritative and not _is_path(ns) and not _is_path(dn)
                         and bool(cited_tables & source_tables(dn)))
            if dn in ns or table_hit:
                auth = ", ".join(a.get("source", "") for a in ent.get("authoritative", []))
                return "derived", f"{d['source']} is derived ({d.get('note', '')}); use {auth}"
    tables = source_tables(source)
    for ent in registry.get("entities", []):
        for a in ent.get("authoritative", []):
            if norm_source(a.get("source", "")) == ns or (source_tables(a.get("source", "")) & tables):
                return "authoritative", ent.get("entity", "")
    return "unregistered", "source is not in the registry; the audit step judges it"


def _unquoted_operator(cmd: str) -> str | None:
    """First shell operator character outside single or double quotes, escaped or not."""
    quote: str | None = None
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if quote == "'":
            if ch == "'":
                quote = None
        elif quote == '"':
            if ch == "\\":
                i += 1
            elif ch == '"':
                quote = None
        elif ch in SHELL_OPERATOR_CHARS:
            return ch
        elif ch == "\\":
            if i + 1 < len(cmd) and cmd[i + 1] in SHELL_OPERATOR_CHARS:
                return cmd[i + 1]
            i += 1
        elif ch in "'\"":
            quote = ch
        i += 1
    return None


def readonly_problem(cmd: str, registry: dict[str, Any] | None) -> str | None:
    """None if a verifier-proposed command is read-only, else the reason it is refused."""
    if not cmd or not cmd.strip():
        return "empty command"
    if any(s in cmd for s in FORBIDDEN_ANYWHERE):
        return "newlines, command substitution and parameter expansion are not allowed"
    op = _unquoted_operator(cmd)
    if op:
        return f"shell operator {op!r} (pipes, redirects, chaining, subshells) is not allowed"
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError as exc:
        return f"cannot parse command: {exc}"
    if not tokens:
        return "empty command"
    for runner in (registry or {}).get("runners", []):
        prefix = shlex.split(runner.get("prefix", ""))
        if prefix and tokens[: len(prefix)] == prefix:
            rest = " ".join(tokens[len(prefix):])
            if runner.get("kind") == "sql":
                if not SQL_READ_RE.match(rest):
                    return "runner SQL must start with SELECT, SHOW, DESCRIBE, EXPLAIN or WITH"
                if ";" in rest.strip().rstrip(";"):
                    return "runner SQL must be a single statement"
                if SQL_WRITE_RE.search(rest):
                    return "runner SQL contains a write keyword"
            return None
    tool = tokens[0]
    if "/" in tool:
        return "tools must be named bare (resolved from PATH), not by path"
    check = READONLY_TOOLS.get(tool)
    if check is None:
        return f"{tool!r} is not an allowed read-only tool or registered runner"
    if not check(tokens[1:]):
        return f"{tool} arguments are not read-only"
    return None


# ---------------------------------------------------------------- deterministic checks


def _negative_no_match(kind: str, run: dict) -> bool:
    """grep, rg and git grep exit 1 with no output when nothing matches.

    For a negative claim that empty result is the expected answer, not a failed command,
    so it does not count as an error. The control command still has to return data, and
    exit 1 with any stdout or stderr (a real error) stays a failure.
    """
    return (
        kind == "negative"
        and run.get("exit_code") == 1
        and not run.get("output_lines")
        and not (run.get("stderr_excerpt") or "").strip()
    )


def check_claim(claim: dict[str, Any], timeout: int, cwd: Path | None, registry: dict[str, Any] | None) -> dict[str, Any]:
    cid = str(claim.get("id") or "").strip()
    label = claim.get("label", "verified")
    category = str(claim.get("category", "")).upper()
    kind = claim.get("kind", "positive")
    result: dict[str, Any] = {
        "id": cid,
        "text": claim.get("text", ""),
        "label": label,
        "category": category,
        "kind": kind,
        "source": claim.get("source", ""),
        "problems": [],
        "notes": [],
        "runs": {},
    }
    problems: list[str] = result["problems"]

    if not cid:
        problems.append("claim has no id")
    if label not in LABELS:
        problems.append(f"label must be one of {LABELS}")
        return result
    if category and category not in CATEGORIES:
        problems.append(f"unknown category {category!r} (expected A-J)")
    if label == "unchecked":
        return result
    if label == "inferred":
        if not claim.get("from"):
            problems.append("inferred claim must list the verified claim ids it comes from")
        return result

    source = str(claim.get("source") or "").strip()
    if not source:
        problems.append("verified claim needs a source: the system of record the fact comes from")
    else:
        status, detail = classify_source(source, registry)
        result["source_status"] = status
        if status == "derived":
            problems.append(f"source is not ground truth: {detail}")
        elif status == "unregistered":
            result["notes"].append(detail)

    cmd = claim.get("command")
    if not cmd:
        problems.append("verified claim needs a command; memory, notes and reasoning are not receipts")
        return result

    main = run_command(cmd, timeout, cwd)
    result["runs"]["command"] = main
    if main["exit_code"] != 0 and not _negative_no_match(kind, main):
        problems.append(f"command exited {main['exit_code']}")

    if kind == "negative":
        if main["output_lines"]:
            problems.append("negative claim's command returned data, which contradicts the claim")
        control = claim.get("control_command")
        if not control:
            problems.append("negative claim needs control_command: the same probe on a known positive must return data")
        else:
            run = run_command(control, timeout, cwd)
            result["runs"]["control_command"] = run
            if not run["has_data"]:
                problems.append("control_command returned no data, so an empty result proves nothing")
    else:
        if not main["has_data"]:
            problems.append("command returned no data; an empty result is not evidence for a positive claim")
        pattern = claim.get("expect_regex")
        if pattern and main["has_data"] and not re.search(pattern, main["_stdout"], re.M):
            problems.append(f"output does not match expect_regex {pattern!r}")

    for cat, key, msg in (
        ("D", "cross_check_command", "identity/ownership/in-use claim needs cross_check_command from an independent source (orders, logs, account status)"),
        ("F", "full_set_command", "all/every claim needs full_set_command covering the whole set, not a sample"),
    ):
        if category != cat:
            continue
        extra = claim.get(key)
        if not extra:
            problems.append(msg)
            continue
        run = run_command(extra, timeout, cwd)
        result["runs"][key] = run
        if not run["has_data"]:
            problems.append(f"{key} returned no data")
    return result


# ---------------------------------------------------------------- verifier model


def _watcher() -> Any:
    spec = importlib.util.spec_from_file_location("paf_watcher_for_factcheck", WATCHER_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _parse_payload(stdout: str) -> dict[str, Any]:
    """Accept a bare JSON object or a CLI envelope (structured_output / response / result)."""
    text = stdout.strip()
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(text))
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                candidates.append(json.loads(line))
                break
            except json.JSONDecodeError:
                continue
    for obj in candidates:
        if not isinstance(obj, dict):
            continue
        if "claims" in obj:
            return obj
        for key in ("structured_output", "response", "result"):
            inner = obj.get(key)
            if isinstance(inner, dict) and "claims" in inner:
                return inner
            if isinstance(inner, str) and inner.strip():
                try:
                    parsed = json.loads(inner)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict) and "claims" in parsed:
                    return parsed
        denied = obj.get("denied_actions")
        if denied:
            raise RuntimeError(f"verifier ended with denied tool calls: {denied}")
    raise RuntimeError("verifier returned no JSON object with a 'claims' list")


def call_verifier(family: str, model: str, effort: str | None, prompt: str, schema: dict[str, Any],
                  verifier_cmd: str | None, timeout: int = 600) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask the verifier model. Returns (payload, meta). verifier_cmd replaces the model CLI (tests)."""
    meta: dict[str, Any] = {"family": family, "model": model, "effort": effort or ""}
    if verifier_cmd:
        proc = subprocess.run(verifier_cmd, shell=True, input=json.dumps({"prompt": prompt, "schema": schema}),
                              capture_output=True, text=True, timeout=timeout)
        meta["cli"] = "override"
    else:
        pw = _watcher()
        schema_str = json.dumps(schema, separators=(",", ":"))
        with tempfile.TemporaryDirectory(prefix="paf-factcheck-verifier-") as empty_dir:
            if family == "gemini":
                cmd = [pw._ensure_executable_binary(os.environ.get("PAF_AGY_BIN", "agy"), "agy"),
                       "--output-format", "json", "--json-schema", schema_str,
                       "--disable-slash-commands", "--sandbox",
                       "--settings", pw._agy_confinement_settings(None),
                       "--model", model, f"-p={prompt}"]
                if effort:
                    cmd[-1:-1] = ["--effort", effort]
                proc = subprocess.run(cmd, cwd=empty_dir, env=pw._scrubbed_reviewer_env("gemini"),
                                      capture_output=True, text=True, timeout=timeout)
            else:
                # The verifier needs no files. Read stays available only so the CLI can finish a
                # structured-output turn; the allow rules and deny settings confine it to the empty
                # directory, so it cannot open the claims file, PHI in the working tree, or the key.
                key_file = Path(os.environ.get("PAF_CONSENSUS_KEY_FILE")
                                or Path.home() / ".config" / "paf" / "consensus.key")
                allow_rules, settings_json = pw._build_claude_confinement_settings(
                    Path(empty_dir), key_file, allow_write=False)
                cmd = [pw._ensure_executable_binary(os.environ.get("PAF_CLAUDE_BIN", "claude"), "claude"),
                       "-p", "--output-format", "json", "--json-schema", schema_str,
                       "--permission-mode", "dontAsk", "--setting-sources", "user", "--tools", "Read",
                       "--allowedTools", ",".join(allow_rules), "--settings", settings_json,
                       "--strict-mcp-config", "--disable-slash-commands", "--max-turns", "4", "--model", model]
                if effort:
                    cmd += ["--effort", effort]
                proc = subprocess.run(cmd, cwd=empty_dir, env=pw._scrubbed_reviewer_env("claude"), input=prompt,
                                      capture_output=True, text=True, timeout=timeout)
        meta["cli"] = family
    if proc.returncode != 0:
        raise RuntimeError(f"verifier exited {proc.returncode}: {proc.stderr.strip()[:400]}")
    try:
        envelope = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        meta["session_id"] = str(envelope.get("conversation_id") or envelope.get("session_id") or "")
    except (json.JSONDecodeError, IndexError, AttributeError):
        meta["session_id"] = ""
    return _parse_payload(proc.stdout), meta


def _registry_for_prompt(registry: dict[str, Any] | None) -> str:
    if not registry:
        return "(no sources registry for this instance)"
    clean = {k: v for k, v in registry.items() if not k.startswith("_")}
    return json.dumps(clean, indent=1, ensure_ascii=False)[:12000]


def blind_prompt(claims: list[dict[str, Any]], registry: dict[str, Any] | None, cwd: Path | None = None) -> str:
    items = "\n".join(f'- id={c["id"]}: {c["text"]}' for c in claims)
    return (
        "You are an independent fact verifier from a different model family than the author. You do NOT see the "
        "author's sources, commands or results. You cannot run anything and must not call any tool; a script runs "
        "the commands you return.\n"
        "For each statement below, name the authoritative source (the system of record, never a label, derived, "
        "cached or copied field; the registry lists which is which) and ONE read-only command that would prove or "
        "disprove it. Use only a registered runner from the registry, or grep/rg/cat/head/tail/wc/ls/jq/find, "
        "read-only git, read-only gh, or kubectl get/describe/logs. Commands run without a shell: no pipes, "
        "redirects, chaining, newlines, variables or command substitution. SQL must be a single "
        "SELECT/SHOW/DESCRIBE/EXPLAIN/WITH statement.\n"
        "Set expect='data' if the command returns rows/lines when the statement is true, or 'empty' if the "
        "statement is true only when nothing comes back (absence claims).\n"
        f"Commands run with working directory {cwd or '(unspecified)'}; use absolute paths for files elsewhere.\n"
        "Return ONLY JSON matching the schema.\n\n"
        f"Sources registry:\n{_registry_for_prompt(registry)}\n\nStatements:\n{items}\n"
    )


def audit_prompt(items: list[dict[str, Any]], registry: dict[str, Any] | None, phi: bool) -> str:
    blob = json.dumps(items, indent=1, ensure_ascii=False)
    privacy = (
        "Query output is withheld for privacy; you see exit codes, line counts and normalized hashes. Equal hashes "
        "mean the two commands returned the same rows.\n" if phi else ""
    )
    return (
        "You are an adversarial fact auditor from a different model family than the author. For each claim you see "
        "the author's statement, source and command, your own independent (blind) proposal, and how both ran. They "
        "did not agree on source or result, so judge the author's version:\n"
        "1. source_is_ground_truth: is the author's source the system of record for this fact (not a label, derived, "
        "cached or copied field, and not another system's copy)?\n"
        "2. command_proves_statement: would that command's result actually prove the statement, or could it pass "
        "while the statement is false (missing scope, wrong table, wrong environment, sampling, NULL handling, "
        "time zone)?\n"
        "3. counterexample_commands: up to 3 read-only commands that must return NOTHING if the statement is true. "
        "A script runs them; any output disproves the claim. Same command rules as before: registered runner or "
        "read-only tool, no pipes, redirects, chaining or substitution; SQL is one SELECT.\n"
        "Set verdict='approve' only if 1 and 2 are both true. You cannot run anything; do not call tools.\n"
        f"{privacy}Return ONLY JSON matching the schema.\n\n"
        f"Sources registry:\n{_registry_for_prompt(registry)}\n\nClaims:\n{blob}\n"
    )


def _is_path(s: str) -> bool:
    return "/" in s or s.startswith("~")


def sources_match(a: str, b: str, cwd: Path | None = None) -> bool:
    """Same system of record. File paths compare as resolved paths; otherwise schema.table tokens."""
    if norm_source(a) and norm_source(a) == norm_source(b):
        return True
    if _is_path(a) or _is_path(b):
        def resolve(x: str) -> str:
            p = Path(x.strip()).expanduser()
            return str((cwd / p) if (cwd and not p.is_absolute()) else p.resolve() if p.is_absolute() else p)
        try:
            return Path(resolve(a)).resolve() == Path(resolve(b)).resolve()
        except OSError:
            return False
    return bool(source_tables(a) & source_tables(b))


def verify_claims(results: list[dict[str, Any]], claims: list[dict[str, Any]], registry: dict[str, Any] | None,
                  family: str, model: str, effort: str | None, phi: bool, timeout: int, cwd: Path | None,
                  verifier_cmd: str | None) -> dict[str, Any]:
    by_id = {c.get("id"): c for c in claims}
    targets = [r for r in results if r["label"] == "verified" and not r["problems"]]
    ver: dict[str, Any] = {"family": family, "model": model, "effort": effort or "",
                           "phi_mode": "hashes-only" if phi else "full", "claims": {}, "calls": []}
    if not targets:
        ver["status"] = "pass"
        return ver

    payload, meta = call_verifier(family, model, effort, blind_prompt(targets, registry, cwd), BLIND_SCHEMA, verifier_cmd)
    ver["calls"].append({"step": "blind", **meta})
    blind = {str(c.get("id")): c for c in payload.get("claims", []) if isinstance(c, dict)}

    diverged: list[dict[str, Any]] = []
    for r in targets:
        entry: dict[str, Any] = {"verdict": "pending"}
        ver["claims"][r["id"]] = entry
        b = blind.get(r["id"])
        if not b:
            entry["blind"] = {"missing": True}
            diverged.append(r)
            continue
        entry["blind"] = {"source": b.get("source", ""), "command": b.get("command", ""), "expect": b.get("expect", "data")}
        refused = readonly_problem(b.get("command", ""), registry)
        if refused:
            entry["blind"]["refused"] = refused
            diverged.append(r)
            continue
        run = run_command(b["command"], timeout, cwd, shell=False)
        entry["blind"]["run"] = strip_private(run)
        author_run = r["runs"]["command"]
        entry["source_match"] = sources_match(r["source"], b.get("source", ""), cwd)
        blind_expects_empty = b.get("expect") == "empty"
        author_negative = r["kind"] == "negative"
        if author_negative and blind_expects_empty:
            entry["result_match"] = (
                (run["exit_code"] == 0 or _negative_no_match("negative", run))
                and not run["output_lines"]
                and not author_run["output_lines"]
            )
        elif not author_negative and not blind_expects_empty:
            entry["result_match"] = run["has_data"] and run["normalized_sha256"] == author_run["normalized_sha256"]
        else:
            # The verifier reads the statement as the opposite kind of claim (presence vs absence);
            # an equal-looking result proves nothing, so the audit step decides.
            entry["result_match"] = False
        if entry["source_match"] and entry["result_match"]:
            entry["verdict"] = "pass"
            entry["by"] = "blind-equivalent"
        else:
            diverged.append(r)

    if diverged:
        items = []
        for r in diverged:
            e = ver["claims"][r["id"]]
            items.append({
                "id": r["id"],
                "statement": r["text"],
                "kind": r["kind"],
                "author": {"source": r["source"], "command": by_id[r["id"]].get("command", ""),
                           "run": run_summary(r["runs"].get("command"), phi)},
                "verifier_blind": {k: v for k, v in e.get("blind", {}).items() if k != "run"}
                | {"run": run_summary(e.get("blind", {}).get("run"), phi) if e.get("blind", {}).get("run") else {"ran": False}},
                "source_match": e.get("source_match"),
                "result_match": e.get("result_match"),
            })
        payload, meta = call_verifier(family, model, effort, audit_prompt(items, registry, phi), AUDIT_SCHEMA, verifier_cmd)
        ver["calls"].append({"step": "audit", **meta})
        audits = {str(c.get("id")): c for c in payload.get("claims", []) if isinstance(c, dict)}
        for r in diverged:
            e = ver["claims"][r["id"]]
            a = audits.get(r["id"])
            if not a:
                e["verdict"] = "fail"
                e["reason"] = "verifier returned no audit for this claim"
                continue
            e["audit"] = {k: a.get(k) for k in ("source_is_ground_truth", "command_proves_statement", "verdict", "reasoning")}
            e["counterexamples"] = []
            disproved = False
            for ce in (a.get("counterexample_commands") or [])[:3]:
                cmd = str((ce or {}).get("command") or "")
                item: dict[str, Any] = {"command": cmd, "why": (ce or {}).get("why", "")}
                refused = readonly_problem(cmd, registry)
                if refused:
                    item["refused"] = refused
                else:
                    run = run_command(cmd, timeout, cwd, shell=False)
                    item["run"] = strip_private(run)
                    if run["has_data"]:
                        disproved = True
                e["counterexamples"].append(item)
            ok = (a.get("verdict") == "approve" and a.get("source_is_ground_truth") is True
                  and a.get("command_proves_statement") is True and not disproved)
            e["verdict"] = "pass" if ok else "fail"
            e["by"] = "audit"
            if not ok:
                reasons = []
                if a.get("source_is_ground_truth") is not True:
                    reasons.append("verifier: source is not ground truth")
                if a.get("command_proves_statement") is not True:
                    reasons.append("verifier: command does not prove the statement")
                if disproved:
                    reasons.append("a counterexample command returned data")
                if a.get("verdict") != "approve" and not reasons:
                    reasons.append("verifier rejected the claim")
                e["reason"] = "; ".join(reasons)

    ver["status"] = "pass" if all(e.get("verdict") == "pass" for e in ver["claims"].values()) else "fail"
    return ver


# ---------------------------------------------------------------- signing


def _consensus_secret(wait_sec: float | None = None) -> tuple[str | None, str, str]:
    """(secret, key_id, reason_if_missing). Waits while `paf watch-prs` shields the key for a review."""
    import time
    wait = float(os.environ.get("PAF_FACTCHECK_KEY_WAIT_SEC", "90")) if wait_sec is None else wait_sec
    deadline = time.monotonic() + wait
    pw = _watcher()
    while True:
        try:
            secret = pw.load_consensus_secret(None)
        except Exception as exc:  # ConsensusKeyError: shielded, unreadable or truncated
            reason = str(exc)
            if "shielded" in reason and time.monotonic() < deadline:
                time.sleep(3)
                continue
            return None, "", reason[:200]
        if not secret:
            return None, "", "no consensus key (run `paf init-consensus-key`)"
        return secret, pw.compute_key_id(secret), ""


def canonical_receipt(receipt: dict[str, Any]) -> bytes:
    body = {k: v for k, v in receipt.items() if k not in ("sig", "receipt_path")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt: dict[str, Any]) -> None:
    secret, key_id, reason = _consensus_secret()
    if not secret:
        receipt["signed"] = False
        receipt["unsigned_reason"] = reason
        return
    receipt["signed"] = True
    receipt["key_id"] = key_id
    receipt["sig"] = hmac.new(secret.encode(), canonical_receipt(receipt), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- main flow


def run(claims_path: Path, timeout: int = DEFAULT_TIMEOUT, out_dir: Path | None = None, *,
        sources: Path | None = None, verify: bool = False, author_family: str = "claude",
        verifier_family: str | None = None, verifier_model: str | None = None, verifier_effort: str | None = None,
        allow_same_family: bool = False, phi: bool | None = None,
        verifier_cmd: str | None = None) -> tuple[int, dict[str, Any]]:
    spec = json.loads(claims_path.read_text())
    target = str(spec.get("target") or "").strip()
    claims = spec.get("claims") or []
    cwd = claims_path.parent
    registry = load_sources(sources)

    results = [check_claim(c, timeout, cwd, registry) for c in claims]

    global_problems = []
    if not target:
        global_problems.append("claims file needs a target (what the text or write goes to)")
    if not claims:
        global_problems.append("claims file lists no claims")

    verification: dict[str, Any] | None = None
    if verify:
        fam = verifier_family or ("gemini" if author_family == "claude" else "claude")
        if fam not in FAMILIES:
            global_problems.append(f"unknown verifier family {fam!r}")
        elif fam == author_family and not allow_same_family:
            global_problems.append("verifier must come from a different model family than the author")
        else:
            phi_mode = registry.get("phi", True) if (phi is None and registry) else (True if phi is None else phi)
            try:
                verification = verify_claims(results, claims, registry, fam,
                                             verifier_model or DEFAULT_VERIFIER_MODEL[fam], verifier_effort,
                                             bool(phi_mode), timeout, cwd, verifier_cmd)
                verification["author_family"] = author_family
            except Exception as exc:  # verifier outage or malformed output: fail closed
                verification = {"family": fam, "status": "error", "error": str(exc)[:500], "author_family": author_family}
                global_problems.append(f"dual-model verification failed: {str(exc)[:200]}")

    if verification and verification.get("status") == "pass":
        for r in results:
            if r["label"] == "verified" and r["id"] in verification.get("claims", {}):
                r["verifier_verdict"] = verification["claims"][r["id"]].get("verdict")
    elif verification and verification.get("status") == "fail":
        for r in results:
            e = (verification.get("claims") or {}).get(r["id"])
            if e and e.get("verdict") != "pass":
                r["problems"].append(f"dual-model verification failed: {e.get('reason', 'see receipt')}")

    passed_verified = {r["id"] for r in results if r["label"] == "verified" and not r["problems"]}
    for r, c in zip(results, claims):
        if r["label"] == "inferred":
            missing = [x for x in (c.get("from") or []) if x not in passed_verified]
            if missing:
                r["problems"].append(f"inferred from claims that are not passing verified claims: {missing}")
        for run_result in r["runs"].values():
            run_result.pop("_stdout", None)

    ok = not global_problems and all(not r["problems"] for r in results)
    # --verifier-cmd replaces the model CLI for tests. Such a receipt is never dual-model; the hook
    # also refuses it when PAF_FACTCHECK_REQUIRE_DUAL=1.
    override = bool(verification and any(c.get("cli") == "override" for c in verification.get("calls") or []))
    receipt: dict[str, Any] = {
        "version": 2,
        "target": target,
        "claims_file": str(claims_path.resolve()),
        "sources_registry": (registry or {}).get("_path", ""),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "status": "pass" if ok else "fail",
        "dual_model": bool(verification and verification.get("status") == "pass") and not override,
        "verifier_override": override,
        "problems": global_problems,
        "unchecked": [r["id"] for r in results if r["label"] == "unchecked"],
        "inferred": [r["id"] for r in results if r["label"] == "inferred"],
        "claims": results,
    }
    if verification is not None:
        receipt["verification"] = verification
    sign_receipt(receipt)
    out_dir = out_dir or receipts_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{stamp}-{slug(target)}.json"
    path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    receipt["receipt_path"] = str(path)
    return (0 if ok else 1), receipt


def print_summary(receipt: dict[str, Any]) -> None:
    dual = " dual-model" if receipt.get("dual_model") else ""
    if receipt.get("verifier_override"):
        dual = " (verifier overridden by --verifier-cmd; not dual-model)"
    print(f"fact-check {receipt['status'].upper()}{dual} target={receipt['target']}")
    for p in receipt["problems"]:
        print(f"  ! {p}")
    ver = receipt.get("verification") or {}
    for r in receipt["claims"]:
        mark = "ok  " if not r["problems"] else "FAIL"
        extra = f", {r['category']}" if r["category"] else ""
        v = (ver.get("claims") or {}).get(r["id"])
        vtag = f" [{v.get('by', v.get('verdict'))}]" if v else ""
        print(f"  [{mark}] {r['id']} ({r['label']}{extra}){vtag} {r['text'][:90]}")
        for p in r["problems"]:
            print(f"         - {p}")
        for n in r.get("notes", []):
            print(f"         . {n}")
    if receipt["inferred"]:
        print(f"  inferred (write them as inferences, naming the source): {', '.join(receipt['inferred'])}")
    if receipt["unchecked"]:
        print(f"  unchecked (say so in the text; never state as fact): {', '.join(receipt['unchecked'])}")
    sig_note = " (signed)" if receipt.get("signed") else f" (UNSIGNED: {receipt.get('unsigned_reason', '')})"
    print(f"receipt: {receipt['receipt_path']}{sig_note}")


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="paf factcheck", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("claims", type=Path, help="claims JSON file")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds per command")
    ap.add_argument("--out-dir", type=Path, default=None, help="receipt directory (default ~/.cache/paf/factcheck)")
    ap.add_argument("--sources", type=Path, default=None, help="sources registry JSON (default $PAF_FACTCHECK_SOURCES)")
    ap.add_argument("--verify", action="store_true", help="dual-model verification: blind proposal, then audit")
    ap.add_argument("--author-family", default="claude", choices=FAMILIES)
    ap.add_argument("--verifier-family", default=None, choices=FAMILIES)
    ap.add_argument("--verifier-model", default=None)
    ap.add_argument("--verifier-effort", default=None)
    ap.add_argument("--allow-same-family", action="store_true", help="allow a verifier from the author's family (recorded)")
    phi = ap.add_mutually_exclusive_group()
    phi.add_argument("--phi", dest="phi", action="store_true", default=None, help="send only hashes and counts to the verifier")
    phi.add_argument("--no-phi", dest="phi", action="store_false", help="allow output excerpts in the audit prompt")
    ap.add_argument("--verifier-cmd", default=os.environ.get("PAF_FACTCHECK_VERIFIER_CMD") or None,
                    help="command that replaces the model CLI (reads {prompt, schema} JSON on stdin); for tests")
    ap.add_argument("--json", action="store_true", help="print the receipt as JSON")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    code, receipt = run(
        args.claims, args.timeout, args.out_dir, sources=args.sources, verify=args.verify,
        author_family=args.author_family, verifier_family=args.verifier_family, verifier_model=args.verifier_model,
        verifier_effort=args.verifier_effort, allow_same_family=args.allow_same_family, phi=args.phi,
        verifier_cmd=args.verifier_cmd,
    )
    if args.json:
        print(json.dumps(receipt, indent=2, ensure_ascii=False))
    else:
        print_summary(receipt)
    return code


if __name__ == "__main__":
    sys.exit(main())
