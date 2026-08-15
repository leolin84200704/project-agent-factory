#!/usr/bin/env python3
"""
Extract the `## Failures` section from every STM file and consolidate them
into `long-term-memory/failures.md` — a single index keyed by root-cause theme.

Designed to run as part of the dream/weekly pipeline (idempotent) and
on-demand. Output file is regenerated each run; do not edit by hand.

ADAPT PER INSTANCE: the THEMES table below ships with generic engineering
themes. Add instance-specific themes (your stack's failure modes) ABOVE the
generic ones — first match wins.

Usage:
  python3 scripts/extract-failures.py          # write LTM file
  python3 scripts/extract-failures.py --print  # print to stdout only
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STM_DIR = ROOT / "storage" / "short_term_memory"
LTM_OUT = ROOT / "long-term-memory" / "failures.md"

# ── ADAPT PER INSTANCE ───────────────────────────────────────────
# Heuristic theme classifier: first match wins. Most specific themes first.
THEMES: list[tuple[str, str, re.Pattern]] = [
    ("build-tooling",       "Build / compile / tooling",
     re.compile(r"tsc|tsconfig|webpack|build fail|compile|lint|bundler|codegen|generate", re.I)),
    ("deploy-coordination", "Deploy / commit / push coordination",
     re.compile(r"\bdeploy\b|不用 build|該不該|commit ↔|push 沒先|build 嗎|該 deploy|是否需要 deploy|rollout", re.I)),
    ("prod-side-effects",   "Production side-effects (queues / email / webhooks)",
     re.compile(r"prod\b|production|kafka|queue|email send|webhook|fire-and-forget|side.effect", re.I)),
    ("db-migration",        "DB / migration / backfill",
     re.compile(r"migration|backfill|schema\b|psql|sqlite|INSERT|UPDATE|duplicate|constraint|transaction.*rollback|column", re.I)),
    ("scope-communication", "Scope / requirement / communication",
     re.compile(r"scope|誤解|否定句|implicit|expand|擴大|requirement|assumption|first.pass|wrong pattern", re.I)),
    ("error-handling",      "Error handling / throw vs log / silent failure",
     re.compile(r"throw|try.catch|catch|error.message|報錯|silent|swallow", re.I)),
    ("tool-usage",          "Tool / cwd / branch / repo confusion",
     re.compile(r"cwd|persistence|wrong repo|wrong branch|cross-repo|git switch|git checkout.*wrong", re.I)),
    ("auth-permission",     "Auth / permission / role",
     re.compile(r"role|permission|admin|Forbidden|token|credential|401|403", re.I)),
    ("external-service",    "External service / API / rate limit",
     re.compile(r"rate.limit|quota|429|timeout|deadline|UNAVAILABLE|ENOTFOUND|third.party|外部", re.I)),
    ("test-mocking",        "Test / mock / spec",
     re.compile(r"jest|pytest|mock|spec\.|integration test|e2e|fixture", re.I)),
]
DEFAULT_THEME = ("other", "Other / uncategorized")
# ─────────────────────────────────────────────────────────────────

# Body content is treated as empty/placeholder if it matches any of these.
PLACEHOLDER_RE = re.compile(
    r"^\s*(?:[（(]?\s*(?:無|none|N/A|pending|尚未 execute|尚未執行)\s*[)）]?|_+\s*\([^)]+\)\s*_+)\s*$",
    re.I,
)
MIN_ENTRY_BODY_LEN = 20  # below this we treat as not-an-entry

FAILURES_RE = re.compile(
    r"^##\s+Failures\s*$\n(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL
)
SUB_ENTRY_RE = re.compile(
    r"###\s+(?:\[(?P<date>[\d\- :]+)\]\s+)?(?P<title>.+?)\s*$", re.MULTILINE
)


def extract_section(text: str) -> str | None:
    m = FAILURES_RE.search(text)
    if not m:
        return None
    body = m.group(1).strip()
    if not body or re.fullmatch(r"[（(]\s*(無|none|N/A|pending)\s*[)）]", body, re.I):
        return None
    return body


def classify(text: str) -> tuple[str, str]:
    for key, label, pat in THEMES:
        if pat.search(text):
            return key, label
    return DEFAULT_THEME


def split_entries(body: str) -> list[tuple[str, str, str]]:
    """
    Split a failures section into individual entries.
    Returns [(date, title, body), ...] — falls back to a single ('', '', body)
    if no ### sub-headers exist.
    """
    headers = list(SUB_ENTRY_RE.finditer(body))
    if not headers:
        return [("", "", body.strip())]
    entries = []
    for i, h in enumerate(headers):
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        chunk = body[start:end].strip()
        entries.append((h.group("date") or "", h.group("title").strip(), chunk))
    return entries


def _is_placeholder_entry(title: str, body: str) -> bool:
    """Treat as placeholder if there's no meaningful content."""
    body_stripped = body.strip()
    if not body_stripped and not title.strip():
        return True
    if not body_stripped or PLACEHOLDER_RE.match(body_stripped):
        if not title.strip() or PLACEHOLDER_RE.match(title.strip()):
            return True
        if len(title.strip()) < MIN_ENTRY_BODY_LEN:
            return True
    return False


