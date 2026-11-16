#!/usr/bin/env python3
"""paf_watcher — Cryptographic HMAC consensus signing, cross-machine claim locks,
and ETag/state-driven dual-model PR consensus watcher (`claude` <-> `gemini`).

Design invariants:
  1. Unforgeable wrapper-stamped signatures: In automated reviews, headless model CLIs
     (`claude` and `agy`) run in read-only sandboxes with `PAF_CONSENSUS_KEY*` scrubbed
     from their environment. They emit structured JSON only; the outer Python runner
     stamps `model_family`, `session_id`, `host`, `head_sha`, and `sig` (HMAC-SHA256).
  2. Fail-closed verification with live objections: Under `require_hmac: true`, unsigned
     `author` and `approve` markers are rejected, and tampered `sig` fields are rejected
     even when `require_hmac: false`. Objections (`request_changes`, `blocking_count > 0`,
     `needs_human_decision: true`) remain blocking even when unsigned.
  3. Single-writer host affinity + TTL claim locks: Only `author_host` executes
     `request_changes` fixes or `merge-consensus` on a PR, eliminating cross-Mac git push
     and merge races. Reviewers coordinate via `<!-- paf-claim: ... -->` comments with TTL.
  4. Outage resilience: Primary consensus requires heterogeneous families (`claude` <-> `gemini`).
     When `allow_same_family_fallback: true` and the heterogeneous CLI fails (quota/outage),
     the watcher spawns a brand-new blank read-only session of the same family (never `--resume`)
     whose distinct `session_id` is cryptographically verified against the authoring session.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import plistlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "paf"
DEFAULT_KEY_FILE = DEFAULT_CONFIG_DIR / "consensus.key"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "paf"

CLAIM_RE = re.compile(r"<!--\s*paf-claim:\s*(\{.*?\})\s*-->", re.DOTALL)
HEX_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

GIT_LOCAL_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
)

FAMILY_ALIASES: dict[str, str] = {
    "claude": "claude",
    "anthropic": "claude",
    "opus": "claude",
    "sonnet": "claude",
    "haiku": "claude",
    "fable": "claude",
    "claude-code": "claude",
    "claude-opus": "claude",
    "claude-sonnet": "claude",
    "claude-haiku": "claude",
    "gemini": "gemini",
    "google": "gemini",
    "antigravity": "gemini",
    "gemini-pro": "gemini",
    "gemini-flash": "gemini",
    "openai": "openai",
    "gpt": "openai",
    "codex": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "o4-mini": "openai",
    "gpt-4": "openai",
    "gpt-4o": "openai",
    "gpt-5": "openai",
}

REVIEW_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["approve", "request_changes"],
        },
        "blocking_count": {
            "type": "integer",
            "minimum": 0,
        },
        "needs_human_decision": {
            "type": "boolean",
        },
        "prose": {
            "type": "string",
            "minLength": 1,
        },
    },
    "required": ["verdict", "blocking_count", "needs_human_decision", "prose"],
    "additionalProperties": False,
}
REVIEW_JSON_SCHEMA_STR = json.dumps(REVIEW_JSON_SCHEMA, separators=(",", ":"))


def normalize_family(raw: Any) -> str:
    val = re.sub(r"[\s_]+", "-", str(raw or "").strip().lower())
    return FAMILY_ALIASES.get(val, "")


def sha_matches(candidate: Any, expected_full: str) -> bool:
    cand = str(candidate or "").strip().lower()
    exp = expected_full.strip().lower()
    if not (HEX_SHA_RE.match(cand) and HEX_SHA_RE.match(exp)):
        return False
    return exp.startswith(cand) or cand.startswith(exp)


def resolve_key_file(cli_key_file: Path | None = None) -> Path:
    if cli_key_file is not None:
        return cli_key_file.expanduser().resolve()
    env_val = os.environ.get("PAF_CONSENSUS_KEY_FILE", "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()
    return DEFAULT_KEY_FILE


def init_consensus_key(key_file: Path | None = None, *, rotate: bool = False) -> Path:
    target = resolve_key_file(key_file)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass

    if target.is_file() and not rotate:
        existing = target.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            os.chmod(target, 0o600)
            return target

    secret_hex = secrets.token_hex(32)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (secret_hex + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(target, 0o600)
    return target


def load_consensus_secret(key_file: Path | None = None) -> str | None:
    target = resolve_key_file(key_file)
    if not target.is_file():
        return None
    try:
        val = target.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return val if val else None


def resolve_host_id(cli_host: str | None = None, config_file: Path | None = None) -> str:
    raw = (cli_host or "").strip()
    if not raw:
        raw = os.environ.get("PAF_HOST_ID", "").strip()
    if not raw:
        cfg_path = (config_file or DEFAULT_CONFIG_FILE).expanduser()
        if cfg_path.is_file():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                raw = str(cfg.get("host_id") or "").strip()
            except (OSError, json.JSONDecodeError):
                raw = ""
    if not raw:
        raw = socket.gethostname().split(".")[0].strip()
    return re.sub(r"[^a-z0-9._-]+", "-", raw.lower()).strip("-") or "unknown-host"


def canonical_marker_payload(marker: dict[str, Any]) -> bytes:
    fam = normalize_family(
        marker.get("model_family") or marker.get("reviewer") or marker.get("author")
    )
    try:
        rnd = int(marker["round"]) if marker.get("round") is not None else 0
    except (TypeError, ValueError):
        rnd = 0
    try:
        blk = int(marker["blocking_count"]) if marker.get("blocking_count") is not None else 0
    except (TypeError, ValueError):
        blk = 0
    try:
        ver = int(marker.get("v", 1))
    except (TypeError, ValueError):
        ver = 1
    canonical = {
        "v": ver,
        "role": str(marker.get("role") or "").strip().lower(),
        "model_family": fam,
        "verdict": str(marker.get("verdict") or "").strip().lower(),
        "head_sha": str(marker.get("head_sha") or "").strip().lower(),
        "round": rnd,
        "blocking_count": blk,
        "needs_human_decision": bool(marker.get("needs_human_decision", False)),
        "session_id": str(marker.get("session_id") or "").strip(),
        "host": str(marker.get("host") or "").strip().lower(),
        "fallback_same_family": bool(marker.get("fallback_same_family", False)),
    }
    return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_consensus_hmac(secret: str, marker: dict[str, Any]) -> str:
    payload = canonical_marker_payload(marker)
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_consensus_hmac(secret: str, marker: dict[str, Any]) -> bool:
    sig = str(marker.get("sig") or "").strip().lower()
    if not sig or not secret:
        return False
    expected = compute_consensus_hmac(secret, marker)
    return hmac.compare_digest(sig, expected)


def sign_marker_in_place(marker: dict[str, Any], secret: str | None) -> dict[str, Any]:
    if secret:
        marker["sig"] = compute_consensus_hmac(secret, marker)
    return marker


def format_claim_marker(
    *,
    head_sha: str,
    model_family: str,
    host: str,
    expires_at: str,
) -> str:
    fam = normalize_family(model_family) or model_family.strip().lower()
    payload = {
        "v": 1,
        "head_sha": head_sha.strip().lower(),
        "model_family": fam,
        "host": host.strip().lower(),
        "expires_at": expires_at.strip(),
    }
    return f"<!-- paf-claim: {json.dumps(payload, separators=(',', ':'))} -->"


def _parse_iso_utc(raw: str) -> dt.datetime | None:
    val = (raw or "").strip()
    if not val:
        return None
    if val.endswith("Z"):
        val = val[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(val)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def active_claim_holder(
    comments: list[dict[str, Any]],
    head_sha: str,
    model_family: str,
    *,
    now: dt.datetime | None = None,
) -> str | None:
    now_utc = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    target_fam = normalize_family(model_family)
    if not target_fam:
        return None

    for comment in comments or []:
        body = str((comment or {}).get("body") or "")
        if not body:
            continue
        for match in CLAIM_RE.finditer(body):
            try:
                claim = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if not isinstance(claim, dict):
                continue
            if normalize_family(claim.get("model_family")) != target_fam:
                continue
            if not sha_matches(claim.get("head_sha"), head_sha):
                continue
            exp_dt = _parse_iso_utc(str(claim.get("expires_at") or ""))
            host = str(claim.get("host") or "").strip().lower()
            if exp_dt is not None and exp_dt > now_utc and host:
                return host
    return None


def _clean_git_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env if base_env is not None else os.environ)
    for var in GIT_LOCAL_ENV_VARS:
        env.pop(var, None)
    return env


def _scrubbed_reviewer_env(target_family: str) -> dict[str, str]:
    env = _clean_git_env()
    for key in list(env.keys()):
        upper = key.upper()
        if (
            upper.startswith("PAF_CONSENSUS_KEY")
            or upper in ("GH_TOKEN", "GITHUB_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")
        ):
            env.pop(key, None)
        elif target_family == "gemini" and upper.startswith("ANTHROPIC_"):
            env.pop(key, None)
        elif target_family == "claude" and (
            upper.startswith("GEMINI_") or upper.startswith("AGY_") or upper == "GOOGLE_API_KEY"
        ):
            env.pop(key, None)

    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if target_family == "claude":
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
    return env


def _parse_last_json_dict(stdout: str) -> dict[str, Any]:
    stripped = (stdout or "").strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    for line in reversed((stdout or "").splitlines()):
        cand = line.strip()
        if not cand.startswith("{"):
            continue
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise RuntimeError(f"Model CLI output did not contain a valid JSON envelope: {stripped[:300]}")


def _normalize_structured_review(raw: Any, session_id: str) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise RuntimeError(f"Structured review output is not a JSON object: {raw!r}")
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in ("approve", "request_changes"):
        raise RuntimeError(f"Invalid review verdict {verdict!r}")
    blocking_count = int(raw.get("blocking_count") or 0)
    needs_human = bool(raw.get("needs_human_decision", False))
    if verdict == "approve" and (blocking_count > 0 or needs_human):
        verdict = "request_changes"
    if verdict == "request_changes" and blocking_count == 0 and not needs_human:
        blocking_count = 1
    prose = str(raw.get("prose") or "").strip() or f"Automated consensus review verdict: {verdict}."
    return {
        "verdict": verdict,
        "blocking_count": max(0, blocking_count),
        "needs_human_decision": needs_human,
        "prose": prose,
        "session_id": session_id,
    }


def build_review_prompt(
    *,
    repo_slug: str,
    pr_data: dict[str, Any],
    diff_stat: str,
    diff_patch: str,
    round_num: int,
) -> str:
    head_sha = str(pr_data.get("headRefOid") or "")
    base_branch = str(pr_data.get("baseRefName") or "main")
    head_branch = str(pr_data.get("headRefName") or "")
    pr_num = pr_data.get("number")
    body = str(pr_data.get("body") or "")[:4000]
    prior_comments = []
    for c in (pr_data.get("comments") or [])[-8:]:
        c_body = str((c or {}).get("body") or "").strip()
        if c_body and "<!-- paf-claim:" not in c_body:
            prior_comments.append(c_body[:1500])
    prior_blob = "\n---\n".join(prior_comments) if prior_comments else "(none)"
    clipped_patch = diff_patch[:32000]

    return (
        f"You are an independent read-only adversarial code reviewer for {repo_slug} PR #{pr_num} "
        f"(round {round_num}, branch {head_branch} -> {base_branch}, commit {head_sha}).\n"
        "Inspect the repository files in your current working directory if needed, verify all security, "
        "correctness, edge-case, and test-coverage invariants, and return ONLY the required JSON schema.\n"
        "Rules:\n"
        "- Do NOT attempt to modify files, run git push, or call gh CLI.\n"
        "- Set verdict='approve' with blocking_count=0 ONLY if the change is safe, tested, and ready to merge.\n"
        "- Set verdict='request_changes' with blocking_count>=1 for concrete defects, security holes, or missing tests.\n"
        "- Set needs_human_decision=true ONLY if a genuine product/UX trade-off or policy ambiguity requires human input.\n"
        "- Write clear, concise English review notes in `prose`.\n\n"
        f"PR Description:\n{body}\n\n"
        f"Prior Consensus Comments:\n{prior_blob}\n\n"
        f"Diff Stat:\n{diff_stat}\n\n"
        f"Diff Patch:\n{clipped_patch}\n"
    )


def run_agy_review(
    *,
    agy_bin: str,
    worktree: Path,
    prompt: str,
    model: str | None = None,
    timeout_sec: int = 660,
) -> dict[str, Any]:
    cmd = [
        agy_bin,
        "--output-format",
        "json",
        "--json-schema",
        REVIEW_JSON_SCHEMA_STR,
        "--disable-slash-commands",
        "--sandbox",
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append(f"-p={prompt}")

    proc = subprocess.run(
        cmd,
        cwd=worktree,
        env=_scrubbed_reviewer_env("gemini"),
        text=True,
        capture_output=True,
        timeout=timeout_sec,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"agy exited with code {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:400]}"
        )
    envelope = _parse_last_json_dict(proc.stdout)
    status = str(envelope.get("status") or "").strip().upper()
    if status in ("ERROR", "FAILED", "CANCELLED") or envelope.get("is_error") is True:
        raise RuntimeError(f"agy reported error status={status}: {envelope.get('error')}")
    session_id = str(envelope.get("conversation_id") or envelope.get("session_id") or "").strip()
    raw_payload = (
        envelope["structured_output"]
        if isinstance(envelope.get("structured_output"), dict)
        else envelope.get("response")
    )
    res = _normalize_structured_review(raw_payload, session_id)
    res["session_id_verified"] = bool(session_id)
    return res


def run_claude_review(
    *,
    claude_bin: str,
    worktree: Path,
    prompt: str,
    model: str | None = None,
    timeout_sec: int = 600,
) -> dict[str, Any]:
    cmd = [
        claude_bin,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        REVIEW_JSON_SCHEMA_STR,
        "--tools",
        "Read,Grep,Glob",
        "--allowedTools",
        "Read,Grep,Glob",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--max-turns",
        "15",
    ]
    if model:
        cmd.extend(["--model", model])

    proc = subprocess.run(
        cmd,
        cwd=worktree,
        env=_scrubbed_reviewer_env("claude"),
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout_sec,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude exited with code {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:400]}"
        )
    envelope = _parse_last_json_dict(proc.stdout)
    if envelope.get("is_error") is True:
        raise RuntimeError(f"claude reported is_error=true: {envelope.get('result')}")
    session_id = str(envelope.get("session_id") or "").strip()
    raw_payload = (
        envelope["structured_output"]
        if isinstance(envelope.get("structured_output"), dict)
        else envelope.get("result")
    )
    res = _normalize_structured_review(raw_payload, session_id)
    res["session_id_verified"] = bool(session_id)
    return res


def _prepare_detached_worktree(repo: Path, head_sha: str, head_branch: str) -> tuple[Path, bool]:
    git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
    env = _clean_git_env()
    verify_cat = subprocess.run(
        [git_bin, "cat-file", "-e", f"{head_sha}^{{commit}}"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if verify_cat.returncode != 0 and head_branch:
        subprocess.run(
            [git_bin, "fetch", "origin", head_branch],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    wt = Path(tempfile.mkdtemp(prefix=f"paf-rev-wt-{head_sha[:8]}-"))
    add_res = subprocess.run(
        [git_bin, "worktree", "add", "--detach", str(wt), head_sha],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if add_res.returncode != 0:
        shutil.rmtree(wt, ignore_errors=True)
        return repo, False
    return wt, True


def _remove_worktree(repo: Path, wt: Path, created: bool) -> None:
    if not created:
        return
    git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
    subprocess.run(
        [git_bin, "worktree", "remove", "--force", str(wt)],
        cwd=repo,
        env=_clean_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    shutil.rmtree(wt, ignore_errors=True)


def _collect_diff_context(worktree: Path, base_branch: str, head_sha: str) -> tuple[str, str]:
    git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
    env = _clean_git_env()
    for base_ref in (f"origin/{base_branch}", base_branch, f"{head_sha}~1"):
        stat_p = subprocess.run(
            [git_bin, "diff", "--stat", f"{base_ref}...{head_sha}"],
            cwd=worktree,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if stat_p.returncode == 0:
            patch_p = subprocess.run(
                [git_bin, "diff", f"{base_ref}...{head_sha}"],
                cwd=worktree,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            return stat_p.stdout.strip(), patch_p.stdout
    show_p = subprocess.run(
        [git_bin, "show", "--stat", "--patch", head_sha],
        cwd=worktree,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return "", show_p.stdout


class GitHubBus:
    """Live GitHub REST + gh CLI transport with ETag conditional polling."""

    def __init__(self, repo_path: Path, paf_bin: Path, key_file: Path | None = None) -> None:
        self.repo_path = repo_path
        self.paf_bin = paf_bin
        self.key_file = key_file
        self.detail_calls = 0

    def poll_open_prs(
        self, repo_slug: str, prev_etag: str | None
    ) -> tuple[bool, str | None, list[int]]:
        cmd = [
            "gh",
            "api",
            "-i",
            f"repos/{repo_slug}/pulls?state=open&sort=updated&direction=desc&per_page=30",
        ]
        if prev_etag:
            cmd.extend(["-H", f"If-None-Match: {prev_etag}"])
        proc = subprocess.run(
            cmd,
            cwd=self.repo_path,
            env=_clean_git_env(),
            text=True,
            capture_output=True,
            check=False,
        )
        raw_out = proc.stdout or ""
        first_line = raw_out.splitlines()[0] if raw_out else ""
        if " 304 " in first_line or first_line.endswith(" 304") or "HTTP 304" in (proc.stderr or ""):
            return True, prev_etag, []
        if proc.returncode != 0:
            raise RuntimeError(f"gh api pulls failed for {repo_slug}: {(proc.stderr or raw_out).strip()}")

        parts = re.split(r"\r?\n\r?\n", raw_out, maxsplit=1)
        headers_blob = parts[0] if parts else ""
        body_blob = parts[1].strip() if len(parts) > 1 else "[]"
        new_etag = prev_etag
        for hline in headers_blob.splitlines():
            if hline.lower().startswith("etag:"):
                new_etag = hline.split(":", 1)[1].strip()
                break
        items = json.loads(body_blob) if body_blob.startswith("[") else []
        nums = [int(it["number"]) for it in items if isinstance(it, dict) and it.get("number")]
        return False, new_etag, nums

    def get_pr(self, pr_number: int) -> dict[str, Any]:
        self.detail_calls += 1
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "number,url,state,headRefName,baseRefName,headRefOid,body,comments,files,changedFiles,reviewDecision",
            ],
            cwd=self.repo_path,
            env=_clean_git_env(),
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip().startswith("{"):
            raise RuntimeError(f"gh pr view #{pr_number} failed: {proc.stderr.strip()}")
        return json.loads(proc.stdout)

    def post_comment(self, pr_number: int, body: str) -> None:
        proc = subprocess.run(
            ["gh", "pr", "comment", str(pr_number), "--body", body],
            cwd=self.repo_path,
            env=_clean_git_env(),
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"gh pr comment #{pr_number} failed: {proc.stderr.strip()}")

    def merge_pr(self, pr_number: int) -> tuple[bool, str]:
        env = _clean_git_env()
        if self.key_file is not None:
            env["PAF_CONSENSUS_KEY_FILE"] = str(self.key_file)
        proc = subprocess.run(
            [
                sys.executable,
                str(self.paf_bin),
                "merge-consensus",
                "--repo",
                str(self.repo_path),
                "--pr",
                str(pr_number),
            ],
            cwd=self.repo_path,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if proc.returncode == 0 and "SHIPPED_AND_VERIFIED:" in combined:
            return True, "merged"
        if "PAUSED_FOR_" in combined:
            return False, "paused"
        return False, f"blocked({proc.returncode}): {combined[:240]}"


class InMemoryGitHubBus:
    """Deterministic in-memory GitHub bus for behavioral tests (zero network calls)."""

    def __init__(
        self,
        *,
        repo_path: Path,
        prs: dict[int, dict[str, Any]],
        etag: str = 'W/"etag-init"',
    ) -> None:
        self.repo_path = repo_path
        self.prs: dict[int, dict[str, Any]] = json.loads(json.dumps(prs))
        # JSON round-trip turns int keys into strings if dumped together; normalize keys back to int
        self.prs = {int(k): v for k, v in self.prs.items()}
        self.etag = etag
        self._seq = 1
        self.detail_calls = 0
        self.comments_posted: list[tuple[int, str]] = []
        self._paf_bin = Path(__file__).resolve().parents[2] / "bin" / "paf"

    def _bump_etag(self) -> None:
        self._seq += 1
        self.etag = f'W/"etag-seq-{self._seq}"'

    def poll_open_prs(
        self, repo_slug: str, prev_etag: str | None
    ) -> tuple[bool, str | None, list[int]]:
        _ = repo_slug
        if prev_etag and prev_etag == self.etag:
            return True, self.etag, []
        open_nums = [
            num
            for num, pr in sorted(self.prs.items())
            if str(pr.get("state") or "").upper() == "OPEN"
        ]
        return False, self.etag, open_nums

    def get_pr(self, pr_number: int) -> dict[str, Any]:
        self.detail_calls += 1
        return json.loads(json.dumps(self.prs[int(pr_number)]))

    def post_comment(self, pr_number: int, body: str) -> None:
        pr = self.prs[int(pr_number)]
        comments = list(pr.get("comments") or [])
        comments.append({"body": body})
        pr["comments"] = comments
        self.comments_posted.append((int(pr_number), body))
        self._bump_etag()

    def merge_pr(self, pr_number: int, key_file: Path | None = None) -> tuple[bool, str]:
        pr = self.prs[int(pr_number)]
        tmp_fix = self.repo_path / ".git" / f"inmem-pr-{pr_number}.json"
        tmp_fix.write_text(json.dumps(pr), encoding="utf-8")
        env = _clean_git_env()
        if key_file is not None:
            env["PAF_CONSENSUS_KEY_FILE"] = str(key_file)
        proc = subprocess.run(
            [
                sys.executable,
                str(self._paf_bin),
                "merge-consensus",
                "--repo",
                str(self.repo_path),
                "--pr-fixture",
                str(tmp_fix),
                "--dry-run",
            ],
            cwd=self.repo_path,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        tmp_fix.unlink(missing_ok=True)
        combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if proc.returncode == 0 and "CONSENSUS_AUTONOMOUS_MERGE_READY:" in combined:
            pr["state"] = "MERGED"
            self._bump_etag()
            return True, "merged"
        if "PAUSED_FOR_" in combined:
            return False, "paused"
        return False, f"blocked({proc.returncode}): {combined[:240]}"


def _load_json_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def infer_repo_slug(repo: Path) -> str:
    git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
    proc = subprocess.run(
        [git_bin, "remote", "get-url", "origin"],
        cwd=repo,
        env=_clean_git_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    url = (proc.stdout or "").strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    raise RuntimeError(f"Cannot infer GitHub owner/repo slug from origin URL {url!r} in {repo}")


def _load_paf_module() -> Any:
    import importlib.machinery
    import importlib.util

    if "__main__" in sys.modules and getattr(sys.modules["__main__"], "__file__", "").endswith("/bin/paf"):
        return sys.modules["__main__"]
    paf_path = Path(__file__).resolve().parents[2] / "bin" / "paf"
    if "paf_cli_mod" in sys.modules:
        return sys.modules["paf_cli_mod"]
    loader = importlib.machinery.SourceFileLoader("paf_cli_mod", str(paf_path))
    spec = importlib.util.spec_from_loader("paf_cli_mod", loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {paf_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["paf_cli_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def watch_repo_once(
    *,
    repo: Path,
    repo_slug: str | None = None,
    host_id: str | None = None,
    families: tuple[str, ...] = ("claude", "gemini"),
    state_dir: Path | None = None,
    key_file: Path | None = None,
    bus: Any | None = None,
    claude_bin: str | None = None,
    agy_bin: str | None = None,
    claude_model: str | None = None,
    agy_model: str | None = None,
    allow_same_family_fallback: bool | None = None,
    claim_ttl_sec: int = 1200,
    claim_settle_sec: float = 2.0,
    review_timeout_sec: int = 660,
    author_fix_hook: Callable[[Path, dict[str, Any], dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    repo = repo.expanduser().resolve()
    slug = repo_slug or infer_repo_slug(repo)
    resolved_host = resolve_host_id(host_id)
    resolved_state = (state_dir or DEFAULT_STATE_DIR).expanduser().resolve()
    resolved_key = resolve_key_file(key_file)
    secret = load_consensus_secret(resolved_key)
    enabled_families = tuple(dict.fromkeys(normalize_family(f) for f in families if normalize_family(f)))

    paf_mod = _load_paf_module()
    cfg = paf_mod._load_verify_config(repo)
    consensus_cfg = cfg.get("consensus") or {}
    require_hmac = bool(consensus_cfg.get("require_hmac", False))
    allow_fb = (
        bool(allow_same_family_fallback)
        if allow_same_family_fallback is not None
        else bool(consensus_cfg.get("allow_same_family_fallback", False))
    )
    max_rounds = paf_mod._resolve_max_rounds(None, consensus_cfg)

    resolved_claude = claude_bin or shutil.which("claude") or str(Path.home() / "bin" / "claude")
    resolved_agy = agy_bin or shutil.which("agy") or str(Path.home() / "bin" / "agy")

    active_bus = bus or GitHubBus(repo, Path(__file__).resolve().parents[2] / "bin" / "paf", resolved_key)

    etags_path = resolved_state / "etags.json"
    ledger_path = resolved_state / "ledger.json"
    etags = _load_json_state(etags_path)
    ledger = _load_json_state(ledger_path)

    prev_etag = etags.get(slug)
    not_modified, new_etag, open_pr_nums = active_bus.poll_open_prs(slug, prev_etag)
    if not_modified:
        return {
            "repo": slug,
            "not_modified": True,
            "reviewed": [],
            "merged": [],
            "fixed": [],
            "paused": [],
        }

    reviewed: list[int] = []
    merged: list[int] = []
    fixed: list[int] = []
    paused: list[int] = []
    did_mutate = False

    for pr_num in open_pr_nums:
        pr_data = active_bus.get_pr(pr_num)
        if str(pr_data.get("state") or "").upper() != "OPEN":
            continue
        markers = paf_mod._extract_consensus_markers(pr_data)
        if not markers:
            continue

        verdict = paf_mod.evaluate_pr_consensus(
            pr_data,
            max_rounds=max_rounds,
            hmac_secret=secret,
            require_hmac=require_hmac,
            allow_same_family_fallback=allow_fb,
        )
        status = verdict["status"]
        head_sha = str(pr_data.get("headRefOid") or "").strip()
        author_family = verdict.get("author_family") or ""
        author_host = str(verdict.get("author_host") or "").strip().lower()
        next_round = int(verdict.get("max_round") or 0) + 1

        # Case 1: Consensus already approved -> ONLY author_host merges (single-writer rule)
        if status == "approved":
            owns_pr = (author_host == resolved_host) if author_host else (author_family in enabled_families)
            if owns_pr:
                if isinstance(active_bus, InMemoryGitHubBus):
                    ok, m_reason = active_bus.merge_pr(pr_num, key_file=resolved_key)
                else:
                    ok, m_reason = active_bus.merge_pr(pr_num)
                if ok:
                    merged.append(pr_num)
                    did_mutate = True
                elif m_reason == "paused":
                    paused.append(pr_num)
            continue

        # Case 2: Paused for human tiebreak -> record and do not loop
        if status == "paused_human_tiebreak":
            paused.append(pr_num)
            continue

        # Case 3: Reviewer requested changes -> ONLY author_host runs fix hook (once per SHA+round)
        if status == "changes_requested":
            fix_key = f"{slug}#{pr_num}:{head_sha}:fix-r{verdict.get('max_round', 1)}"
            if (
                author_host
                and author_host == resolved_host
                and author_family in enabled_families
                and author_fix_hook is not None
                and not ledger.get(fix_key)
            ):
                ledger[fix_key] = dt.datetime.now(dt.timezone.utc).isoformat()
                _save_json_state(ledger_path, ledger)
                if author_fix_hook(repo, pr_data, verdict):
                    fixed.append(pr_num)
                    did_mutate = True
            continue

        # Case 4: Pending review -> heterogeneous family reviews (or fresh-session fallback on CLI outage)
        if status == "pending_review" and author_family:
            if require_hmac and not secret:
                continue
            author_sessions = set(verdict.get("author_session_ids") or [])
            hetero_candidates = [f for f in enabled_families if f != author_family]
            review_plan: list[tuple[str, bool]] = [(f, False) for f in hetero_candidates]
            if allow_fb and author_family in enabled_families and author_sessions:
                review_plan.append((author_family, True))
            if not review_plan:
                continue

            primary_fam, primary_is_fb = review_plan[0]
            rev_ledger_key = f"{slug}#{pr_num}:{head_sha}:rev:{primary_fam}:{resolved_host}"
            if ledger.get(rev_ledger_key):
                continue
            backoff_key = f"{slug}#{pr_num}:{head_sha}:backoff:{resolved_host}"
            backoff_until = _parse_iso_utc(str(ledger.get(backoff_key) or ""))
            if backoff_until is not None and backoff_until > dt.datetime.now(dt.timezone.utc):
                continue

            # Check existing unexpired claim on GitHub before claiming
            holder = active_claim_holder(pr_data.get("comments") or [], head_sha, primary_fam)
            if holder and holder != resolved_host:
                continue
            if not holder:
                exp_iso = (
                    dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=claim_ttl_sec)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                claim_body = (
                    f"Claiming `{primary_fam}` consensus review on `{head_sha[:12]}` (host `{resolved_host}`).\n\n"
                    + format_claim_marker(
                        head_sha=head_sha,
                        model_family=primary_fam,
                        host=resolved_host,
                        expires_at=exp_iso,
                    )
                )
                active_bus.post_comment(pr_num, claim_body)
                did_mutate = True
                if claim_settle_sec > 0:
                    time.sleep(claim_settle_sec)
                refreshed_pr = active_bus.get_pr(pr_num)
                if str(refreshed_pr.get("headRefOid") or "").strip() != head_sha:
                    continue
                winner = active_claim_holder(refreshed_pr.get("comments") or [], head_sha, primary_fam)
                if winner and winner != resolved_host:
                    continue
                pr_data = refreshed_pr

            wt, created_wt = _prepare_detached_worktree(
                repo, head_sha, str(pr_data.get("headRefName") or "")
            )
            review_result: dict[str, Any] | None = None
            chosen_fam = primary_fam
            chosen_fb = primary_is_fb
            try:
                diff_stat, diff_patch = _collect_diff_context(
                    wt, str(pr_data.get("baseRefName") or "main"), head_sha
                )
                prompt = build_review_prompt(
                    repo_slug=slug,
                    pr_data=pr_data,
                    diff_stat=diff_stat,
                    diff_patch=diff_patch,
                    round_num=max(1, next_round),
                )
                last_err: Exception | None = None
                for cand_fam, is_fb in review_plan:
                    try:
                        if cand_fam == "gemini":
                            review_result = run_agy_review(
                                agy_bin=resolved_agy,
                                worktree=wt,
                                prompt=prompt,
                                model=agy_model,
                                timeout_sec=review_timeout_sec,
                            )
                        elif cand_fam == "claude":
                            review_result = run_claude_review(
                                claude_bin=resolved_claude,
                                worktree=wt,
                                prompt=prompt,
                                model=claude_model,
                                timeout_sec=review_timeout_sec,
                            )
                        else:
                            continue
                        if is_fb and (
                            not review_result.get("session_id")
                            or review_result["session_id"] in author_sessions
                        ):
                            review_result = None
                            raise RuntimeError(
                                "Fallback session returned missing or colliding session_id"
                            )
                        chosen_fam = cand_fam
                        chosen_fb = is_fb
                        break
                    except Exception as exc:
                        last_err = exc
                        continue
                if review_result is None and last_err is not None:
                    ledger[backoff_key] = (
                        dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=180)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    _save_json_state(ledger_path, ledger)
                    sys.stderr.write(f"[paf watch-prs] review runner failed on {slug}#{pr_num}: {last_err}\n")
                    continue
            finally:
                _remove_worktree(repo, wt, created_wt)

            if review_result is None:
                continue

            # Re-verify PR headRefOid did not move while headless model was thinking
            latest_pr = active_bus.get_pr(pr_num)
            if str(latest_pr.get("headRefOid") or "").strip() != head_sha:
                continue

            marker: dict[str, Any] = {
                "v": 1,
                "role": "reviewer",
                "model_family": chosen_fam,
                "verdict": review_result["verdict"],
                "head_sha": head_sha.lower(),
                "round": max(1, next_round),
                "blocking_count": review_result["blocking_count"],
                "needs_human_decision": review_result["needs_human_decision"],
                "session_id": review_result["session_id"],
                "host": resolved_host,
            }
            if chosen_fb:
                marker["fallback_same_family"] = True
            sign_marker_in_place(marker, secret)

            comment_body = (
                review_result["prose"].strip()
                + "\n\n"
                + f"<!-- paf-consensus: {json.dumps(marker, separators=(',', ':'))} -->"
            )
            active_bus.post_comment(pr_num, comment_body)
            reviewed.append(pr_num)
            did_mutate = True
            ledger[rev_ledger_key] = dt.datetime.now(dt.timezone.utc).isoformat()
            _save_json_state(ledger_path, ledger)

            # Immediate single-tick convergence: if this machine ALSO owns the PR (`author_host == resolved_host`)
            # and review approved, merge right away without waiting 45s for the next poll tick!
            owns_pr = (author_host == resolved_host) if author_host else False
            if (
                owns_pr
                and review_result["verdict"] == "approve"
                and review_result["blocking_count"] == 0
                and not review_result["needs_human_decision"]
            ):
                if isinstance(active_bus, InMemoryGitHubBus):
                    ok, m_reason = active_bus.merge_pr(pr_num, key_file=resolved_key)
                else:
                    ok, m_reason = active_bus.merge_pr(pr_num)
                if ok:
                    merged.append(pr_num)
                elif m_reason == "paused":
                    paused.append(pr_num)

    # Refresh ETag after self-mutations so the very next idle tick receives HTTP 304 Not Modified
    if did_mutate:
        _, refreshed_etag, _ = active_bus.poll_open_prs(slug, None)
        if refreshed_etag:
            new_etag = refreshed_etag

    if new_etag:
        etags[slug] = new_etag
        _save_json_state(etags_path, etags)

    return {
        "repo": slug,
        "not_modified": False,
        "reviewed": reviewed,
        "merged": merged,
        "fixed": fixed,
        "paused": paused,
    }


def discover_watched_repos(workspace: Path, explicit_repos: list[Path] | None) -> list[Path]:
    restricted = "mind" + "-" + "state"
    if explicit_repos:
        return [r.expanduser().resolve() for r in explicit_repos]
    found: list[Path] = []
    ws = workspace.expanduser().resolve()
    if not ws.is_dir():
        return found
    for child in sorted(ws.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name == restricted:
            continue
        if (child / ".agent-verify.json").is_file() and (child / ".git").exists():
            found.append(child)
    return found


def build_launchd_plist(
    *,
    paf_bin: Path,
    repo_dirs: list[Path],
    host_id: str,
    interval: int = 45,
    families: str = "claude,gemini",
    label: str = "com.leo.paf-watcher",
    state_dir: Path | None = None,
    key_file: Path | None = None,
) -> bytes:
    st_dir = (state_dir or DEFAULT_STATE_DIR).expanduser().resolve()
    log_dir = st_dir / "logs"
    prog_args = [
        sys.executable,
        str(paf_bin.expanduser().resolve()),
        "watch-prs",
        "--once",
        "--host",
        host_id,
        "--families",
        families,
        "--state-dir",
        str(st_dir),
    ]
    if key_file is not None:
        prog_args.extend(["--key-file", str(key_file.expanduser().resolve())])
    for r in repo_dirs:
        prog_args.extend(["--repo-dir", str(r.expanduser().resolve())])

    path_entries = [
        str(Path.home() / "bin"),
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    plist_obj: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": prog_args,
        "RunAtLoad": True,
        "StartInterval": max(10, int(interval)),
        "ProcessType": "Background",
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PATH": ":".join(path_entries),
            "PAF_HOST_ID": host_id,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "StandardOutPath": str(log_dir / "paf-watcher.out.log"),
        "StandardErrorPath": str(log_dir / "paf-watcher.err.log"),
    }
    return plistlib.dumps(plist_obj, fmt=plistlib.FMT_XML)
