#!/usr/bin/env python3
"""Dune Architecture deterministic codebase linter (Lauren Tan style).

Enforces high-signal structural invariants across TypeScript, JavaScript, Python,
SQL, C#, Go, and Shell files (including extensionless shebang scripts) so agents
catch incident-class bugs locally in <1s with exact remediation instructions
embedded in every failure output.

Rules:
  DUNE001  Uncaught floating `void fn()` promise in TypeScript/JavaScript
  DUNE002  Silent hardcoded URL fallback on environment variable outside tests
  DUNE003  NestJS `@Injectable` optional constructor parameter missing `@Optional()`/`@Inject()`
  DUNE004  Non-English (CJK) text in source code (configurable allowlist via `.agent-verify.json`)
  DUNE005  Hardcoded API credential / secret token literal in tracked source
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "coverage",
    ".turbo",
    ".next",
    "Library",
    "Temp",
    "Obj",
    "Logs",
}

CODE_EXTS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".cs",
    ".sql",
    ".go",
    ".sh",
}
TS_JS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

VOID_CALL_RE = re.compile(r"^\s*void\s+[A-Za-z_$][\w$.]*\s*\(")
JS_ENV_URL_RE = re.compile(
    r"""process\.env\.[A-Z0-9_]+\s*(?:\|\||\?\?)\s*['"`]https?://"""
)
PY_ENV_URL_RE = re.compile(
    r"""os\.(?:getenv|environ\.get)\(\s*['"][A-Z0-9_]+['"]\s*(?:,\s*['"]https?://|\)\s+or\s+['"]https?://)"""
)
INJECTABLE_RE = re.compile(r"@Injectable\s*\(")
CONSTRUCTOR_HEAD_RE = re.compile(r"\bconstructor\s*\(")
OPTIONAL_CTOR_PARAM_RE = re.compile(
    r"(?:private|public|protected|readonly|\s|^)+([A-Za-z_$][\w$]*)\s*\?\s*:\s*([A-Z][\w$]*)"
)
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
SECRET_RES = [
    re.compile(r"\bAIzaSy[0-9A-Za-z_-]{33}\b"),
    re.compile(r"\bghp_[0-9A-Za-z]{36}\b"),
    re.compile(r"\bsk-(?:proj-)?[0-9A-Za-z_-]{32,}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b"),
]


@dataclass(frozen=True)
class Finding:
    code: str
    rel_path: str
    line: int
    message: str
    remediation: str

    def format(self) -> str:
        return (
            f"{self.rel_path}:{self.line}: [{self.code}] {self.message}\n"
            f"  Remediation: {self.remediation}"
        )


def _is_test_path(rel_path: str) -> bool:
    low = rel_path.lower()
    parts = low.split("/")
    if any(p in ("test", "tests", "__tests__", "fixtures", "evals") for p in parts):
        return True
    name = parts[-1]
    return (
        ".spec." in name
        or ".test." in name
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.py")
        or name.endswith(".test.sh")
    )


def _glob_to_regex(pat: str) -> re.Pattern[str]:
    out = ["^"]
    i = 0
    n = len(pat)
    while i < n:
        if pat[i : i + 3] == "**/":
            out.append("(?:.+/)?")
            i += 3
        elif pat[i : i + 3] == "/**":
            out.append("(?:/.*)?")
            i += 3
        elif pat[i : i + 2] == "**":
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _matches_any(rel_path: str, globs: Iterable[str]) -> bool:
    for pat in globs:
        if fnmatch.fnmatch(rel_path, pat) or _glob_to_regex(pat).match(rel_path):
            return True
    return False


def _load_lint_config(repo_root: Path) -> dict:
    cfg_path = repo_root / ".agent-verify.json"
    if not cfg_path.is_file():
        return {}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return data.get("dune_lint") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _detect_effective_ext(file_path: Path, first_line: str) -> str:
    ext = file_path.suffix.lower()
    if ext:
        return ext
    if first_line.startswith("#!"):
        low = first_line.lower()
        if "python" in low:
            return ".py"
        if any(sh in low for sh in ("bash", "/sh", " env sh", "zsh")):
            return ".sh"
        if "node" in low or "ts-node" in low:
            return ".js"
    return ""


def _extract_balanced_constructors(content: str) -> list[tuple[int, str]]:
    """Extract `(line_number, params_blob)` for every `constructor(...)` with balanced parens."""
    results: list[tuple[int, str]] = []
    n = len(content)
    for m in CONSTRUCTOR_HEAD_RE.finditer(content):
        open_idx = m.end() - 1
        line_no = content.count("\n", 0, m.start()) + 1
        depth = 1
        i = open_idx + 1
        quote: str | None = None
        while i < n and depth > 0:
            ch = content[i]
            if quote is not None:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                i += 1
                continue
            if ch in ("'", '"', "`"):
                quote = ch
                i += 1
                continue
            if ch == "/" and i + 1 < n and content[i + 1] == "/":
                nl = content.find("\n", i + 2)
                i = n if nl == -1 else nl + 1
                continue
            if ch == "/" and i + 1 < n and content[i + 1] == "*":
                end_c = content.find("*/", i + 2)
                i = n if end_c == -1 else end_c + 2
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    results.append((line_no, content[open_idx + 1 : i]))
                    break
            i += 1
    return results