def gather() -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    if not STM_DIR.exists():
        return grouped
    for stm in sorted(STM_DIR.glob("*.md")):
        if stm.name.startswith("_"):
            continue
        text = stm.read_text(encoding="utf-8")
        body = extract_section(text)
        if not body:
            continue
        ticket = stm.stem
        for entry_date, title, entry_body in split_entries(body):
            if _is_placeholder_entry(title, entry_body):
                continue
            key = classify(f"{title}\n{entry_body}")
            grouped[key].append({
                "ticket": ticket,
                "date": entry_date.strip(),
                "title": title,
                "body": entry_body,
            })
    return grouped


def render(grouped: dict[tuple[str, str], list[dict]]) -> str:
    today = date.today().isoformat()
    total = sum(len(v) for v in grouped.values())

    # Collect outbound links: every ticket that contributed an entry, plus
    # any [[xxx]] reference inside an entry body. Ordered, deduped.
    seen: set[str] = set()
    ordered_links: list[str] = []
    body_link_re = re.compile(r"\[\[([\w\-]+)\]\]")
    for entries in grouped.values():
        for e in entries:
            for tid in [e["ticket"], *body_link_re.findall(e["body"])]:
                if tid and tid not in seen and tid != "failures":
                    seen.add(tid)
                    ordered_links.append(tid)

    links_yaml = "\n".join(f"- {tid}" for tid in ordered_links) if ordered_links else ""

    out = [
        "---",
        "id: failures",
        "type: ltm",
        "category: technical",
        "status: active",
        "score: 0.0",
        "base_weight: 0.9",
        "urgency: 3",
        f"created: {today}",
        f"updated: {today}",
        "links:" if ordered_links else "links: []",
    ]
    if ordered_links:
        out.append(links_yaml)
    out += [
        "tags:",
        "- failures",
        "- root-cause",
        "- auto-generated",
        f'summary: Auto-aggregated failure index from {total} entries across STM',
        "---",
        "",
        "# Failure Index",
        "",
        "> 自動生成自 `storage/short_term_memory/*.md` 的 `## Failures` 區段。",
        "> 由 `scripts/extract-failures.py` 維護，手動編輯會被下次 run 覆蓋。",
        f"> Last updated: {today} — total {total} entries",
        "",
        "## Themes",
        "",
    ]
    # ToC
    for (key, label), entries in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        out.append(f"- [{label}](#{key}) — {len(entries)} entries")
    out.append("")
    out.append("---")
    out.append("")

    # Sections
    for (key, label), entries in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        out.append(f"## {label} <a id='{key}'></a>")
        out.append("")
        for e in entries:
            header_bits = [f"**[[{e['ticket']}]]**"]
            if e["date"]:
                header_bits.append(f"`{e['date']}`")
            if e["title"]:
                header_bits.append(e["title"])
            out.append("### " + " — ".join(header_bits))
            out.append("")
            out.append(e["body"])
            out.append("")
        out.append("---")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="print to stdout, do not write file")
    args = ap.parse_args()

    grouped = gather()
    rendered = render(grouped)
    if args.print:
        sys.stdout.write(rendered)
        return
    LTM_OUT.parent.mkdir(exist_ok=True)
    LTM_OUT.write_text(rendered, encoding="utf-8")
    total = sum(len(v) for v in grouped.values())
    print(f"Wrote {LTM_OUT.relative_to(ROOT)} — {total} entries across {len(grouped)} themes")


if __name__ == "__main__":
    main()
