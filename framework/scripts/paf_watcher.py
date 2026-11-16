#!/usr/bin/env python3
"""paf_watcher — Cryptographic HMAC consensus signing, cross-machine claim locks,
and ETag/state-driven dual-model PR consensus watcher (`claude` <-> `gemini`).

Design invariants:
  1. Unforgeable wrapper-stamped signatures: In automated reviews, headless model CLIs
     (`claude` and `agy`) run in read-only sandboxes with `PAF_CONSENSUS_KEY*` scrubbed
     from their environment. They emit structured JSON only; the outer Python runner
     stamps `model_family`, `session_id`, `host`, `head_sha`, and `sig` (HMAC-SHA256).
  2. Fail-closed verification with live objections: Under `require_hmac: true` (the default
     for `watch-prs` and `project-agent-factory`), unsigned (`sig` missing/stripped) and
     tampered `author`/`approve` markers fail closed. When a repo explicitly sets
     `require_hmac: false` for migration, unsigned markers are accepted while any present
     `sig` that fails HMAC still fails closed. Objections (`request_changes`,
     `blocking_count > 0`, `needs_human_decision: true`) remain blocking even when unsigned.
  3. Single-writer host affinity + TTL claim locks: Only `author_host` executes
     `request_changes` fixes or `merge-consensus` on a PR, eliminating cross-Mac git push
     and merge races. Reviewers coordinate via `<!-- paf-claim: ... -->` comments with TTL.
  4. Outage resilience: Primary consensus requires heterogeneous families (`claude` <-> `gemini`).
     When `allow_same_family_fallback: true` and the heterogeneous CLI fails (quota/outage),
     the watcher spawns a brand-new blank read-only session of the same family (never `--resume`)
     whose distinct `session_id` is cryptographically verified against the authoring session.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import fcntl
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
import uuid
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

AUTHOR_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "commit_message": {
            "type": "string",
            "minLength": 1,
        },
        "reply_prose": {
            "type": "string",
            "minLength": 1,
        },
        "needs_human_decision": {
            "type": "boolean",
        },
    },
    "required": ["commit_message", "reply_prose", "needs_human_decision"],
    "additionalProperties": False,
}
AUTHOR_JSON_SCHEMA_STR = json.dumps(AUTHOR_JSON_SCHEMA, separators=(",", ":"))


def normalize_family(raw: Any) -> str:
    val = re.sub(r"[\s_]+", "-", str(raw or "").strip().lower())
    return FAMILY_ALIASES.get(val, "")


def sha_matches(candidate: Any, expected_full: str) -> bool:
    cand = str(candidate or "").strip().lower()
    exp = expected_full.strip().lower()
    if not (HEX_SHA_RE.match(cand) and HEX_SHA_RE.match(exp)):
        return False
    return exp.startswith(cand) or cand.startswith(exp)


class ConsensusKeyError(RuntimeError):
    """Raised when consensus.key exists on disk but is locked, unreadable, or invalid."""


class ReviewerOutageError(RuntimeError):
    """Raised strictly on subprocess timeout or recognized API quota/5xx error on stderr."""


CREDENTIAL_LEAK_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\boauth_token\s*:\s*['\"]?[A-Za-z0-9_-]{20,}"),
)


def _key_lock_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.lock")


@contextlib.contextmanager
def _key_file_lock(
    target: Path,
    *,
    exclusive: bool,
    wait_sec: float | None = 3.0,
) -> Any:
    if not target.parent.is_dir():
        if exclusive:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        else:
            yield
            return
    lock_path = _key_lock_path(target)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    locked = False
    try:
        if wait_sec is None:
            fcntl.flock(fd, mode)
            locked = True
        else:
            deadline = time.monotonic() + max(0.0, wait_sec)
            while True:
                try:
                    fcntl.flock(fd, mode | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise ConsensusKeyError(
                            f"Consensus key {target} is temporarily shielded by an active `paf watch-prs` sandboxed review; wait for the review to finish and retry."
                        ) from exc
                    time.sleep(0.05)
        # Self-heal if a prior `_shield_key_file` holder died via SIGKILL leaving `shielding` marker behind
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            marker_bytes = os.read(fd, 32)
            if marker_bytes.startswith(b"shielding") and os.path.lexists(target):
                os.chmod(target, 0o600)
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
        except OSError:
            pass
        yield
    finally:
        if locked:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def resolve_key_file(cli_key_file: Path | None = None) -> Path:
    if cli_key_file is not None:
        return cli_key_file.expanduser().resolve()
    env_val = os.environ.get("PAF_CONSENSUS_KEY_FILE", "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()
    return DEFAULT_KEY_FILE


def init_consensus_key(
    key_file: Path | None = None,
    *,
    rotate: bool = False,
    wait_sec: float = 3.0,
) -> Path:
    target = resolve_key_file(key_file)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass

    with _key_file_lock(target, exclusive=True, wait_sec=wait_sec):
        if os.path.lexists(target) and not rotate:
            try:
                existing = target.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ConsensusKeyError(
                    f"Consensus key {target} exists on disk but could not be read ({exc}); restore 0600 permissions or pass --rotate."
                ) from exc
            if len(existing) >= 32:
                os.chmod(target, 0o600)
                return target

        secret_hex = secrets.token_hex(32)
        if os.path.lexists(target):
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (secret_hex + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(target, 0o600)
        return target


def load_consensus_secret(
    key_file: Path | None = None,
    *,
    wait_sec: float = 2.0,
) -> str | None:
    target = resolve_key_file(key_file)
    if not os.path.lexists(target):
        return None
    with _key_file_lock(target, exclusive=False, wait_sec=wait_sec):
        if not os.path.lexists(target):
            return None
        try:
            val = target.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConsensusKeyError(
                f"Consensus key {target} exists on disk but is unreadable ({exc}); retry after active review completes or check permissions."
            ) from exc
        if len(val) < 16:
            raise ConsensusKeyError(
                f"Consensus key {target} exists on disk but is empty or truncated; run `paf init-consensus-key --rotate`."
            )
        return val


def compute_key_id(secret: str) -> str:
    return hashlib.sha256(f"paf-key-id:v1:{secret}".encode("utf-8")).hexdigest()[:12]


@contextlib.contextmanager
def _shield_key_file(key_file: Path | None) -> Any:
    """Acquire exclusive flock and set 0000 on consensus.key while untrusted model CLI runs."""
    target = key_file.expanduser().resolve() if key_file is not None else None
    if target is None or not os.path.lexists(target):
        yield
        return
    lock_path = _key_lock_path(target)
    with _key_file_lock(target, exclusive=True, wait_sec=None):
        shielded = False
        if os.path.lexists(target):
            try:
                lock_path.write_bytes(b"shielding\n")
                os.chmod(target, 0o000)
                shielded = True
            except OSError:
                shielded = False
        try:
            yield
        finally:
            if shielded and os.path.lexists(target):
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
            try:
                if lock_path.is_file():
                    lock_path.write_bytes(b"")
            except OSError:
                pass


def contains_secret_material(text: str, secret: str | None) -> bool:
    if not text:
        return False
    for pat in CREDENTIAL_LEAK_PATTERNS:
        if pat.search(text):
            return True
    if not secret:
        return False
    sec = secret.strip()
    if len(sec) < 16:
        return sec.lower() in text.lower()
    low_text = text.lower()
    compact_text = re.sub(r"[\s:_-]+", "", low_text)
    sec_low = sec.lower()
    for i in range(0, len(sec_low) - 15, 4):
        chunk = sec_low[i : i + 16]
        if chunk in low_text or chunk in compact_text:
            return True
    try:
        b64 = base64.b64encode(bytes.fromhex(sec)).decode("ascii").rstrip("=").lower()
        if len(b64) >= 16 and b64[:16] in low_text:
            return True
    except ValueError:
        pass
    return False


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
    try:
        pr_num = int(marker["pr_number"]) if marker.get("pr_number") is not None else 0
    except (TypeError, ValueError):
        pr_num = 0
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
        "base_branch": str(marker.get("base_branch") or "").strip(),
        "pr_number": pr_num,
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


def sign_marker_in_place(
    marker: dict[str, Any],
    secret: str | None,
    *,
    key_file: Path | None = None,
    require_hmac: bool = False,
) -> dict[str, Any]:
    if secret:
        marker["key_id"] = compute_key_id(secret)
        marker["sig"] = compute_consensus_hmac(secret, marker)
        return marker
    resolved = resolve_key_file(key_file) if key_file is not None else None
    if require_hmac or (resolved is not None and os.path.lexists(resolved)):
        raise ConsensusKeyError(
            f"Refusing to emit unsigned consensus marker when HMAC is required or key file exists ({resolved or DEFAULT_KEY_FILE})."
        )
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
            or upper
            in (
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "CLAUDECODE",
                "CLAUDE_CODE_ENTRYPOINT",
                "CLAUDE_CODE_SSE_PORT",
                "CLAUDE_SESSION_ID",
                "AGY_CONVERSATION_ID",
                "PAF_SESSION_ID",
            )
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


def _normalize_structured_author_turn(raw: Any, session_id: str) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise RuntimeError(f"Structured author output is not a JSON object: {raw!r}")
    commit_msg = str(raw.get("commit_message") or "").strip() or "[paf] Address cross-model consensus review feedback"
    commit_msg = commit_msg.splitlines()[0].strip()[:140]
    reply_prose = str(raw.get("reply_prose") or "").strip() or "Addressed review feedback and verified locally."
    needs_human = bool(raw.get("needs_human_decision", False))
    return {
        "commit_message": commit_msg,
        "reply_prose": reply_prose,
        "needs_human_decision": needs_human,
        "session_id": session_id,
    }


def build_followup_review_prompt(
    *,
    repo_slug: str,
    pr_data: dict[str, Any],
    prev_sha: str,
    head_sha: str,
    diff_stat: str,
    delta_patch: str,
    author_reply: str,
    round_num: int,
) -> str:
    pr_num = pr_data.get("number")
    base_branch = str(pr_data.get("baseRefName") or "main")
    head_branch = str(pr_data.get("headRefName") or "")
    clipped_delta = (
        delta_patch[:64000] + "\n[... delta truncated at 64,000 chars; read files in working directory ...]\n"
        if len(delta_patch) > 64000
        else (delta_patch or "(no file diff between commits; see author reply)")
    )
    return (
        f"Continuing your persistent review session for {repo_slug} PR #{pr_num} "
        f"(round {round_num}, branch {head_branch} -> {base_branch}, previous commit {prev_sha}, new commit {head_sha}).\n"
        "Your working directory has been updated to the latest commit. Verify whether every blocking issue you "
        "raised in the prior round is resolved without regressions, and return ONLY the required JSON schema.\n"
        "Rules:\n"
        "- Do NOT attempt to modify files, run git push, or call gh CLI.\n"
        "- Set verdict='approve' with blocking_count=0 if all blocking issues are resolved and the branch is safe to merge.\n"
        "- Set verdict='request_changes' with blocking_count>=1 ONLY if concrete blocking defects remain or were introduced.\n"
        "- Set needs_human_decision=true ONLY if a genuine product/UX trade-off requires human input.\n\n"
        f"Author Reply (Round {round_num}):\n{author_reply[:6000]}\n\n"
        f"Overall Diff Stat vs {base_branch}:\n{diff_stat}\n\n"
        f"Incremental Delta ({prev_sha[:12]}..{head_sha[:12]}):\n{clipped_delta}\n"
    )


def build_author_fix_prompt(
    *,
    repo_slug: str,
    pr_data: dict[str, Any],
    diff_stat: str,
    reviewer_prose: str,
    round_num: int,
    is_resume: bool,
    verify_failure: str = "",
) -> str:
    pr_num = pr_data.get("number")
    head_sha = str(pr_data.get("headRefOid") or "")
    base_branch = str(pr_data.get("baseRefName") or "main")
    head_branch = str(pr_data.get("headRefName") or "")
    body = str(pr_data.get("body") or "")[:3000]
    if verify_failure:
        return (
            f"Local `paf verify --mode pre-push` failed on your changes for {repo_slug} PR #{pr_num}.\n"
            "Fix the failing check(s) directly in the working directory files (do NOT run git commit/push or modify "
            "`.agent-verify.json` / `.github/workflows/`), keep all code/comments in English, and return the required JSON schema.\n\n"
            f"Verification Failure Output:\n{verify_failure[:8000]}\n"
        )
    header = (
        f"Continuing your persistent author session for {repo_slug} PR #{pr_num} (round {round_num}, commit {head_sha}).\n"
        if is_resume
        else (
            f"You are the authoring model session maintaining {repo_slug} PR #{pr_num} "
            f"(branch {head_branch} -> {base_branch}, commit {head_sha}).\n"
            f"PR Description:\n{body}\n\n"
            f"Branch Diff Stat vs {base_branch}:\n{diff_stat}\n\n"
        )
    )
    return (
        header
        + "The peer heterogeneous reviewer requested changes below. Inspect and edit files directly in your working "
        "directory to fix every valid defect and add/update tests as needed.\n"
        "Rules:\n"
        "- Do NOT run `git commit`, `git push`, or `gh`; the outer orchestrator runs `paf verify`, commits, and pushes.\n"
        "- Do NOT modify `.agent-verify.json` or `.github/workflows/` during automated fix rounds.\n"
        "- All code, comments, commit messages, and `reply_prose` MUST be in English (zero CJK characters in code).\n"
        "- If a point is genuinely a false positive, leave code unchanged and cite exact file/line proof in `reply_prose`.\n"
        "- Set `needs_human_decision=true` ONLY if resolving the issue requires a human product/UX decision.\n\n"
        f"Peer Reviewer Feedback (Round {round_num}):\n{reviewer_prose[:8000]}\n"
    )


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
    clipped_patch = (
        diff_patch[:64000] + "\n[... diff truncated at 64,000 chars; read remaining files in working directory ...]\n"
        if len(diff_patch) > 64000
        else diff_patch
    )

    return (
        f"You are an independent read-only adversarial code reviewer for {repo_slug} PR #{pr_num} "
        f"(round {round_num}, branch {head_branch} -> {base_branch}, commit {head_sha}).\n"
        "Inspect the repository files in your current working directory if needed, verify all security, "
        "correctness, edge-case, and test-coverage invariants, and return ONLY the required JSON schema.\n"
        "Rules:\n"
        "- Do NOT attempt to modify files, run git push, or call gh CLI.\n"
        "- Batch independent Read/Grep calls in parallel so you finish well within the turn budget.\n"
        "- Set verdict='approve' with blocking_count=0 ONLY if the change is safe, tested, and ready to merge.\n"
        "- Set verdict='request_changes' with blocking_count>=1 for concrete defects, security holes, or missing tests.\n"
        "- Set needs_human_decision=true ONLY if a genuine product/UX trade-off or policy ambiguity requires human input.\n"
        "- Write clear, concise English review notes in `prose`.\n\n"
        f"PR Description:\n{body}\n\n"
        f"Prior Consensus Comments:\n{prior_blob}\n\n"
        f"Diff Stat:\n{diff_stat}\n\n"
        f"Diff Patch:\n{clipped_patch}\n"
    )


STDERR_OUTAGE_RE = re.compile(
    r"\b(?:429|500|502|503|504|529)\b|"
    r"quota(?:\s+exhausted|\s+exceeded)?|"
    r"rate[-_ ]?limit(?:ed|\s+exceeded)?|"
    r"overloaded_error|resource[-_ ]?exhausted|service\s+unavailable",
    re.IGNORECASE,
)


def _ensure_executable_binary(bin_path: str, label: str) -> str:
    resolved = shutil.which(bin_path)
    if resolved:
        return resolved
    if os.path.isfile(bin_path) and os.access(bin_path, os.X_OK):
        return bin_path
    raise RuntimeError(f"{label} reviewer binary missing or not executable: {bin_path}")


def _raise_for_reviewer_exit(label: str, proc: subprocess.CompletedProcess[str]) -> None:
    stderr_text = (proc.stderr or "").strip()
    stdout_tail = (proc.stdout or "").strip()[:300]
    if proc.returncode != 2 and stderr_text and STDERR_OUTAGE_RE.search(stderr_text):
        raise ReviewerOutageError(
            f"{label} API quota/outage (rc={proc.returncode}): {stderr_text[:300]}"
        )
    detail = stderr_text[:300] or stdout_tail
    raise RuntimeError(f"{label} exited with code {proc.returncode}: {detail}")


def _snapshot_agy_brain_ids() -> set[str]:
    brain_dir = Path.home() / ".gemini" / "antigravity-cli" / "brain"
    if not brain_dir.is_dir():
        return set()
    try:
        return {p.name for p in brain_dir.iterdir() if p.is_dir()}
    except OSError:
        return set()


def _find_agy_session_by_nonce(pre_ids: set[str], nonce: str) -> str:
    if not nonce:
        return ""
    brain_dir = Path.home() / ".gemini" / "antigravity-cli" / "brain"
    if not brain_dir.is_dir():
        return ""
    try:
        candidates = [
            p
            for p in brain_dir.iterdir()
            if p.is_dir() and p.name not in pre_ids and SAFE_SESSION_TOKEN_RE.match(p.name)
        ]
    except OSError:
        return ""
    for cand in candidates:
        for rel in (Path(".system_generated") / "logs" / "transcript.jsonl", Path("transcript.jsonl")):
            tpath = cand / rel
            if not tpath.is_file():
                continue
            try:
                with tpath.open("r", encoding="utf-8", errors="ignore") as fh:
                    head_blob = fh.read(98304)
                if nonce in head_blob:
                    return cand.name
            except OSError:
                continue
    return ""


def _build_claude_confinement_settings(
    worktree: Path,
    key_file: Path | None,
    *,
    allow_write: bool = False,
) -> tuple[list[str], str]:
    wt_resolved = worktree.expanduser().resolve()
    wt_str = str(wt_resolved)
    wt_glob = "//" + wt_str.lstrip("/") + "/**"
    allow_rules = [
        f"Read({wt_glob})",
        f"Grep({wt_glob})",
        f"Glob({wt_glob})",
    ]
    if allow_write:
        allow_rules.extend(
            [
                f"Edit({wt_glob})",
                f"Write({wt_glob})",
            ]
        )
    deny_rules = [
        "Read(~/.config/**)",
        "Grep(~/.config/**)",
        "Glob(~/.config/**)",
        "Read(~/.ssh/**)",
        "Grep(~/.ssh/**)",
        "Glob(~/.ssh/**)",
        "Read(~/.aws/**)",
        "Grep(~/.aws/**)",
        "Glob(~/.aws/**)",
        "Read(~/.gnupg/**)",
        "Grep(~/.gnupg/**)",
        "Glob(~/.gnupg/**)",
        "Read(~/.kube/**)",
        "Grep(~/.kube/**)",
        "Glob(~/.kube/**)",
        "Read(~/.netrc)",
        "Read(~/.git-credentials)",
        "Read(~/.npmrc)",
        "Read(~/.pypirc)",
        "Read(**/.env)",
        "Read(**/.env.*)",
        "Grep(**/.env)",
        "Grep(**/.env.*)",
        "Read(**/*.key)",
        "Read(**/*.pem)",
        "Grep(**/*.key)",
        "Grep(**/*.pem)",
    ]
    if allow_write:
        wt_base = "//" + wt_str.lstrip("/")
        deny_rules.extend(
            [
                f"Edit({wt_base}/.agent-verify.json)",
                f"Write({wt_base}/.agent-verify.json)",
                f"Edit({wt_base}/.github/workflows/**)",
                f"Write({wt_base}/.github/workflows/**)",
                f"Edit({wt_base}/.claude/settings*.json)",
                f"Write({wt_base}/.claude/settings*.json)",
                f"Edit({wt_base}/.gemini/settings*.json)",
                f"Write({wt_base}/.gemini/settings*.json)",
                f"Edit({wt_base}/githooks/**)",
                f"Write({wt_base}/githooks/**)",
                f"Edit({wt_base}/framework/githooks/**)",
                f"Write({wt_base}/framework/githooks/**)",
                f"Edit({wt_base}/hooks/**)",
                f"Write({wt_base}/hooks/**)",
                f"Edit({wt_base}/framework/hooks/**)",
                f"Write({wt_base}/framework/hooks/**)",
                "Edit(**/.claude/settings*.json)",
                "Write(**/.claude/settings*.json)",
                "Edit(**/.gemini/settings*.json)",
                "Write(**/.gemini/settings*.json)",
                "Edit(**/.env)",
                "Edit(**/.env.*)",
                "Write(**/.env)",
                "Write(**/.env.*)",
            ]
        )
    for sys_root in ("/Users", "/home", "/etc", "/root"):
        if not (wt_str == sys_root or wt_str.startswith(sys_root + "/")):
            r_glob = "//" + sys_root.lstrip("/") + "/**"
            deny_rules.extend([f"Read({r_glob})", f"Grep({r_glob})", f"Glob({r_glob})"])
            if allow_write:
                deny_rules.extend([f"Edit({r_glob})", f"Write({r_glob})"])
    if key_file is not None:
        key_dir = str(key_file.expanduser().resolve().parent)
        if not (wt_str == key_dir or wt_str.startswith(key_dir + "/")):
            key_dir_slash = "//" + key_dir.lstrip("/") + "/**"
            deny_rules.extend(
                [
                    f"Read({key_dir_slash})",
                    f"Grep({key_dir_slash})",
                    f"Glob({key_dir_slash})",
                ]
            )
    settings_json = json.dumps(
        {
            "permissions": {
                "defaultMode": "dontAsk",
                "allow": allow_rules,
                "deny": deny_rules,
            }
        },
        separators=(",", ":"),
    )
    return allow_rules, settings_json


def run_agy_review(
    *,
    agy_bin: str,
    worktree: Path,
    prompt: str,
    model: str | None = None,
    resume_session_id: str | None = None,
    timeout_sec: int = 660,
) -> dict[str, Any]:
    exec_agy = _ensure_executable_binary(agy_bin, "agy")
    pre_ids = _snapshot_agy_brain_ids() if not resume_session_id else set()
    agy_nonce = f"paf-agy-nonce-{uuid.uuid4()}" if not resume_session_id else ""
    eff_prompt = f"{prompt}\n\n<!-- {agy_nonce} -->" if agy_nonce else prompt
    cmd = [
        exec_agy,
        "--output-format",
        "json",
        "--json-schema",
        REVIEW_JSON_SCHEMA_STR,
        "--disable-slash-commands",
        "--sandbox",
    ]
    if resume_session_id:
        cmd.extend(["--conversation", resume_session_id])
    if model:
        cmd.extend(["--model", model])
    cmd.append(f"-p={eff_prompt}")

    try:
        proc = subprocess.run(
            cmd,
            cwd=worktree,
            env=_scrubbed_reviewer_env("gemini"),
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        err = ReviewerOutageError(f"agy timed out after {timeout_sec}s")
        err.spawned_session_id = _find_agy_session_by_nonce(pre_ids, agy_nonce)  # type: ignore[attr-defined]
        raise err from exc
    if proc.returncode != 0:
        try:
            _raise_for_reviewer_exit("agy", proc)
        except BaseException as exc:
            exc.spawned_session_id = _find_agy_session_by_nonce(pre_ids, agy_nonce)  # type: ignore[attr-defined]
            raise
    envelope = _parse_last_json_dict(proc.stdout)
    status = str(envelope.get("status") or "").strip().upper()
    reported_sid = str(envelope.get("conversation_id") or envelope.get("session_id") or "").strip()
    if status in ("ERROR", "FAILED", "CANCELLED") or envelope.get("is_error") is True:
        err_rt = RuntimeError(f"agy reported error status={status}: {envelope.get('error')}")
        if reported_sid and not resume_session_id and reported_sid not in pre_ids:
            err_rt.spawned_session_id = reported_sid  # type: ignore[attr-defined]
        raise err_rt
    session_id = reported_sid or (resume_session_id or "")
    raw_payload = (
        envelope["structured_output"]
        if isinstance(envelope.get("structured_output"), dict)
        else envelope.get("response")
    )
    res = _normalize_structured_review(raw_payload, session_id)
    res["session_id_verified"] = bool(reported_sid or resume_session_id)
    res["session_created"] = bool(reported_sid and not resume_session_id and reported_sid not in pre_ids)
    return res


def run_claude_review(
    *,
    claude_bin: str,
    worktree: Path,
    prompt: str,
    model: str | None = None,
    key_file: Path | None = None,
    init_session_id: str | None = None,
    resume_session_id: str | None = None,
    timeout_sec: int = 600,
) -> dict[str, Any]:
    allow_rules, settings_json = _build_claude_confinement_settings(worktree, key_file, allow_write=False)
    exec_claude = _ensure_executable_binary(claude_bin, "claude")
    cmd = [
        exec_claude,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        REVIEW_JSON_SCHEMA_STR,
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "user",
        "--tools",
        "Read,Grep,Glob",
        "--allowedTools",
        ",".join(allow_rules),
        "--settings",
        settings_json,
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--max-turns",
        "35",
    ]
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])
    elif init_session_id:
        cmd.extend(["--session-id", init_session_id])
    if model:
        cmd.extend(["--model", model])

    try:
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
    except subprocess.TimeoutExpired as exc:
        raise ReviewerOutageError(f"claude timed out after {timeout_sec}s") from exc
    if proc.returncode != 0:
        _raise_for_reviewer_exit("claude", proc)
    envelope = _parse_last_json_dict(proc.stdout)
    if envelope.get("is_error") is True:
        raise RuntimeError(f"claude reported is_error=true: {envelope.get('result')}")
    reported_sid = str(envelope.get("session_id") or "").strip()
    session_id = reported_sid or (resume_session_id or init_session_id or "")
    raw_payload = (
        envelope["structured_output"]
        if isinstance(envelope.get("structured_output"), dict)
        else envelope.get("result")
    )
    res = _normalize_structured_review(raw_payload, session_id)
    res["session_id_verified"] = bool(reported_sid or resume_session_id or init_session_id)
    res["session_created"] = bool((init_session_id or reported_sid) and not resume_session_id)
    return res


def run_agy_author_turn(
    *,
    agy_bin: str,
    worktree: Path,
    prompt: str,
    model: str | None = None,
    resume_session_id: str | None = None,
    timeout_sec: int = 1200,
) -> dict[str, Any]:
    exec_agy = _ensure_executable_binary(agy_bin, "agy")
    pre_ids = _snapshot_agy_brain_ids() if not resume_session_id else set()
    agy_nonce = f"paf-agy-nonce-{uuid.uuid4()}" if not resume_session_id else ""
    eff_prompt = f"{prompt}\n\n<!-- {agy_nonce} -->" if agy_nonce else prompt
    cmd = [
        exec_agy,
        "--output-format",
        "json",
        "--json-schema",
        AUTHOR_JSON_SCHEMA_STR,
        "--disable-slash-commands",
        "--sandbox",
        "--mode",
        "accept-edits",
    ]
    if resume_session_id:
        cmd.extend(["--conversation", resume_session_id])
    if model:
        cmd.extend(["--model", model])
    cmd.append(f"-p={eff_prompt}")

    try:
        proc = subprocess.run(
            cmd,
            cwd=worktree,
            env=_scrubbed_reviewer_env("gemini"),
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        err = ReviewerOutageError(f"agy author session timed out after {timeout_sec}s")
        err.spawned_session_id = _find_agy_session_by_nonce(pre_ids, agy_nonce)  # type: ignore[attr-defined]
        raise err from exc
    if proc.returncode != 0:
        try:
            _raise_for_reviewer_exit("agy", proc)
        except BaseException as exc:
            exc.spawned_session_id = _find_agy_session_by_nonce(pre_ids, agy_nonce)  # type: ignore[attr-defined]
            raise
    envelope = _parse_last_json_dict(proc.stdout)
    status = str(envelope.get("status") or "").strip().upper()
    reported_sid = str(envelope.get("conversation_id") or envelope.get("session_id") or "").strip()
    if status in ("ERROR", "FAILED", "CANCELLED") or envelope.get("is_error") is True:
        err_rt = RuntimeError(f"agy author reported error status={status}: {envelope.get('error')}")
        if reported_sid and not resume_session_id and reported_sid not in pre_ids:
            err_rt.spawned_session_id = reported_sid  # type: ignore[attr-defined]
        raise err_rt
    session_id = reported_sid or (resume_session_id or "")
    raw_payload = (
        envelope["structured_output"]
        if isinstance(envelope.get("structured_output"), dict)
        else envelope.get("response")
    )
    res = _normalize_structured_author_turn(raw_payload, session_id)
    res["session_created"] = bool(reported_sid and not resume_session_id and reported_sid not in pre_ids)
    return res


def run_claude_author_turn(
    *,
    claude_bin: str,
    worktree: Path,
    prompt: str,
    model: str | None = None,
    key_file: Path | None = None,
    init_session_id: str | None = None,
    resume_session_id: str | None = None,
    timeout_sec: int = 1200,
) -> dict[str, Any]:
    allow_rules, settings_json = _build_claude_confinement_settings(worktree, key_file, allow_write=True)
    exec_claude = _ensure_executable_binary(claude_bin, "claude")
    cmd = [
        exec_claude,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        AUTHOR_JSON_SCHEMA_STR,
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "user",
        "--tools",
        "Read,Edit,Write,Grep,Glob",
        "--allowedTools",
        ",".join(allow_rules),
        "--settings",
        settings_json,
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--max-turns",
        "45",
    ]
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])
    elif init_session_id:
        cmd.extend(["--session-id", init_session_id])
    if model:
        cmd.extend(["--model", model])

    try:
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
    except subprocess.TimeoutExpired as exc:
        raise ReviewerOutageError(f"claude author session timed out after {timeout_sec}s") from exc
    if proc.returncode != 0:
        _raise_for_reviewer_exit("claude", proc)
    envelope = _parse_last_json_dict(proc.stdout)
    if envelope.get("is_error") is True:
        raise RuntimeError(f"claude author reported is_error=true: {envelope.get('result')}")
    reported_sid = str(envelope.get("session_id") or "").strip()
    session_id = reported_sid or (resume_session_id or init_session_id or "")
    raw_payload = (
        envelope["structured_output"]
        if isinstance(envelope.get("structured_output"), dict)
        else envelope.get("result")
    )
    res = _normalize_structured_author_turn(raw_payload, session_id)
    res["session_created"] = bool((init_session_id or reported_sid) and not resume_session_id)
    return res


def _prepare_detached_worktree(repo: Path, head_sha: str, head_branch: str) -> tuple[Path | None, bool]:
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
        return None, False
    return wt, True


def _prune_stale_worktrees(repo: Path) -> None:
    git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
    subprocess.run(
        [git_bin, "worktree", "prune"],
        cwd=repo,
        env=_clean_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def is_quota_or_outage_error(exc: BaseException) -> bool:
    return isinstance(exc, ReviewerOutageError)


def _remove_worktree(repo: Path, wt: Path | None, created: bool) -> None:
    if not created or wt is None:
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
    subprocess.run(
        [git_bin, "fetch", "origin", base_branch],
        cwd=worktree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    for base_ref in (f"origin/{base_branch}", base_branch):
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
            if patch_p.returncode == 0:
                return stat_p.stdout.strip(), patch_p.stdout
    return "", ""


def _collect_delta_diff(worktree: Path, prev_sha: str, head_sha: str) -> str:
    if not prev_sha or prev_sha == head_sha:
        return ""
    git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
    p = subprocess.run(
        [git_bin, "diff", f"{prev_sha}..{head_sha}"],
        cwd=worktree,
        env=_clean_git_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    return p.stdout if p.returncode == 0 else ""


def _ensure_pr_worktree(repo: Path, wt_path: Path, target_sha: str, fetch_branch: str) -> bool:
    git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
    env = _clean_git_env()
    verify_cat = subprocess.run(
        [git_bin, "cat-file", "-e", f"{target_sha}^{{commit}}"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if verify_cat.returncode != 0 and fetch_branch:
        subprocess.run(
            [git_bin, "fetch", "origin", fetch_branch],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    wt_resolved = wt_path.expanduser().resolve()
    if (wt_resolved / ".git").exists():
        subprocess.run([git_bin, "reset", "--hard"], cwd=wt_resolved, env=env, capture_output=True, check=False)
        subprocess.run([git_bin, "clean", "-fd"], cwd=wt_resolved, env=env, capture_output=True, check=False)
        chk = subprocess.run(
            [git_bin, "checkout", "--detach", target_sha],
            cwd=wt_resolved,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if chk.returncode == 0:
            cur = subprocess.run(
                [git_bin, "rev-parse", "HEAD"],
                cwd=wt_resolved,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if sha_matches(cur, target_sha):
                return True
        _remove_worktree(repo, wt_resolved, True)

    if wt_resolved.exists():
        shutil.rmtree(wt_resolved, ignore_errors=True)
    wt_resolved.parent.mkdir(parents=True, exist_ok=True)
    _prune_stale_worktrees(repo)
    add_res = subprocess.run(
        [git_bin, "worktree", "add", "--detach", str(wt_resolved), target_sha],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if add_res.returncode != 0:
        shutil.rmtree(wt_resolved, ignore_errors=True)
        return False
    return True


UUID_SAFE_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SAFE_SESSION_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


def close_consensus_sessions(session_ctx: dict[str, Any]) -> dict[str, Any]:
    """Deterministically close and clean up both the author and reviewer consensus sessions."""
    if session_ctx.get("closed"):
        return dict(session_ctx.get("close_receipt") or {})

    for proc in list(session_ctx.get("processes") or []):
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    claude_projects = Path.home() / ".claude" / "projects"
    cleaned_claude: list[str] = []
    if claude_projects.is_dir():
        for sid in sorted(session_ctx.get("spawned_claude_sessions") or set()):
            if not UUID_SAFE_RE.match(sid):
                continue
            for proj_dir in claude_projects.iterdir():
                if not proj_dir.is_dir():
                    continue
                jsonl = proj_dir / f"{sid}.jsonl"
                if jsonl.is_file():
                    jsonl.unlink(missing_ok=True)
                    cleaned_claude.append(sid)
                sdir = proj_dir / sid
                if sdir.is_dir():
                    shutil.rmtree(sdir, ignore_errors=True)
                try:
                    if not any(proj_dir.iterdir()):
                        proj_dir.rmdir()
                except OSError:
                    pass

    agy_brain = Path.home() / ".gemini" / "antigravity-cli" / "brain"
    cleaned_agy: list[str] = []
    if agy_brain.is_dir():
        for sid in sorted(session_ctx.get("spawned_agy_sessions") or set()):
            if not SAFE_SESSION_TOKEN_RE.match(sid):
                continue
            target_dir = agy_brain / sid
            if target_dir.is_dir():
                shutil.rmtree(target_dir, ignore_errors=True)
                cleaned_agy.append(sid)

    repo_root: Path | None = session_ctx.get("repo")
    for wt in list(session_ctx.get("worktrees") or []):
        if repo_root is not None and isinstance(wt, Path):
            _remove_worktree(repo_root, wt, True)
    if repo_root is not None:
        _prune_stale_worktrees(repo_root)

    closed_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    author_sid = str(session_ctx.get("author_session_id") or "")
    reviewer_sid = str(session_ctx.get("reviewer_session_id") or "")
    pr_num = session_ctx.get("pr_number")
    receipt = {
        "closed": True,
        "pr_number": pr_num,
        "author_session_id": author_sid,
        "reviewer_session_id": reviewer_sid,
        "cleaned_claude_sessions": sorted(set(cleaned_claude)),
        "cleaned_agy_sessions": sorted(set(cleaned_agy)),
        "closed_at": closed_at,
    }

    reg_path: Path | None = session_ctx.get("registry_path")
    reg_key = str(session_ctx.get("registry_key") or "")
    if isinstance(reg_path, Path) and reg_key:
        reg = _load_json_state(reg_path)
        active = reg.get("active") if isinstance(reg.get("active"), dict) else {}
        active.pop(reg_key, None)
        reg["active"] = active
        closed_map = reg.get("closed") if isinstance(reg.get("closed"), dict) else {}
        closed_map[reg_key] = receipt
        if len(closed_map) > 100:
            keys_sorted = sorted(closed_map.keys(), key=lambda k: str((closed_map[k] or {}).get("closed_at") or ""))
            closed_map = {k: closed_map[k] for k in keys_sorted[-100:]}
        reg["closed"] = closed_map
        _save_json_state(reg_path, reg)

    session_ctx["closed"] = True
    session_ctx["close_receipt"] = receipt
    on_close = session_ctx.get("close_hook")
    if callable(on_close):
        on_close(receipt)
    print(f"CLOSED_CONSENSUS_SESSIONS: pr=#{pr_num} author={author_sid or 'none'} reviewer={reviewer_sid or 'none'}")
    return receipt


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

    def update_pr_body(self, pr_number: int, body: str) -> None:
        proc = subprocess.run(
            ["gh", "pr", "edit", str(pr_number), "--body", body],
            cwd=self.repo_path,
            env=_clean_git_env(),
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"gh pr edit #{pr_number} failed: {proc.stderr.strip()}")

    def push_head(self, pr_number: int, new_sha: str, head_branch: str, source_wt: Path) -> None:
        git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
        push_env = _clean_git_env()
        push_env["PAF_PRE_PUSH_VERIFIED"] = new_sha
        proc = subprocess.run(
            [git_bin, "push", "origin", f"HEAD:refs/heads/{head_branch}"],
            cwd=source_wt,
            env=push_env,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git push failed for PR #{pr_number} ({head_branch}): {proc.stderr.strip()}")
        subprocess.run(
            [git_bin, "fetch", "origin", head_branch],
            cwd=self.repo_path,
            env=_clean_git_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        cur_br = subprocess.run(
            [git_bin, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.repo_path,
            env=_clean_git_env(),
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        clean_st = subprocess.run(
            [git_bin, "status", "--porcelain"],
            cwd=self.repo_path,
            env=_clean_git_env(),
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if cur_br == head_branch and not clean_st:
            subprocess.run(
                [git_bin, "merge", "--ff-only", new_sha],
                cwd=self.repo_path,
                env=_clean_git_env(),
                capture_output=True,
                text=True,
                check=False,
            )
        for _ in range(8):
            pr_now = self.get_pr(pr_number)
            if sha_matches(str(pr_now.get("headRefOid") or ""), new_sha):
                return
            time.sleep(0.8)

    def merge_pr(
        self,
        pr_number: int,
        *,
        require_hmac: bool = False,
        allow_same_family_fallback: bool = False,
        key_file: Path | None = None,
        approved_main: bool = False,
        max_wait_ci: int = 600,
        no_auto_revert: bool = False,
        host_id: str | None = None,
        on_merged_callback: Callable[[], None] | None = None,
    ) -> tuple[bool, str]:
        eff_key = key_file if key_file is not None else self.key_file
        if on_merged_callback is not None:
            paf_mod = _load_paf_module()
            rc, status_tag = paf_mod.merge_pr_in_process(
                repo=self.repo_path,
                pr_number=pr_number,
                key_file=eff_key,
                require_hmac=require_hmac,
                allow_same_family_fallback=bool(allow_same_family_fallback),
                approved_main=approved_main,
                max_wait_ci=max_wait_ci,
                no_auto_revert=no_auto_revert,
                host_id=host_id,
                on_merged_callback=on_merged_callback,
            )
            if rc == 0 and status_tag == "merged":
                return True, "merged"
            if status_tag == "paused":
                return False, "paused"
            return False, f"blocked({rc}): {status_tag}"

        env = _clean_git_env()
        if eff_key is not None:
            env["PAF_CONSENSUS_KEY_FILE"] = str(eff_key)
        cmd = [
            sys.executable,
            str(self.paf_bin),
            "merge-consensus",
            "--repo",
            str(self.repo_path),
            "--pr",
            str(pr_number),
            "--max-wait-ci",
            str(max_wait_ci),
        ]
        if require_hmac:
            cmd.append("--require-hmac")
        if allow_same_family_fallback:
            cmd.append("--allow-same-family-fallback")
        if approved_main:
            cmd.append("--approved-main")
        if no_auto_revert:
            cmd.append("--no-auto-revert")
        proc = subprocess.run(
            cmd,
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
        key_file: Path | None = None,
        run_post_merge_verify: bool = False,
    ) -> None:
        self.repo_path = repo_path
        self.prs: dict[int, dict[str, Any]] = json.loads(json.dumps(prs))
        # JSON round-trip turns int keys into strings if dumped together; normalize keys back to int
        self.prs = {int(k): v for k, v in self.prs.items()}
        self.etag = etag
        self.key_file = key_file
        self.run_post_merge_verify = run_post_merge_verify
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

    def update_pr_body(self, pr_number: int, body: str) -> None:
        pr = self.prs[int(pr_number)]
        pr["body"] = body
        self._bump_etag()

    def push_head(self, pr_number: int, new_sha: str, head_branch: str, source_wt: Path) -> None:
        git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
        rem = subprocess.run(
            [git_bin, "remote", "get-url", "origin"],
            cwd=source_wt,
            env=_clean_git_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        if rem.returncode == 0 and rem.stdout.strip():
            push_env = _clean_git_env()
            push_env["PAF_PRE_PUSH_VERIFIED"] = new_sha
            subprocess.run(
                [git_bin, "push", "origin", f"HEAD:refs/heads/{head_branch}"],
                cwd=source_wt,
                env=push_env,
                capture_output=True,
                text=True,
                check=False,
            )
            subprocess.run(
                [git_bin, "fetch", "origin", head_branch],
                cwd=self.repo_path,
                env=_clean_git_env(),
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            subprocess.run(
                [git_bin, "branch", "-f", head_branch, new_sha],
                cwd=self.repo_path,
                env=_clean_git_env(),
                capture_output=True,
                text=True,
                check=False,
            )
        self.prs[int(pr_number)]["headRefOid"] = new_sha
        self._bump_etag()

    def merge_pr(
        self,
        pr_number: int,
        *,
        require_hmac: bool = False,
        allow_same_family_fallback: bool = False,
        key_file: Path | None = None,
        approved_main: bool = False,
        max_wait_ci: int = 600,
        no_auto_revert: bool = False,
        host_id: str | None = None,
        on_merged_callback: Callable[[], None] | None = None,
    ) -> tuple[bool, str]:
        _ = (max_wait_ci, no_auto_revert)
        pr = self.prs[int(pr_number)]
        tmp_fix = self.repo_path / ".git" / f"inmem-pr-{pr_number}.json"
        tmp_fix.write_text(json.dumps(pr), encoding="utf-8")
        env = _clean_git_env()
        eff_key = key_file if key_file is not None else self.key_file
        if eff_key is not None:
            env["PAF_CONSENSUS_KEY_FILE"] = str(eff_key)
        cmd = [
            sys.executable,
            str(self._paf_bin),
            "merge-consensus",
            "--repo",
            str(self.repo_path),
            "--pr-fixture",
            str(tmp_fix),
            "--dry-run",
        ]
        if require_hmac:
            cmd.append("--require-hmac")
        if allow_same_family_fallback:
            cmd.append("--allow-same-family-fallback")
        if approved_main:
            cmd.append("--approved-main")
        proc = subprocess.run(
            cmd,
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
            if on_merged_callback is not None:
                on_merged_callback()
            if self.run_post_merge_verify:
                paf_mod = _load_paf_module()
                head_oid = str(pr.get("headRefOid") or "")
                wt_post, created_post = _prepare_detached_worktree(
                    self.repo_path,
                    head_oid,
                    str(pr.get("headRefName") or ""),
                )
                try:
                    target_dir = wt_post if (created_post and wt_post is not None) else self.repo_path
                    ok, msg = paf_mod.run_post_merge_and_deploy_checks(
                        target_dir,
                        pr_url=str(pr.get("url") or f"PR#{pr_number}"),
                        base_branch=str(pr.get("baseRefName") or "main"),
                        merged_sha=head_oid,
                        host_id=host_id,
                    )
                    if not ok:
                        return False, f"post_merge_or_deploy_failed: {msg}"
                finally:
                    _remove_worktree(self.repo_path, wt_post, created_post)
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


MAX_LEDGER_ENTRIES = 500


def _save_json_state(path: Path, data: dict[str, Any], *, max_entries: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    if max_entries is not None and len(payload) > max_entries:
        # Keep most recently inserted/updated entries (ISO timestamp values sort chronologically)
        sorted_items = sorted(payload.items(), key=lambda kv: str(kv[1]))
        payload = dict(sorted_items[-max_entries:])
        data.clear()
        data.update(payload)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
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

    main_mod = sys.modules.get("__main__")
    main_file = getattr(main_mod, "__file__", None) or ""
    if str(main_file).endswith("/bin/paf"):
        return main_mod
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


FORBIDDEN_AUTO_EDIT_RE = re.compile(
    r"^(?:"
    r"(?:.+/)?\.agent-verify\.json"
    r"|\.github/workflows/.*"
    r"|(?:.+/)?\.claude/settings[^/]*\.json"
    r"|(?:.+/)?\.gemini/settings[^/]*\.json"
    r"|(?:framework/)?githooks/.*"
    r"|(?:framework/)?hooks/.*"
    r"|(?:.+/)?\.env(?:\..+)?"
    r"|.*\.(?:key|pem)"
    r")$"
)

DIALOGUE_REGISTRY_TTL_SEC = 2700


def _dialogue_lock_path(st_dir: Path, slug: str, pr_number: int) -> Path:
    lock_key = f"{slug.strip().lower()}#{int(pr_number)}"
    digest = hashlib.sha256(lock_key.encode("utf-8")).hexdigest()[:16]
    return st_dir / f".dialogue-{digest}.lock"


@contextlib.contextmanager
def _acquire_pr_dialogue_lock(st_dir: Path, slug: str, pr_number: int):
    st_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _dialogue_lock_path(st_dir, slug, pr_number)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise RuntimeError(
                f"Consensus dialogue lock is already held for {slug}#{int(pr_number)}."
            ) from exc
        yield lock_path
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(fd)
        except OSError:
            pass


def _is_local_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def is_pr_actively_driven(
    st_dir: Path,
    slug: str,
    pr_number: int,
    *,
    local_host: str | None = None,
    now: float | None = None,
) -> bool:
    """Return True when `run_consensus_dialogue` is actively driving `slug#pr_number`,
    interlocking `watch_repo_once` so background launchd ticks never race an active dialogue.
    Evicts stale registry entries left behind by crashed/expired processes.
    """
    st_resolved = st_dir.expanduser().resolve()
    lock_path = _dialogue_lock_path(st_resolved, slug, pr_number)
    if lock_path.exists():
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    reg_path = st_resolved / "active-pr-sessions.json"
    if not reg_path.is_file():
        return False
    reg = _load_json_state(reg_path)
    active = reg.get("active") if isinstance(reg.get("active"), dict) else {}
    reg_key = f"{slug}#{int(pr_number)}"
    entry = active.get(reg_key)
    if not isinstance(entry, dict):
        return False

    cur_now = time.time() if now is None else float(now)
    try:
        expires_epoch = float(entry.get("expires_epoch") or 0.0)
    except (TypeError, ValueError):
        expires_epoch = 0.0
    try:
        entry_pid = int(entry.get("pid") or 0)
    except (TypeError, ValueError):
        entry_pid = 0
    entry_host = str(entry.get("host") or "").strip().lower()
    eff_host = (local_host or resolve_host_id(None)).strip().lower()

    not_expired = expires_epoch > cur_now
    if entry_host and entry_host != eff_host:
        alive = not_expired
    else:
        alive = not_expired and (entry_pid > 0 and _is_local_pid_alive(entry_pid))

    if alive:
        return True

    active.pop(reg_key, None)
    reg["active"] = active
    _save_json_state(reg_path, reg)
    return False


def _replace_verify_section_in_pr_body(old_body: str, verify_md: str) -> str:
    clean_md = verify_md.strip()
    chunks = old_body.split("\n\n---\n\n") if old_body else []
    replaced = False
    out_chunks: list[str] = []
    for ch in chunks:
        if ch.lstrip().startswith("## Verification Artifact"):
            out_chunks.append(clean_md)
            replaced = True
        else:
            out_chunks.append(ch)
    if replaced:
        return "\n\n---\n\n".join(out_chunks)
    marker_idx = old_body.find("<!-- paf-consensus:")
    if marker_idx >= 0:
        prefix = old_body[:marker_idx].rstrip()
        suffix = old_body[marker_idx:].lstrip()
        parts = [p for p in (prefix, clean_md, suffix) if p]
        return "\n\n---\n\n".join(parts)
    if old_body.strip():
        return f"{old_body.rstrip()}\n\n---\n\n{clean_md}"
    return clean_md


def _list_dirty_paths(worktree: Path) -> list[str]:
    git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
    proc = subprocess.run(
        [git_bin, "status", "--porcelain", "-z", "-uall"],
        cwd=worktree,
        env=_clean_git_env(),
        capture_output=True,
        check=False,
    )
    raw = proc.stdout or b""
    entries = raw.split(b"\x00")
    paths: list[str] = []
    idx = 0
    while idx < len(entries):
        item = entries[idx]
        idx += 1
        if len(item) < 4:
            continue
        status_xy = item[:2].decode("utf-8", errors="replace")
        rel_path = item[3:].decode("utf-8", errors="replace")
        if rel_path:
            paths.append(rel_path)
        if "R" in status_xy or "C" in status_xy:
            if idx < len(entries) and entries[idx]:
                orig_path = entries[idx].decode("utf-8", errors="replace")
                if orig_path:
                    paths.append(orig_path)
                idx += 1
    deduped = sorted(set(paths))
    for p in deduped:
        norm = re.sub(r"^(?:\./)+", "", p)
        if FORBIDDEN_AUTO_EDIT_RE.search(norm):
            raise RuntimeError(
                f"Automated consensus fix session attempted to modify protected/secret path '{norm}'; refusing."
            )
    return deduped


def _commit_dirty_paths(worktree: Path, paths: list[str], commit_message: str) -> str:
    git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
    env = _clean_git_env()
    add_proc = subprocess.run(
        [git_bin, "add", "--", *paths],
        cwd=worktree,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if add_proc.returncode != 0:
        raise RuntimeError(f"git add failed in author worktree: {add_proc.stderr.strip()}")
    commit_proc = subprocess.run(
        [git_bin, "commit", "-m", commit_message],
        cwd=worktree,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if commit_proc.returncode != 0:
        raise RuntimeError(f"git commit failed in author worktree: {commit_proc.stderr.strip()}")
    sha_proc = subprocess.run(
        [git_bin, "rev-parse", "HEAD"],
        cwd=worktree,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return sha_proc.stdout.strip()


def run_consensus_dialogue(
    *,
    repo: Path,
    pr_number: int,
    repo_slug: str | None = None,
    author_family: str | None = None,
    caller_session_id: str | None = None,
    host_id: str | None = None,
    state_dir: Path | None = None,
    key_file: Path | None = None,
    bus: Any | None = None,
    claude_bin: str | None = None,
    agy_bin: str | None = None,
    claude_model: str | None = None,
    agy_model: str | None = None,
    allow_same_family_fallback: bool | None = None,
    max_rounds: int | None = None,
    review_timeout_sec: int = 660,
    author_timeout_sec: int = 1200,
    approved_main: bool = False,
    max_wait_ci: int = 600,
    no_auto_revert: bool = False,
    close_hook: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Synchronous persistent two-session PR consensus loop:
    Open/inspect PR -> keep one persistent Reviewer session and one persistent Author session
    across back-and-forth rounds -> merge immediately on consensus + green CI -> close both
    sessions -> run post-merge check and optional deploy + post-deploy check.
    """
    repo = repo.expanduser().resolve()
    st_dir = (state_dir or DEFAULT_STATE_DIR).expanduser().resolve()
    st_dir.mkdir(parents=True, exist_ok=True)
    paf_mod = _load_paf_module()
    paf_bin = Path(paf_mod.__file__).resolve()
    resolved_host = resolve_host_id(host_id)
    resolved_key = resolve_key_file(key_file)
    secret = load_consensus_secret(resolved_key)
    if repo_slug:
        slug = repo_slug
    else:
        try:
            slug = infer_repo_slug(repo)
        except RuntimeError:
            slug = f"local/{repo.name}"
    safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", slug)

    verify_cfg: dict[str, Any] = {}
    cfg_file = repo / ".agent-verify.json"
    if cfg_file.is_file():
        verify_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    consensus_cfg = verify_cfg.get("consensus") or {}
    require_hmac = (
        bool(consensus_cfg["require_hmac"])
        if "require_hmac" in consensus_cfg
        else True
    )
    if require_hmac and not secret:
        raise ConsensusKeyError(
            f"HMAC consensus key required for {slug}#{pr_number}, but {resolved_key} is missing. "
            "Run `paf init-consensus-key` first."
        )
    allow_fb = (
        allow_same_family_fallback
        if allow_same_family_fallback is not None
        else bool(consensus_cfg.get("allow_same_family_fallback", False))
    )
    eff_max_rounds = (
        max(1, int(max_rounds))
        if max_rounds is not None
        else max(1, int(consensus_cfg.get("max_rounds", 3)))
    )

    eff_claude_bin = (
        claude_bin
        or os.environ.get("PAF_CLAUDE_BIN")
        or ("/Users/linhungfan/bin/claude" if os.path.isfile("/Users/linhungfan/bin/claude") else "claude")
    )
    eff_agy_bin = (
        agy_bin
        or os.environ.get("PAF_AGY_BIN")
        or ("/Users/linhungfan/bin/agy" if os.path.isfile("/Users/linhungfan/bin/agy") else "agy")
    )
    active_bus = bus if bus is not None else GitHubBus(repo, paf_bin, key_file=resolved_key)

    with _acquire_pr_dialogue_lock(st_dir, slug, pr_number):
        pr_data = active_bus.get_pr(pr_number)
        if str(pr_data.get("state") or "").upper() != "OPEN":
            raise RuntimeError(f"PR #{pr_number} is not OPEN (state={pr_data.get('state')!r}).")

        markers = paf_mod._extract_consensus_markers(pr_data)
        pr_base = str(pr_data.get("baseRefName") or "main").strip()
        inferred_af, _seen_af, author_host, author_sessions, _bad_sig, _mm = paf_mod._extract_author_metadata(
            markers,
            hmac_secret=secret,
            require_hmac=require_hmac,
            expected_base=pr_base,
            expected_pr_num=int(pr_number),
        )
        if author_host and author_host != resolved_host:
            raise RuntimeError(
                f"PR #{pr_number} is owned by host '{author_host}' (current host='{resolved_host}')."
            )
        eff_author_fam = normalize_family(author_family) or inferred_af
        if eff_author_fam not in ("claude", "gemini"):
            raise RuntimeError(
                f"Cannot determine author_family ('claude' or 'gemini') for PR #{pr_number}; pass --author-family."
            )
        primary_reviewer_fam = "claude" if eff_author_fam == "gemini" else "gemini"

        sessions_root = st_dir / "sessions"
        reviewer_wt = sessions_root / f"{safe_slug}-pr-{pr_number}-reviewer"
        author_wt = sessions_root / f"{safe_slug}-pr-{pr_number}-author"
        registry_path = st_dir / "active-pr-sessions.json"
        registry_key = f"{slug}#{pr_number}"

        pre_author_sid = (
            str(caller_session_id or "").strip() or (sorted(author_sessions)[0] if author_sessions else "")
        )
        session_ctx: dict[str, Any] = {
            "repo": repo,
            "pr_number": int(pr_number),
            "author_family": eff_author_fam,
            "reviewer_family": primary_reviewer_fam,
            "author_session_id": pre_author_sid,
            "reviewer_session_id": "",
            "spawned_claude_sessions": set(),
            "spawned_agy_sessions": set(),
            "worktrees": [reviewer_wt, author_wt],
            "processes": [],
            "registry_path": registry_path,
            "registry_key": registry_key,
            "close_hook": close_hook,
            "closed": False,
        }

        def _sync_active_registry() -> None:
            reg = _load_json_state(registry_path)
            active = reg.get("active") if isinstance(reg.get("active"), dict) else {}
            now_epoch = time.time()
            active[registry_key] = {
                "pr_number": int(pr_number),
                "pid": os.getpid(),
                "host": resolved_host,
                "author_family": session_ctx["author_family"],
                "reviewer_family": session_ctx["reviewer_family"],
                "author_session_id": session_ctx["author_session_id"],
                "reviewer_session_id": session_ctx["reviewer_session_id"],
                "updated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expires_epoch": round(now_epoch + DIALOGUE_REGISTRY_TTL_SEC, 3),
            }
            reg["active"] = active
            _save_json_state(registry_path, reg)

        _sync_active_registry()

        active_reviewer_fam = primary_reviewer_fam
        active_reviewer_is_fb = False
        reviewer_session_started = False
        author_session_started = False
        prev_reviewed_sha = ""
        last_author_reply = ""
        no_edit_rebuttal_used = False
        rounds_run = 0

        try:
            for round_idx in range(1, eff_max_rounds + 1):
                _sync_active_registry()
                pr_data = active_bus.get_pr(pr_number)
                if str(pr_data.get("state") or "").upper() != "OPEN":
                    return {
                        "status": "closed_externally",
                        "pr_number": int(pr_number),
                        "rounds": rounds_run,
                        "sessions_closed": bool(close_consensus_sessions(session_ctx).get("closed")),
                    }

                head_sha = str(pr_data.get("headRefOid") or "").strip()
                head_branch = str(pr_data.get("headRefName") or "").strip()
                base_branch = str(pr_data.get("baseRefName") or "main").strip()

                eval_now = paf_mod.evaluate_pr_consensus(
                    pr_data,
                    author_family_override=eff_author_fam,
                    max_rounds=eff_max_rounds,
                    hmac_secret=secret,
                    require_hmac=require_hmac,
                    allow_same_family_fallback=allow_fb,
                )
                if eval_now.get("status") == "approved":
                    break
                if eval_now.get("status") == "paused_human_tiebreak":
                    close_rec = close_consensus_sessions(session_ctx)
                    return {
                        "status": "paused_human_tiebreak",
                        "reason": str(eval_now.get("reason") or ""),
                        "pr_number": int(pr_number),
                        "head_sha": head_sha,
                        "rounds": rounds_run,
                        "author_session_id": session_ctx["author_session_id"],
                        "reviewer_session_id": session_ctx["reviewer_session_id"],
                        "sessions_closed": bool(close_rec.get("closed")),
                    }

                rounds_run = round_idx
                if not _ensure_pr_worktree(repo, reviewer_wt, head_sha, head_branch):
                    raise RuntimeError(f"Failed preparing reviewer worktree for PR #{pr_number} at {head_sha}.")
                diff_stat, diff_patch = _collect_diff_context(reviewer_wt, base_branch, head_sha)
                if not diff_patch.strip():
                    raise RuntimeError(f"Empty diff against '{base_branch}' for PR #{pr_number} at {head_sha}.")

                if reviewer_session_started:
                    delta_patch = _collect_delta_diff(reviewer_wt, prev_reviewed_sha, head_sha)
                    rev_prompt = build_followup_review_prompt(
                        repo_slug=slug,
                        pr_data=pr_data,
                        prev_sha=prev_reviewed_sha,
                        head_sha=head_sha,
                        diff_stat=diff_stat,
                        delta_patch=delta_patch,
                        author_reply=last_author_reply,
                        round_num=round_idx,
                    )
                else:
                    rev_prompt = build_review_prompt(
                        repo_slug=slug,
                        pr_data=pr_data,
                        diff_stat=diff_stat,
                        diff_patch=diff_patch,
                        round_num=round_idx,
                    )

                def _invoke_reviewer(fam: str, resume_sid: str | None) -> dict[str, Any]:
                    with _shield_key_file(resolved_key):
                        if fam == "gemini":
                            try:
                                res = run_agy_review(
                                    agy_bin=eff_agy_bin,
                                    worktree=reviewer_wt,
                                    prompt=rev_prompt,
                                    model=agy_model,
                                    resume_session_id=resume_sid,
                                    timeout_sec=review_timeout_sec,
                                )
                            except BaseException as exc:
                                sp_sid = str(getattr(exc, "spawned_session_id", "") or "").strip()
                                if sp_sid:
                                    session_ctx["spawned_agy_sessions"].add(sp_sid)
                                raise
                            if res.get("session_created") and res.get("session_id"):
                                session_ctx["spawned_agy_sessions"].add(res["session_id"])
                            return res
                        init_sid = None if resume_sid else str(uuid.uuid4())
                        if init_sid:
                            session_ctx["spawned_claude_sessions"].add(init_sid)
                        res = run_claude_review(
                            claude_bin=eff_claude_bin,
                            worktree=reviewer_wt,
                            prompt=rev_prompt,
                            model=claude_model,
                            key_file=resolved_key,
                            init_session_id=init_sid,
                            resume_session_id=resume_sid,
                            timeout_sec=review_timeout_sec,
                        )
                        if res.get("session_created") and res.get("session_id"):
                            session_ctx["spawned_claude_sessions"].add(res["session_id"])
                        return res

                try:
                    rev_res = _invoke_reviewer(
                        active_reviewer_fam,
                        session_ctx["reviewer_session_id"] if reviewer_session_started else None,
                    )
                except Exception as exc:
                    known_author_sids = set(author_sessions)
                    if session_ctx["author_session_id"]:
                        known_author_sids.add(session_ctx["author_session_id"])
                    if (
                        not active_reviewer_is_fb
                        and allow_fb
                        and known_author_sids
                        and is_quota_or_outage_error(exc)
                    ):
                        sys.stderr.write(
                            f"[paf consensus] WARNING on {slug}#{pr_number}: heterogeneous reviewer "
                            f"'{active_reviewer_fam}' hit API quota/outage ({exc}); switching to persistent "
                            f"fresh '{eff_author_fam}' fallback reviewer session.\n"
                        )
                        active_reviewer_fam = eff_author_fam
                        active_reviewer_is_fb = True
                        session_ctx["reviewer_family"] = active_reviewer_fam
                        reviewer_session_started = False
                        session_ctx["reviewer_session_id"] = ""
                        rev_res = _invoke_reviewer(active_reviewer_fam, None)
                        if not rev_res.get("session_id") or rev_res["session_id"] in known_author_sids:
                            raise RuntimeError(
                                "Same-family outage fallback reviewer returned missing or colliding session_id."
                            ) from exc
                    else:
                        raise

                reported_rev_sid = str(rev_res.get("session_id") or "").strip()
                if not reported_rev_sid:
                    raise RuntimeError(
                        f"Reviewer CLI ({active_reviewer_fam}) returned an empty session_id on {slug}#{pr_number}; "
                        "refusing non-persistent consensus review."
                    )
                if not reviewer_session_started:
                    session_ctx["reviewer_session_id"] = reported_rev_sid
                    reviewer_session_started = True
                    _sync_active_registry()

                if contains_secret_material(rev_res["prose"], secret):
                    raise RuntimeError(
                        f"SECURITY BLOCK on {slug}#{pr_number}: reviewer prose contained secret/credential material."
                    )

                latest_pr = active_bus.get_pr(pr_number)
                if not sha_matches(str(latest_pr.get("headRefOid") or ""), head_sha):
                    raise RuntimeError(
                        f"PR #{pr_number} headRefOid moved concurrently during review ({head_sha[:12]} -> {str(latest_pr.get('headRefOid'))[:12]})."
                    )

                rev_marker: dict[str, Any] = {
                    "v": 1,
                    "role": "reviewer",
                    "model_family": active_reviewer_fam,
                    "verdict": rev_res["verdict"],
                    "head_sha": head_sha.lower(),
                    "round": round_idx,
                    "blocking_count": rev_res["blocking_count"],
                    "needs_human_decision": rev_res["needs_human_decision"],
                    "session_id": session_ctx["reviewer_session_id"],
                    "host": resolved_host,
                    "base_branch": base_branch,
                    "pr_number": int(pr_number),
                }
                if active_reviewer_is_fb:
                    rev_marker["fallback_same_family"] = True
                sign_marker_in_place(rev_marker, secret, key_file=resolved_key, require_hmac=require_hmac)
                fb_banner = (
                    "> **[Outage Fallback Review]**: Primary heterogeneous reviewer encountered an API quota/outage error; "
                    "reviewed by an independent fresh blank session.\n\n"
                    if active_reviewer_is_fb
                    else ""
                )
                rev_comment = (
                    fb_banner
                    + rev_res["prose"].strip()
                    + "\n\n"
                    + f"<!-- paf-consensus: {json.dumps(rev_marker, separators=(',', ':'))} -->"
                )
                active_bus.post_comment(pr_number, rev_comment)
                prev_reviewed_sha = head_sha

                if (
                    rev_res["verdict"] == "approve"
                    and rev_res["blocking_count"] == 0
                    and not rev_res["needs_human_decision"]
                ):
                    break

                if rev_res["needs_human_decision"] or round_idx >= eff_max_rounds:
                    close_rec = close_consensus_sessions(session_ctx)
                    reason = (
                        "Reviewer flagged needs_human_decision=true."
                        if rev_res["needs_human_decision"]
                        else f"Reached max_rounds ({eff_max_rounds}) without approve consensus."
                    )
                    return {
                        "status": "paused_human_tiebreak",
                        "reason": reason,
                        "pr_number": int(pr_number),
                        "head_sha": head_sha,
                        "rounds": rounds_run,
                        "author_session_id": session_ctx["author_session_id"],
                        "reviewer_session_id": session_ctx["reviewer_session_id"],
                        "sessions_closed": bool(close_rec.get("closed")),
                    }

                # Author Fix Turn (same persistent author_session_id across all fix rounds)
                if not _ensure_pr_worktree(repo, author_wt, head_sha, head_branch):
                    raise RuntimeError(f"Failed preparing author worktree for PR #{pr_number} at {head_sha}.")

                fix_prompt = build_author_fix_prompt(
                    repo_slug=slug,
                    pr_data=pr_data,
                    diff_stat=diff_stat,
                    reviewer_prose=rev_res["prose"],
                    round_num=round_idx,
                    is_resume=author_session_started,
                )

                def _invoke_author(prompt_text: str) -> dict[str, Any]:
                    nonlocal author_session_started
                    with _shield_key_file(resolved_key):
                        if eff_author_fam == "gemini":
                            try:
                                res = run_agy_author_turn(
                                    agy_bin=eff_agy_bin,
                                    worktree=author_wt,
                                    prompt=prompt_text,
                                    model=agy_model,
                                    resume_session_id=(session_ctx["author_session_id"] if author_session_started else None),
                                    timeout_sec=author_timeout_sec,
                                )
                            except BaseException as exc:
                                sp_sid = str(getattr(exc, "spawned_session_id", "") or "").strip()
                                if sp_sid:
                                    session_ctx["spawned_agy_sessions"].add(sp_sid)
                                raise
                            reported_auth_sid = str(res.get("session_id") or "").strip()
                            if not reported_auth_sid:
                                raise RuntimeError(
                                    f"Author CLI (gemini) returned an empty session_id on {slug}#{pr_number}; "
                                    "refusing non-persistent author session."
                                )
                            if res.get("session_created"):
                                session_ctx["spawned_agy_sessions"].add(reported_auth_sid)
                            if not author_session_started:
                                session_ctx["author_session_id"] = reported_auth_sid
                                author_session_started = True
                                _sync_active_registry()
                            return res
                        init_sid = None if author_session_started else str(uuid.uuid4())
                        if init_sid:
                            session_ctx["author_session_id"] = init_sid
                            session_ctx["spawned_claude_sessions"].add(init_sid)
                        res = run_claude_author_turn(
                            claude_bin=eff_claude_bin,
                            worktree=author_wt,
                            prompt=prompt_text,
                            model=claude_model,
                            key_file=resolved_key,
                            init_session_id=init_sid,
                            resume_session_id=(session_ctx["author_session_id"] if author_session_started else None),
                            timeout_sec=author_timeout_sec,
                        )
                        if res.get("session_created") and res.get("session_id"):
                            session_ctx["spawned_claude_sessions"].add(res["session_id"])
                        if not author_session_started:
                            if res.get("session_id"):
                                session_ctx["author_session_id"] = res["session_id"]
                            author_session_started = True
                            _sync_active_registry()
                        return res

                auth_res = _invoke_author(fix_prompt)
                if auth_res["needs_human_decision"]:
                    close_rec = close_consensus_sessions(session_ctx)
                    return {
                        "status": "paused_human_tiebreak",
                        "reason": "Author session flagged needs_human_decision=true.",
                        "pr_number": int(pr_number),
                        "head_sha": head_sha,
                        "rounds": rounds_run,
                        "author_session_id": session_ctx["author_session_id"],
                        "reviewer_session_id": session_ctx["reviewer_session_id"],
                        "sessions_closed": bool(close_rec.get("closed")),
                    }

                dirty_paths = _list_dirty_paths(author_wt)
                new_sha = head_sha
                if dirty_paths:
                    verify_ok, verify_rep, _, _ = paf_mod.execute_verify(author_wt, mode="pre-push")
                    if not verify_ok:
                        fail_tail = "\n\n".join(
                            f"[{s['stage']}] {s['command']}:\n{s['output_tail']}"
                            for s in (verify_rep.get("steps") or [])
                            if not s.get("passed")
                        )
                        retry_prompt = build_author_fix_prompt(
                            repo_slug=slug,
                            pr_data=pr_data,
                            diff_stat=diff_stat,
                            reviewer_prose=rev_res["prose"],
                            round_num=round_idx,
                            is_resume=True,
                            verify_failure=fail_tail,
                        )
                        auth_res = _invoke_author(retry_prompt)
                        dirty_paths = _list_dirty_paths(author_wt)
                        verify_ok, verify_rep, _, _ = paf_mod.execute_verify(author_wt, mode="pre-push")
                        if not verify_ok:
                            raise RuntimeError(
                                f"Author fix failed pre-push verification on PR #{pr_number} after retry."
                            )
                    if contains_secret_material(auth_res["commit_message"], secret) or contains_secret_material(
                        auth_res["reply_prose"], secret
                    ):
                        raise RuntimeError(
                            f"SECURITY BLOCK on {slug}#{pr_number}: author output contained secret/credential material."
                        )
                    if dirty_paths:
                        new_sha = _commit_dirty_paths(author_wt, dirty_paths, auth_res["commit_message"])
                        refreshed_rep = dict(verify_rep)
                        refreshed_rep["head_sha"] = new_sha
                        git_common = paf_mod._git_common_dir(author_wt)
                        git_common.mkdir(parents=True, exist_ok=True)
                        refreshed_json = git_common / f"paf-verify-{new_sha[:12]}-pre-push.json"
                        refreshed_md_path = git_common / f"paf-verify-{new_sha[:12]}-pre-push.md"
                        refreshed_md = paf_mod._render_verify_markdown(refreshed_rep, review=None)
                        refreshed_json.write_text(json.dumps(refreshed_rep, indent=2), encoding="utf-8")
                        refreshed_md_path.write_text(refreshed_md, encoding="utf-8")
                        active_bus.push_head(pr_number, new_sha, head_branch, author_wt)
                        if hasattr(active_bus, "update_pr_body"):
                            updated_body = _replace_verify_section_in_pr_body(
                                str(pr_data.get("body") or ""), refreshed_md
                            )
                            active_bus.update_pr_body(pr_number, updated_body)
                else:
                    if no_edit_rebuttal_used:
                        close_rec = close_consensus_sessions(session_ctx)
                        return {
                            "status": "paused_human_tiebreak",
                            "reason": "Reviewer and Author deadlocked without further code changes.",
                            "pr_number": int(pr_number),
                            "head_sha": head_sha,
                            "rounds": rounds_run,
                            "author_session_id": session_ctx["author_session_id"],
                            "reviewer_session_id": session_ctx["reviewer_session_id"],
                            "sessions_closed": bool(close_rec.get("closed")),
                        }
                    if contains_secret_material(auth_res["reply_prose"], secret):
                        raise RuntimeError(
                            f"SECURITY BLOCK on {slug}#{pr_number}: author reply contained secret/credential material."
                        )
                    no_edit_rebuttal_used = True

                author_marker: dict[str, Any] = {
                    "v": 1,
                    "role": "author",
                    "model_family": eff_author_fam,
                    "verdict": "open",
                    "head_sha": new_sha.lower(),
                    "round": round_idx + 1,
                    "blocking_count": 0,
                    "needs_human_decision": False,
                    "session_id": session_ctx["author_session_id"],
                    "host": resolved_host,
                    "base_branch": base_branch,
                    "pr_number": int(pr_number),
                }
                sign_marker_in_place(author_marker, secret, key_file=resolved_key, require_hmac=require_hmac)
                author_sessions.add(session_ctx["author_session_id"])
                author_comment = (
                    auth_res["reply_prose"].strip()
                    + "\n\n"
                    + f"<!-- paf-consensus: {json.dumps(author_marker, separators=(',', ':'))} -->"
                )
                active_bus.post_comment(pr_number, author_comment)
                last_author_reply = auth_res["reply_prose"]

            # Step 3 -> Step 4 -> Step 5: Merge on consensus, close both sessions right after merge,
            # then run post-merge check and (if deploy configured) deploy + post-deploy check.
            merged_ok, merge_reason = active_bus.merge_pr(
                pr_number,
                require_hmac=require_hmac,
                allow_same_family_fallback=allow_fb,
                key_file=resolved_key,
                approved_main=approved_main,
                max_wait_ci=max_wait_ci,
                no_auto_revert=no_auto_revert,
                host_id=resolved_host,
                on_merged_callback=lambda: close_consensus_sessions(session_ctx),
            )
            close_rec = close_consensus_sessions(session_ctx)
            final_pr = active_bus.get_pr(pr_number)
            final_sha = str(final_pr.get("headRefOid") or "")
            if merged_ok:
                return {
                    "status": "merged",
                    "pr_number": int(pr_number),
                    "head_sha": final_sha,
                    "rounds": rounds_run,
                    "author_session_id": session_ctx["author_session_id"],
                    "reviewer_session_id": session_ctx["reviewer_session_id"],
                    "sessions_closed": bool(close_rec.get("closed")),
                }
            if merge_reason == "paused":
                return {
                    "status": "paused_main_approval",
                    "reason": merge_reason,
                    "pr_number": int(pr_number),
                    "head_sha": final_sha,
                    "rounds": rounds_run,
                    "author_session_id": session_ctx["author_session_id"],
                    "reviewer_session_id": session_ctx["reviewer_session_id"],
                    "sessions_closed": bool(close_rec.get("closed")),
                }
            raise RuntimeError(
                f"Consensus approved on PR #{pr_number} ({final_sha[:12]}), but merge failed: {merge_reason}"
            )
        finally:
            close_consensus_sessions(session_ctx)


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
    backoff_sec: int = 180,
    author_fix_hook: Callable[[Path, dict[str, Any], dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Run one ETag-aware consensus watch tick on `repo`.

    Note: `author_fix_hook` is an optional Python callback for daemon integrations;
    the standalone `paf watch-prs` CLI executes sandboxed reviews, claim locks, and
    `author_host` `merge-consensus` while leaving code-fix commits to authoring sessions.
    """
    repo = repo.expanduser().resolve()
    slug = repo_slug or infer_repo_slug(repo)
    resolved_host = resolve_host_id(host_id)
    resolved_state = (state_dir or DEFAULT_STATE_DIR).expanduser().resolve()
    resolved_state.mkdir(parents=True, exist_ok=True)

    # Non-blocking per-repo watcher lock prevents manual + launchd ticks from racing
    repo_lock_hash = hashlib.sha256(f"{slug}:{repo}".encode("utf-8")).hexdigest()[:12]
    repo_lock_path = resolved_state / f".watch-{repo_lock_hash}.lock"
    repo_lock_fd = os.open(str(repo_lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(repo_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "repo": slug,
                "not_modified": True,
                "locked": True,
                "reviewed": [],
                "merged": [],
                "fixed": [],
                "paused": [],
            }

        _prune_stale_worktrees(repo)
        resolved_key = resolve_key_file(key_file)
        secret = load_consensus_secret(resolved_key)
        enabled_families = tuple(dict.fromkeys(normalize_family(f) for f in families if normalize_family(f)))

        paf_mod = _load_paf_module()
        cfg = paf_mod._load_verify_config(repo)
        consensus_cfg = cfg.get("consensus") or {}
        # Unattended `watch-prs` defaults `require_hmac` to True unless explicitly disabled in `.agent-verify.json`
        require_hmac = bool(consensus_cfg.get("require_hmac", True))
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

        has_backoff_for_slug = any(
            k.startswith(f"{slug}#") and ":backoff:" in k for k in ledger
        )
        prev_etag = None if has_backoff_for_slug else etags.get(slug)
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
        has_pending_work = False
        ledger_dirty = False

        for pr_num in open_pr_nums:
            if is_pr_actively_driven(resolved_state, slug, pr_num, local_host=resolved_host):
                has_pending_work = True
                continue
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
            prior_reviewed_shas = {
                str(m.get("head_sha") or "").strip().lower()[:12]
                for m in markers
                if str(m.get("role") or "").strip().lower() != "author"
                and str(m.get("verdict") or "").strip().lower() in ("approve", "request_changes")
                and HEX_SHA_RE.match(str(m.get("head_sha") or "").strip().lower())
                and not sha_matches(m.get("head_sha"), head_sha)
            }
            next_round = max(int(verdict.get("max_round") or 0) + 1, len(prior_reviewed_shas) + 1)

            # Case 1: Consensus already approved -> ONLY author_host merges (single-writer rule)
            if status == "approved":
                owns_pr = (author_host == resolved_host) if author_host else (author_family in enabled_families)
                if owns_pr:
                    ok, m_reason = active_bus.merge_pr(
                        pr_num,
                        require_hmac=require_hmac,
                        allow_same_family_fallback=allow_fb,
                        key_file=resolved_key,
                    )
                    if ok:
                        merged.append(pr_num)
                        did_mutate = True
                    elif m_reason == "paused":
                        paused.append(pr_num)
                    else:
                        # Check runs still pending or transient merge block -> do not cache ETag!
                        has_pending_work = True
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
                    _save_json_state(ledger_path, ledger, max_entries=MAX_LEDGER_ENTRIES)
                    if author_fix_hook(repo, pr_data, verdict):
                        fixed.append(pr_num)
                        did_mutate = True
                continue

            if status == "pending_review" and require_hmac and not secret:
                has_pending_work = True
                sys.stderr.write(
                    f"[paf watch-prs] skipping {slug}#{pr_num}: consensus.key missing at {resolved_key} (run `paf init-consensus-key`).\n"
                )
                continue

            if status == "pending_review" and not author_family:
                sys.stderr.write(
                    f"[paf watch-prs] skipping {slug}#{pr_num}: {verdict.get('reason')}\n"
                )
                continue

            # Case 4: Pending review -> heterogeneous family reviews (or fresh-session fallback on CLI outage)
            if status == "pending_review" and author_family:
                author_sessions = set(verdict.get("author_session_ids") or [])
                hetero_candidates = [f for f in enabled_families if f != author_family]
                review_plan: list[tuple[str, bool]] = [(f, False) for f in hetero_candidates]
                if allow_fb and author_family in enabled_families and author_sessions:
                    review_plan.append((author_family, True))
                if not review_plan:
                    continue

                if any(
                    ledger.get(f"{slug}#{pr_num}:{head_sha}:rev:{cand_f}:{resolved_host}")
                    for cand_f, _ in review_plan
                ):
                    continue

                primary_fam, primary_is_fb = review_plan[0]
                backoff_key = f"{slug}#{pr_num}:{head_sha}:backoff:{resolved_host}"
                now_utc = dt.datetime.now(dt.timezone.utc)
                backoff_until = _parse_iso_utc(str(ledger.get(backoff_key) or ""))
                if backoff_until is not None:
                    if backoff_until > now_utc:
                        has_pending_work = True
                        continue
                    ledger.pop(backoff_key, None)
                    ledger_dirty = True

                # Check existing unexpired claim on GitHub before preparing worktree
                holder = active_claim_holder(pr_data.get("comments") or [], head_sha, primary_fam)
                if holder and holder != resolved_host:
                    has_pending_work = True
                    continue

                # Fail closed BEFORE claiming if detached worktree or diff context cannot be built
                wt, created_wt = _prepare_detached_worktree(
                    repo, head_sha, str(pr_data.get("headRefName") or "")
                )
                if not created_wt or wt is None:
                    has_pending_work = True
                    ledger[backoff_key] = (
                        dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=backoff_sec)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    _save_json_state(ledger_path, ledger, max_entries=MAX_LEDGER_ENTRIES)
                    sys.stderr.write(
                        f"[paf watch-prs] aborted review on {slug}#{pr_num}: failed creating detached worktree for {head_sha}.\n"
                    )
                    continue

                review_result: dict[str, Any] | None = None
                chosen_fam = primary_fam
                chosen_fb = primary_is_fb
                try:
                    diff_stat, diff_patch = _collect_diff_context(
                        wt, str(pr_data.get("baseRefName") or "main"), head_sha
                    )
                    if not diff_patch.strip():
                        has_pending_work = True
                        ledger[backoff_key] = (
                            dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=backoff_sec)
                        ).strftime("%Y-%m-%dT%H:%M:%SZ")
                        _save_json_state(ledger_path, ledger, max_entries=MAX_LEDGER_ENTRIES)
                        sys.stderr.write(
                            f"[paf watch-prs] aborted review on {slug}#{pr_num}: empty diff patch for {head_sha}.\n"
                        )
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
                            has_pending_work = True
                            continue
                        winner = active_claim_holder(refreshed_pr.get("comments") or [], head_sha, primary_fam)
                        if winner and winner != resolved_host:
                            has_pending_work = True
                            continue
                        pr_data = refreshed_pr

                    prompt = build_review_prompt(
                        repo_slug=slug,
                        pr_data=pr_data,
                        diff_stat=diff_stat,
                        diff_patch=diff_patch,
                        round_num=max(1, next_round),
                    )
                    last_err: Exception | None = None
                    claimed_fams = {primary_fam}
                    with _shield_key_file(resolved_key):
                        for cand_fam, is_fb in review_plan:
                            if is_fb and last_err is not None:
                                sys.stderr.write(
                                    f"[paf watch-prs] WARNING on {slug}#{pr_num}: heterogeneous reviewer '{primary_fam}' "
                                    f"hit API quota/outage ({last_err}); invoking fresh blank '{cand_fam}' fallback session.\n"
                                )
                            if is_fb and cand_fam not in claimed_fams:
                                fb_holder = active_claim_holder(
                                    pr_data.get("comments") or [], head_sha, cand_fam
                                )
                                if fb_holder and fb_holder != resolved_host:
                                    has_pending_work = True
                                    continue
                                if not fb_holder:
                                    fb_exp = (
                                        dt.datetime.now(dt.timezone.utc)
                                        + dt.timedelta(seconds=claim_ttl_sec)
                                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                                    fb_claim = (
                                        f"Claiming `{cand_fam}` outage-fallback review on `{head_sha[:12]}` (host `{resolved_host}`).\n\n"
                                        + format_claim_marker(
                                            head_sha=head_sha,
                                            model_family=cand_fam,
                                            host=resolved_host,
                                            expires_at=fb_exp,
                                        )
                                    )
                                    active_bus.post_comment(pr_num, fb_claim)
                                    did_mutate = True
                                    if claim_settle_sec > 0:
                                        time.sleep(claim_settle_sec)
                                    refreshed_fb = active_bus.get_pr(pr_num)
                                    if str(refreshed_fb.get("headRefOid") or "").strip() != head_sha:
                                        has_pending_work = True
                                        break
                                    fb_winner = active_claim_holder(
                                        refreshed_fb.get("comments") or [], head_sha, cand_fam
                                    )
                                    if fb_winner and fb_winner != resolved_host:
                                        has_pending_work = True
                                        continue
                                    pr_data = refreshed_fb
                                claimed_fams.add(cand_fam)
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
                                        key_file=resolved_key,
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
                                if not is_fb and not is_quota_or_outage_error(exc):
                                    # Non-outage failure (e.g., bad CLI flag or invalid JSON): do not silently degrade heterogeneity
                                    break
                                continue
                    if review_result is None and last_err is not None:
                        has_pending_work = True
                        ledger[backoff_key] = (
                            dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=backoff_sec)
                        ).strftime("%Y-%m-%dT%H:%M:%SZ")
                        _save_json_state(ledger_path, ledger, max_entries=MAX_LEDGER_ENTRIES)
                        sys.stderr.write(f"[paf watch-prs] review runner failed on {slug}#{pr_num}: {last_err}\n")
                        continue
                finally:
                    _remove_worktree(repo, wt, created_wt)

                if review_result is None:
                    has_pending_work = True
                    continue

                if contains_secret_material(review_result["prose"], secret):
                    has_pending_work = True
                    ledger[backoff_key] = (
                        dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(600, backoff_sec))
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    _save_json_state(ledger_path, ledger, max_entries=MAX_LEDGER_ENTRIES)
                    sys.stderr.write(
                        f"[paf watch-prs] SECURITY BLOCK on {slug}#{pr_num}: reviewer prose contained secret or credential material; refusing to post.\n"
                    )
                    continue

                # Re-verify PR headRefOid did not move while headless model was thinking
                latest_pr = active_bus.get_pr(pr_num)
                if str(latest_pr.get("headRefOid") or "").strip() != head_sha:
                    has_pending_work = True
                    continue

                base_branch_name = str(latest_pr.get("baseRefName") or pr_data.get("baseRefName") or "main").strip()
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
                    "base_branch": base_branch_name,
                    "pr_number": int(pr_num),
                }
                if chosen_fb:
                    marker["fallback_same_family"] = True
                sign_marker_in_place(
                    marker,
                    secret,
                    key_file=resolved_key,
                    require_hmac=require_hmac,
                )

                fallback_banner = (
                    "> **[Outage Fallback Review]**: Primary heterogeneous reviewer encountered an API quota/outage error; "
                    "reviewed by an independent fresh blank session.\n\n"
                    if chosen_fb
                    else ""
                )
                comment_body = (
                    fallback_banner
                    + review_result["prose"].strip()
                    + "\n\n"
                    + f"<!-- paf-consensus: {json.dumps(marker, separators=(',', ':'))} -->"
                )
                active_bus.post_comment(pr_num, comment_body)
                reviewed.append(pr_num)
                did_mutate = True
                rev_ledger_key = f"{slug}#{pr_num}:{head_sha}:rev:{chosen_fam}:{resolved_host}"
                ledger[rev_ledger_key] = dt.datetime.now(dt.timezone.utc).isoformat()
                ledger.pop(backoff_key, None)
                _save_json_state(ledger_path, ledger, max_entries=MAX_LEDGER_ENTRIES)
                ledger_dirty = False

                # Immediate single-tick convergence: if this machine ALSO owns the PR (`author_host == resolved_host`)
                # and review approved, merge right away without waiting 45s for the next poll tick!
                owns_pr = (author_host == resolved_host) if author_host else False
                if (
                    owns_pr
                    and review_result["verdict"] == "approve"
                    and review_result["blocking_count"] == 0
                    and not review_result["needs_human_decision"]
                ):
                    ok, m_reason = active_bus.merge_pr(
                        pr_num,
                        require_hmac=require_hmac,
                        allow_same_family_fallback=allow_fb,
                        key_file=resolved_key,
                    )
                    if ok:
                        merged.append(pr_num)
                    elif m_reason == "paused":
                        paused.append(pr_num)
                    else:
                        has_pending_work = True

        if ledger_dirty:
            _save_json_state(ledger_path, ledger, max_entries=MAX_LEDGER_ENTRIES)

        # Persist ETag ONLY when no PR was left in a non-terminal pending/backoff/CI-waiting state
        if has_pending_work:
            if slug in etags:
                etags.pop(slug, None)
                _save_json_state(etags_path, etags)
        else:
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
    finally:
        try:
            fcntl.flock(repo_lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(repo_lock_fd)


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