def _split_top_level_commas(blob: str) -> list[str]:
    """Split a parameter list by `,` only when nested `()[]{}<>` and quotes are at depth 0."""
    parts: list[str] = []
    cur: list[str] = []
    paren = bracket = brace = angle = 0
    quote: str | None = None
    i = 0
    n = len(blob)
    while i < n:
        ch = blob[i]
        if quote is not None:
            cur.append(ch)
            if ch == "\\" and i + 1 < n:
                cur.append(blob[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            cur.append(ch)
            i += 1
            continue
        if ch == "(":
            paren += 1
        elif ch == ")" and paren > 0:
            paren -= 1
        elif ch == "[":
            bracket += 1
        elif ch == "]" and bracket > 0:
            bracket -= 1
        elif ch == "{":
            brace += 1
        elif ch == "}" and brace > 0:
            brace -= 1
        elif ch == "<":
            angle += 1
        elif ch == ">" and angle > 0:
            angle -= 1
        elif ch == "," and paren == 0 and bracket == 0 and brace == 0 and angle == 0:
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        parts.append("".join(cur))
    return parts


GIT_LOCAL_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
)


def _clean_git_env() -> dict[str, str]:
    env = os.environ.copy()
    for var in GIT_LOCAL_ENV_VARS:
        env.pop(var, None)
    return env


def _iter_repo_files(repo_root: Path, changed_only: bool = False, tracked_only: bool = False) -> list[Path]:
    """Files to lint. `tracked_only` leaves out untracked files: a push gate judges what is being pushed,
    and in a checkout shared by several sessions and scheduled jobs someone else's scratch file must not
    fail everyone's push. Without git there is nothing to tell tracked from untracked, so every file counts."""
    git_env = _clean_git_env()
    if changed_only and (repo_root / ".git").exists():
        try:
            diff_proc = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD"],
                cwd=repo_root,
                env=git_env,
                capture_output=True,
                text=True,
                check=False,
            )
            untracked_proc = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=repo_root,
                env=git_env,
                capture_output=True,
                text=True,
                check=False,
            )
            rels = {
                p.strip()
                for p in (diff_proc.stdout.splitlines() + untracked_proc.stdout.splitlines())
                if p.strip()
            }
            return sorted(
                repo_root / rel for rel in rels if (repo_root / rel).is_file()
            )
        except OSError:
            pass

    if (repo_root / ".git").exists():
        try:
            proc = subprocess.run(
                ["git", "ls-files", "--cached"] + ([] if tracked_only else ["--others", "--exclude-standard"]),
                cwd=repo_root,
                env=git_env,
                capture_output=True,
                text=True,
                check=True,
            )
            rels = {p.strip() for p in proc.stdout.splitlines() if p.strip()}
            return sorted(
                repo_root / rel for rel in rels if (repo_root / rel).is_file()
            )
        except (OSError, subprocess.CalledProcessError):
            pass

    results: list[Path] = []
    for cur_root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        base = Path(cur_root)
        for fname in files:
            results.append(base / fname)
    return sorted(results)


