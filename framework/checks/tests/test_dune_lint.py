#!/usr/bin/env python3
"""Unit tests for framework/checks/dune_lint.py deterministic codebase checks."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile

CHECKS_DIR = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dune_lint", CHECKS_DIR / "dune_lint.py")
assert spec is not None and spec.loader is not None
dl = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dl
spec.loader.exec_module(dl)


def run_case(files: dict[str, str], verify_cfg: dict | None = None) -> list[dl.Finding]:
    root = pathlib.Path(tempfile.mkdtemp())
    if verify_cfg is not None:
        (root / ".agent-verify.json").write_text(json.dumps(verify_cfg), encoding="utf-8")
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return dl.lint_repo(root)


def main() -> int:
    failures = 0

    # 1. DUNE001: floating void promise vs caught void promise
    findings = run_case(
        {
            "src/bad.ts": "export function f() {\n  void sendWebhook(payload);\n}\n",
            "src/good.ts": "export function g() {\n  void sendWebhook(payload).catch((err) => console.error(err));\n}\n",
        }
    )
    codes = [(f.code, f.rel_path) for f in findings]
    if codes != [("DUNE001", "src/bad.ts")]:
        print(f"FAIL DUNE001: got {codes}")
        failures += 1
    else:
        print("PASS DUNE001 floating void promise")

    # 2. DUNE002: silent URL fallback on env var (default arg + `or` fallback; allowed in tests)
    findings = run_case(
        {
            "src/config.ts": 'const url = process.env.API_URL || "http://localhost:3000";\n',
            "src/client.py": 'endpoint = os.getenv("SVC_URL", "https://staging.example.com")\n',
            "src/client_or.py": 'endpoint = os.getenv("SVC_URL") or "https://staging.example.com"\n',
            "tests/config.test.ts": 'const url = process.env.API_URL || "http://localhost:3000";\n',
        }
    )
    codes = sorted((f.code, f.rel_path) for f in findings)
    if codes != [
        ("DUNE002", "src/client.py"),
        ("DUNE002", "src/client_or.py"),
        ("DUNE002", "src/config.ts"),
    ]:
        print(f"FAIL DUNE002: got {codes}")
        failures += 1
    else:
        print("PASS DUNE002 env URL fallback (arg + `or` expression)")

    # 3. DUNE003: NestJS @Injectable optional constructor param including balanced decorator parens
    findings = run_case(
        {
            "src/bare_opt.service.ts": (
                "import { Injectable } from '@nestjs/common';\n"
                "@Injectable()\n"
                "export class BareOptService {\n"
                "  constructor(private readonly mailer?: MailerInterface) {}\n"
                "}\n"
            ),
            "src/only_optional.service.ts": (
                "import { Injectable, Optional } from '@nestjs/common';\n"
                "@Injectable()\n"
                "export class OnlyOptionalService {\n"
                "  constructor(@Optional() private readonly mailer?: MailerInterface) {}\n"
                "}\n"
            ),
            "src/only_inject.service.ts": (
                "import { Inject, Injectable } from '@nestjs/common';\n"
                "@Injectable()\n"
                "export class OnlyInjectService {\n"
                "  constructor(@Inject('MAILER') private readonly mailer?: MailerInterface) {}\n"
                "}\n"
            ),
            "src/second_param_bad.service.ts": (
                "import { Inject, Injectable, Optional } from '@nestjs/common';\n"
                "@Injectable()\n"
                "export class SecondParamBadService {\n"
                "  constructor(\n"
                "    @Optional() @Inject('AUDIT') private readonly audit?: AuditPort,\n"
                "    private readonly mailer?: MailerInterface,\n"
                "  ) {}\n"
                "}\n"
            ),
            "src/safe.service.ts": (
                "import { Inject, Injectable, Optional } from '@nestjs/common';\n"
                "@Injectable()\n"
                "export class SafeService {\n"
                "  constructor(@Optional() @Inject('MAILER') private readonly mailer?: MailerInterface) {}\n"
                "}\n"
            ),
        }
    )
    codes = sorted((f.code, f.rel_path) for f in findings)
    expected_d3 = [
        ("DUNE003", "src/bare_opt.service.ts"),
        ("DUNE003", "src/only_inject.service.ts"),
        ("DUNE003", "src/only_optional.service.ts"),
        ("DUNE003", "src/second_param_bad.service.ts"),
    ]
    if codes != expected_d3:
        print(f"FAIL DUNE003: got {codes}, expected {expected_d3}")
        failures += 1
    else:
        print("PASS DUNE003 NestJS optional DI parameter (balanced decorator parens)")

    # 4. DUNE004: CJK in code file AND extensionless shebang script vs allowed locale glob
    cjk_sample = "x = '\u6e2c\u8a66'\n"
    findings = run_case(
        {
            "src/logic.py": cjk_sample,
            "bin/my-cli": "#!/usr/bin/env python3\n# \u5584\u5f8c\nprint('hi')\n",
            "locales/zh.py": cjk_sample,
            "docs/readme.md": cjk_sample,
        },
        verify_cfg={"dune_lint": {"cjk_allow_globs": ["locales/**"]}},
    )
    codes = sorted((f.code, f.rel_path) for f in findings)
    if codes != [("DUNE004", "bin/my-cli"), ("DUNE004", "src/logic.py")]:
        print(f"FAIL DUNE004: got {codes}")
        failures += 1
    else:
        print("PASS DUNE004 CJK code guard (including extensionless shebang scripts)")

    # 5. DUNE005: hardcoded API secret detection (code + markdown deployment docs)
    fake_gemini_key = "AIzaSy" + ("A" * 33)
    findings = run_case(
        {
            "src/llm.ts": f'const KEY = "{fake_gemini_key}";\n',
            "docs/deployment.md": f" Key: `{fake_gemini_key}`\n",
        }
    )
    codes = sorted((f.code, f.rel_path) for f in findings)
    if codes != [("DUNE005", "docs/deployment.md"), ("DUNE005", "src/llm.ts")]:
        print(f"FAIL DUNE005: got {codes}")
        failures += 1
    else:
        print("PASS DUNE005 secret literal detection (code + markdown)")

    # 6. Actionable remediation text must accompany every finding
    if not all("Remediation:" in f.format() for f in findings):
        print("FAIL remediation guidance missing")
        failures += 1
    else:
        print("PASS actionable remediation message format")

    # 7. tracked_only: a push gate judges tracked files; another session's untracked scratch file must not
    #    fail it, while the same text in a tracked file still does (and the default still reads both)
    import subprocess

    root = pathlib.Path(tempfile.mkdtemp())
    cjk = "x = 1  # \u6e2c\u8a66\n"
    (root / "tracked_ok.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tracked_bad.py").write_text(cjk, encoding="utf-8")
    (root / "scratch.py").write_text(cjk, encoding="utf-8")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "tracked_ok.py", "tracked_bad.py"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    tracked = sorted(f.rel_path for f in dl.lint_repo(root, tracked_only=True))
    everything = sorted(f.rel_path for f in dl.lint_repo(root))
    if tracked != ["tracked_bad.py"] or everything != ["scratch.py", "tracked_bad.py"]:
        print(f"FAIL tracked_only: tracked={tracked} everything={everything}")
        failures += 1
    else:
        print("PASS tracked_only skips untracked files and still reads tracked ones")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
