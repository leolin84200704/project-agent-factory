#!/usr/bin/env python3
"""Regression and behavioral test suite for framework/scripts/memory_scoring.py.

Covers:
1. Idempotent write_frontmatter() separator handling (no blank-line growth).
2. Summary and status clamping in _index.md (<=160 chars single-line summary,
   <=40 chars status, stripping historical context tails) WITHOUT mutating L3 files.
3. Unified instance.json support (canonical + alias keys) for both 'code' and 'personal'.
4. Deterministic journal/_index.md generation sorted newest-first + bounded row cap.
5. PyYAML-absent fallback safety: multiline block scalars, escaped quotes, and
   surgical block-list `links:` replacement without orphaned `- item` lines.
6. Backward-compatible `eval.py` import and `collect()` execution.

Run: python3 framework/scripts/tests/memory-scoring-frontmatter.test.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
from datetime import date

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("memory_scoring", SCRIPTS / "memory_scoring.py")
assert spec is not None and spec.loader is not None
ms = importlib.util.module_from_spec(spec)
sys.modules["memory_scoring"] = ms
spec.loader.exec_module(ms)


def blanks_after_frontmatter(text: str) -> int:
    lines = text.split("\n")
    delims = [i for i, line in enumerate(lines) if line.strip() == "---"]
    assert len(delims) >= 2, "frontmatter delimiters missing"
    count = 0
    for line in lines[delims[1] + 1 :]:
        if line.strip() == "":
            count += 1
        else:
            break
    return count


def check_frontmatter_idempotence(name: str, initial: str, writes: int = 4) -> bool:
    path = pathlib.Path(tempfile.mkdtemp()) / "t.md"
    path.write_text(initial, encoding="utf-8")
    counts = []
    for i in range(writes):
        meta = ms.read_frontmatter(path)
        meta["score"] = i
        ms.write_frontmatter(path, meta)
        counts.append(blanks_after_frontmatter(path.read_text(encoding="utf-8")))

    final = path.read_text(encoding="utf-8")
    stable = len(set(counts)) == 1
    exactly_one = counts[-1] == 1
    body_kept = "# Heading" in final and "body text" in final
    meta_kept = ms.read_frontmatter(path).get("id") == "X"
    ok = stable and exactly_one and body_kept and meta_kept
    print(f"{'PASS' if ok else 'FAIL'} {name}: blanks_per_write={counts}")
    return ok


def test_personal_instance_clamping_and_journal() -> bool:
    root = pathlib.Path(tempfile.mkdtemp())
    (root / ".agent-factory").mkdir()
    (root / ".agent-factory" / "instance.json").write_text(
        json.dumps(
            {
                "mode": "personal",  # alias for index_style
                "tier_dirs": {"stm": "short-term-memory", "ltm": "long-term-memory"},
                "half_life": {"stm": 30, "ltm": 180},
                "max_journal_index_entries": 2,
            }
        ),
        encoding="utf-8",
    )
    stm_dir = root / "short-term-memory"
    ltm_dir = root / "long-term-memory"
    journal_dir = root / "journal"
    stm_dir.mkdir()
    ltm_dir.mkdir()
    journal_dir.mkdir()

    history_marker = "\u4ee5\u4e0b\u70ba 11/08 \u8108\u7d61"
    huge_summary = (
        "Current actionable headline with pipe | character and details "
        + ("very-long-clause " * 20)
        + f" {history_marker} "
        + ("old-dream-history " * 400)
    )
    long_status = "ROOT-CAUSE-CONFIRMED-by-professor-2026-10-20-submissions-stuck-in-draft-mode; extra-notes"
    stm_file = stm_dir / "coursework.md"
    # Include an existing block-list `links:` to verify update_frontmatter_fields
    # consumes `- old.md` continuation lines cleanly without leaving orphan items!
    original_stm_body = (
        "---\n"
        "title: Coursework tracker\n"
        f"status: {long_status}\n"
        "deadline: 2026-12-18\n"
        "category: education\n"
        "updated: 2026-11-15\n"
        "tags: [mba, moodle, assignment]\n"
        "links:\n"
        "- stale-link.md\n"
        f"summary: {huge_summary}\n"
        "note: |\n"
        "  keep multiline note untouched\n"
        "---\n\n"
        "# Body stays intact\n"
    )
    stm_file.write_text(original_stm_body, encoding="utf-8")

    (ltm_dir / "patterns.md").write_text(
        "---\n"
        "title: Core patterns\n"
        "category: work\n"
        "updated: 2026-11-10\n"
        "tags: [mba, moodle, assignment]\n"
        "summary: Durable operational patterns.\n"
        "---\n\n# Patterns\n",
        encoding="utf-8",
    )

    for d_str, slug, h1 in (
        ("2026-11-01", "oldest-entry", "# Oldest Heading"),
        ("2026-11-10", "older-entry", "# Older Journal Heading"),
        ("2026-11-15", "newer-entry", "# Newer Heading"),
    ):
        (journal_dir / f"{d_str}-{slug}.md").write_text(
            f"---\ndate: {d_str}\nslug: {slug}\ndistilled: true\n---\n\n{h1}\n",
            encoding="utf-8",
        )

    stats = ms.run_for_agent(root, write=True)
    stm_index = (stm_dir / "_index.md").read_text(encoding="utf-8")
    journal_index = (journal_dir / "_index.md").read_text(encoding="utf-8")
    after_stm = stm_file.read_text(encoding="utf-8")

    ok = True
    if len(stm_index.encode("utf-8")) > 1500:
        print(f"FAIL personal stm_index too large: {len(stm_index)}")
        ok = False
    if "old-dream-history" in stm_index or "old-dream-history" not in after_stm:
        print("FAIL L3 preservation vs L2 clamping mismatch")
        ok = False
    if "keep multiline note untouched" not in after_stm:
        print("FAIL multiline note corrupted")
        ok = False
    if "\n- stale-link.md" in after_stm:
        print("FAIL block-list continuation `- stale-link.md` was orphaned instead of consumed")
        ok = False
    if "patterns.md" not in after_stm:
        print("FAIL personal auto-linking did not link coursework.md <-> patterns.md")
        ok = False
    # Journal index capped at max_journal_index_entries=2: newest 2 shown, oldest omitted with note
    pos_new = journal_index.find("newer-entry")
    pos_old = journal_index.find("older-entry")
    if pos_new == -1 or pos_old == -1 or pos_new > pos_old:
        print("FAIL journal_index ordering wrong")
        ok = False
    if "oldest-entry" in journal_index or "+1 older entries" not in journal_index:
        print("FAIL journal_index cap did not bound older entries")
        ok = False
    if stats.get("journal", {}).get("count") != 3:
        print(f"FAIL journal stats count={stats.get('journal')}")
        ok = False

    print(f"{'PASS' if ok else 'FAIL'} personal instance clamping + bounded journal + block-list consumption")
    return ok


def test_code_instance_and_no_yaml_fallback() -> bool:
    root = pathlib.Path(tempfile.mkdtemp())
    stm_dir = root / "storage" / "short_term_memory"
    ltm_dir = root / "long-term-memory"
    archive_dir = root / "archive"
    journal_dir = root / "journal"
    for d in (stm_dir, ltm_dir, archive_dir, journal_dir):
        d.mkdir(parents=True)

    t1 = stm_dir / "ticket-1.md"
    t2 = stm_dir / "ticket-2.md"
    t1.write_text(
        "---\n"
        "id: ticket-1\n"
        "type: stm\n"
        "base_weight: 0.9\n"
        "urgency: 3\n"
        "updated: 2026-11-15\n"
        "tags:\n"
        "- api\n"
        "- docker\n"
        "- service\n"
        'summary: "Ticket: alpha \\"quoted\\" detail"\n'
        "note: |\n"
        "  line one of note\n"
        "  line two of note\n"
        "---\n\n# Ticket 1\n",
        encoding="utf-8",
    )
    t2.write_text(
        "---\n"
        "id: ticket-2\n"
        "type: stm\n"
        "base_weight: 0.9\n"
        "urgency: 2\n"
        "updated: 2026-11-14\n"
        "tags:\n"
        "- api\n"
        "- docker\n"
        "- service\n"
        "summary: Second ticket\n"
        "---\n\n# Ticket 2\n",
        encoding="utf-8",
    )

    saved_yaml = ms.yaml
    try:
        ms.yaml = None  # Force fallback parser/writer path
        for _ in range(3):
            ms.run_for_agent(root, write=True)
        meta1 = ms.read_frontmatter(t1)
        raw1 = t1.read_text(encoding="utf-8")
        # Also test write_frontmatter round-trip with multiline + quotes when yaml is None
        rt_path = root / "rt.md"
        ms.write_frontmatter(
            rt_path,
            {"id": "RT-1", "summary": 'Say: "hello"', "note": "first line\nsecond line"},
        )
        rt_meta = ms.read_frontmatter(rt_path)
    finally:
        ms.yaml = saved_yaml

    ok = (
        "line one of note\n  line two of note" in raw1
        and "links:\n- ticket-2\n" in raw1
        and '\\\\"' not in raw1
        and meta1.get("links") == ["ticket-2"]
        and rt_meta.get("summary") == 'Say: "hello"'
        and rt_meta.get("note") == "first line\nsecond line"
    )
    print(f"{'PASS' if ok else 'FAIL'} code mode + PyYAML-absent multiline/quote/block-links preservation")
    return ok


def test_eval_py_compatibility() -> bool:
    root = pathlib.Path(tempfile.mkdtemp())
    (root / "storage" / "short_term_memory").mkdir(parents=True)
    (root / "long-term-memory").mkdir(parents=True)
    (root / "archive").mkdir(parents=True)
    (root / "storage" / "short_term_memory" / "VP-1.md").write_text(
        "---\nid: VP-1\ntype: stm\nupdated: 2026-11-15\nsummary: test\n---\n\n# VP-1\n",
        encoding="utf-8",
    )
    os.environ["PAF_AGENT_ROOT"] = str(root)
    try:
        # Reload memory_scoring + eval under PAF_AGENT_ROOT
        spec_ms = importlib.util.spec_from_file_location("memory_scoring", SCRIPTS / "memory_scoring.py")
        assert spec_ms and spec_ms.loader
        mod_ms = importlib.util.module_from_spec(spec_ms)
        sys.modules["memory_scoring"] = mod_ms
        spec_ms.loader.exec_module(mod_ms)

        spec_ev = importlib.util.spec_from_file_location("eval_mod", SCRIPTS / "eval.py")
        assert spec_ev and spec_ev.loader
        mod_ev = importlib.util.module_from_spec(spec_ev)
        spec_ev.loader.exec_module(mod_ev)
        collected = mod_ev.collect()
        sc, days = mod_ms.score_file(
            root / "storage" / "short_term_memory" / "VP-1.md",
            {"type": "stm", "updated": "2026-11-15", "base_weight": 0.9},
            2,
            date(2026, 11, 15),
        )
    finally:
        os.environ.pop("PAF_AGENT_ROOT", None)
        sys.modules["memory_scoring"] = ms

    ok = collected.get("tiers", {}).get("stm", {}).get("count") == 1 and sc > 0 and days == 0
    print(f"{'PASS' if ok else 'FAIL'} eval.py import + collect() + 4-arg score_file compatibility")
    return ok


def main() -> int:
    head = "---\nid: X\nscore: 0\n---\n"
    body = "# Heading\n\nbody text\n"
    cases = [
        ("clean file", head + "\n" + body),
        ("no blank separator", head + body),
        ("already polluted (40 blanks)", head + "\n" * 40 + body),
        ("polluted (3 blanks)", head + "\n\n\n" + body),
    ]
    results = [check_frontmatter_idempotence(name, initial) for name, initial in cases]

    path = pathlib.Path(tempfile.mkdtemp()) / "t.md"
    path.write_text(body, encoding="utf-8")
    ms.write_frontmatter(path, {"id": "X", "score": 1})
    first = path.read_text(encoding="utf-8")
    ms.write_frontmatter(path, ms.read_frontmatter(path))
    second = path.read_text(encoding="utf-8")
    ok_nofm = (
        blanks_after_frontmatter(first) == 1
        and blanks_after_frontmatter(second) == 1
        and "# Heading" in second
        and first == second
    )
    print(f"{'PASS' if ok_nofm else 'FAIL'} no frontmatter at all: idempotent={first == second}")
    results.append(ok_nofm)

    results.append(test_personal_instance_clamping_and_journal())
    results.append(test_code_instance_and_no_yaml_fallback())
    results.append(test_eval_py_compatibility())

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