def lint_file(repo_root: Path, file_path: Path, cfg: dict) -> list[Finding]:
    rel_path = file_path.relative_to(repo_root).as_posix()
    ignore_globs = cfg.get("ignore_globs") or []
    disabled_rules = set(cfg.get("disable_rules") or [])
    cjk_allow_globs = cfg.get("cjk_allow_globs") or []

    if _matches_any(rel_path, ignore_globs):
        return []

    is_test = _is_test_path(rel_path)

    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    lines = content.splitlines()
    first_line = lines[0] if lines else ""
    ext = _detect_effective_ext(file_path, first_line)

    findings: list[Finding] = []

    # DUNE005: secret literal scan across non-test text/code/markdown files
    if (
        "DUNE005" not in disabled_rules
        and not is_test
        and ext in (CODE_EXTS | {".json", ".yaml", ".yml", ".toml", ".md"})
    ):
        for idx, line in enumerate(lines, start=1):
            if any(rx.search(line) for rx in SECRET_RES):
                findings.append(
                    Finding(
                        code="DUNE005",
                        rel_path=rel_path,
                        line=idx,
                        message="Hardcoded credential or API key literal detected in source.",
                        remediation="Move the secret to an environment variable or Secret Manager, rotate the key if real, and ensure `.env` is gitignored.",
                    )
                )

    if ext not in CODE_EXTS:
        return findings

    # DUNE001: uncaught floating `void fn()` in TS/JS
    if "DUNE001" not in disabled_rules and ext in TS_JS_EXTS and not is_test:
        for idx, line in enumerate(lines, start=1):
            if VOID_CALL_RE.search(line):
                window = " ".join(lines[idx - 1 : min(idx + 2, len(lines))])
                if ".catch(" not in window:
                    findings.append(
                        Finding(
                            code="DUNE001",
                            rel_path=rel_path,
                            line=idx,
                            message="Unhandled floating promise via `void fn()`.",
                            remediation="Attach `.catch((err) => logger.error({ err }, 'background task failed'))` or `await` the promise so rejections are never silently dropped.",
                        )
                    )

    # DUNE002: silent URL fallback on missing environment variable
    if "DUNE002" not in disabled_rules and not is_test:
        if ext in TS_JS_EXTS:
            for idx, line in enumerate(lines, start=1):
                if JS_ENV_URL_RE.search(line):
                    findings.append(
                        Finding(
                            code="DUNE002",
                            rel_path=rel_path,
                            line=idx,
                            message="Silent hardcoded URL fallback on `process.env` variable.",
                            remediation="Validate required URL environment variables at startup and throw immediately when unset instead of falling back to localhost/staging URLs.",
                        )
                    )
        elif ext == ".py":
            for idx, line in enumerate(lines, start=1):
                if PY_ENV_URL_RE.search(line):
                    findings.append(
                        Finding(
                            code="DUNE002",
                            rel_path=rel_path,
                            line=idx,
                            message="Silent hardcoded URL fallback on `os.getenv` / `os.environ.get`.",
                            remediation="Require explicit URL configuration at startup (`os.environ['VAR']` or validated Settings) instead of defaulting to a fallback URL.",
                        )
                    )

    # DUNE003: NestJS @Injectable optional constructor parameter without @Optional() @Inject()
    if (
        "DUNE003" not in disabled_rules
        and ext == ".ts"
        and not is_test
        and INJECTABLE_RE.search(content)
    ):
        for ctor_line, params_blob in _extract_balanced_constructors(content):
            for raw_param in _split_top_level_commas(params_blob):
                param = raw_param.strip()
                if not param:
                    continue
                if OPTIONAL_CTOR_PARAM_RE.search(param) and (
                    "@Inject(" not in param or "@Optional(" not in param
                ):
                    findings.append(
                        Finding(
                            code="DUNE003",
                            rel_path=rel_path,
                            line=ctor_line,
                            message="NestJS `@Injectable` constructor has TypeScript `?` parameter without `@Optional() @Inject(TOKEN)`.",
                            remediation="Decorate optional NestJS dependencies with `@Optional() @Inject(TOKEN)` (TypeScript `?` does not make Nest DI optional, and interfaces erase to `Object` at runtime).",
                        )
                    )

    # DUNE004: CJK characters in code files (unless explicitly allowlisted)
    if "DUNE004" not in disabled_rules and not _matches_any(rel_path, cjk_allow_globs):
        for idx, line in enumerate(lines, start=1):
            if CJK_RE.search(line):
                findings.append(
                    Finding(
                        code="DUNE004",
                        rel_path=rel_path,
                        line=idx,
                        message="Non-English (CJK) text found in code file.",
                        remediation="Write all code, comments, and identifiers in English. If this file legitimately holds localized user-facing prompts/copy, add its glob to `dune_lint.cjk_allow_globs` in `.agent-verify.json`.",
                    )
                )
                break  # One finding per file keeps output concise

    return findings


def lint_repo(repo_root: Path, changed_only: bool = False, tracked_only: bool = False) -> list[Finding]:
    repo_root = repo_root.expanduser().resolve()
    cfg = _load_lint_config(repo_root)
    findings: list[Finding] = []
    for fpath in _iter_repo_files(repo_root, changed_only=changed_only, tracked_only=tracked_only):
        findings.extend(lint_file(repo_root, fpath, cfg))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="repository root directory to lint (default: cwd)",
    )
    parser.add_argument(
        "--changed-only",
        action="store_true",
        help="only lint files modified or untracked relative to HEAD",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON output")
    args = parser.parse_args(argv)

    findings = lint_repo(args.repo, changed_only=args.changed_only)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "code": f.code,
                        "file": f.rel_path,
                        "line": f.line,
                        "message": f.message,
                        "remediation": f.remediation,
                    }
                    for f in findings
                ],
                indent=2,
            )
        )
    elif findings:
        for f in findings:
            print(f.format())
        print(f"\ndune_lint: {len(findings)} violation(s) found.")
    else:
        print("dune_lint: 0 violations.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
