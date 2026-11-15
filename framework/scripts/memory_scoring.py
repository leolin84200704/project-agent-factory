#!/usr/bin/env python3
"""Unified memory scoring, Zettelkasten linking, and L2 index builder.

Supports both Engineering/Code Agent instances ('code' mode) and Personal/Life
Agent instances ('personal' mode) via optional `.agent-factory/instance.json`
or directory layout auto-detection, eliminating per-instance script forks.

Key guarantees:
1. L2 `_index.md` summary/one-line cells are clamped to <=160 single-line chars
   (preferring frontmatter `hook:` if present, stripping historical Dream
   context tails, newlines, and unescaped pipes) and status cells to <=40 chars
   so L2 routing tables stay strictly within the 2,000-token startup budget.
2. L3 `.md` file bodies and full frontmatter `summary:` / multiline block
   scalars (`note: |`) are NEVER truncated or corrupted by index clamping or
   surgical `links:` / `score:` updates (even when `PyYAML` is absent).
3. `journal/_index.md` is deterministically rebuilt newest-first from
   `journal/*.md` frontmatter (with `# Heading` fallback) and capped to the
   newest `max_journal_index_entries` rows (default 50) so L2 startup size stays
   bounded forever.
4. Backward-compatible exports (`TIER_DIRS`, `list_tier_files`, `score_file`,
   `read_frontmatter`, `write_frontmatter`, `rescore_and_reindex`) are preserved
   for `eval.py` and thin instance wrappers (`PAF_AGENT_ROOT`).

Usage:
  python3 memory_scoring.py [--dir /path/to/agent] [--stats]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

DEFAULT_AGENT_ROOT = Path(
    os.environ.get("PAF_AGENT_ROOT") or Path(__file__).resolve().parent.parent
).resolve()

TIER_NAMES = {
    "stm": "Short-Term Memory",
    "ltm": "Long-Term Memory",
    "archive": "Archive",
}

LTM_FIXED_RECENCY = 0.99
NORMALIZATION = 8.0
URGENCY_BOOST_PER_LEVEL = 0.15
DEFAULT_MAX_KEYWORD_DF = 0.20
DEFAULT_LINK_BOOST_CAP = 20
DEFAULT_MAX_JOURNAL_INDEX_ENTRIES = 50

DEFAULT_CODE_KEYWORDS: dict[str, list[str]] = {
    "domain": [],
    "technical": [
        "api",
        "database",
        "sql",
        "docker",
        "service",
        "module",
        "queue",
        "cache",
        "typescript",
        "python",
    ],
    "repo_patterns": [
        "build",
        "deploy",
        "config",
        "env",
        "migration",
        "script",
        "pattern",
        "gotcha",
        "investigation",
    ],
    "pm_patterns": ["ticket", "routing", "pm", "sprint", "requirement"],
}

DEFAULT_PERSONAL_WEIGHTS: dict[str, float] = {
    "immigration": 1.0,
    "work": 0.9,
    "finance": 0.8,
    "health": 0.7,
    "education": 0.7,
    "project": 0.7,
    "personal": 0.6,
}

PERSONAL_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "on",
    "for",
    "md",
    "new",
    "update",
    "updated",
    "pending",
    "waiting",
    "blocked",
    "done",
    "progress",
    "active",
    "task",
    "todo",
    "file",
    "info",
    "form",
    "call",
    "email",
    "letter",
    "plan",
    "check",
    "setup",
    "with",
    "add",
}

# Historical Dream tail marker ('\u4ee5\u4e0b\u70ba' == Traditional Chinese 'below is')
HISTORY_TAIL_RE = re.compile(r"\s*\u4ee5\u4e0b\u70ba.{0,40}?\u8108\u7d61.*$", re.DOTALL)
MAX_SUMMARY_CHARS = 160
MAX_STATUS_CHARS = 40
TOP_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*:(.*)$")


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        inner = raw[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    if raw in ("", "null", "~"):
        return None
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def _parse_flat_yaml(text: str) -> dict[str, Any]:
    """Minimal fallback YAML parser supporting block scalars, block lists, and flow lists."""
    meta: dict[str, Any] = {}
    list_key: str | None = None
    block_scalar_key: str | None = None
    block_scalar_lines: list[str] = []

    for line in text.splitlines():
        if block_scalar_key is not None:
            if line.startswith(("  ", "\t")) or not line.strip():
                stripped = line[2:] if line.startswith("  ") else line.lstrip("\t")
                block_scalar_lines.append(stripped)
                continue
            meta[block_scalar_key] = "\n".join(block_scalar_lines).strip()
            block_scalar_key = None
            block_scalar_lines = []

        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and list_key is not None:
            meta.setdefault(list_key, []).append(_parse_scalar(line.lstrip()[2:]))
            continue
        if ":" not in line or line[0].isspace():
            continue
        key, _, raw = line.partition(":")
        key, raw = key.strip(), raw.strip()
        list_key = None
        if raw in ("|", ">", "|-", ">-"):
            block_scalar_key = key
            block_scalar_lines = []
        elif raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            meta[key] = [_parse_scalar(x) for x in inner.split(",")] if inner else []
        elif not raw:
            list_key = key
            meta[key] = []
        else:
            meta[key] = _parse_scalar(raw)

    if block_scalar_key is not None:
        meta[block_scalar_key] = "\n".join(block_scalar_lines).strip()
    return meta


def _parse_lenient_yaml(block: str) -> dict[str, Any]:
    """Entry-by-entry fallback for frontmatter containing unquoted colons."""
    entries: list[list[str]] = []
    cur: list[str] = []
    for line in block.splitlines():
        if line and not line[0].isspace() and not line.startswith("- ") and ":" in line and cur:
            entries.append(cur)
            cur = []
        cur.append(line)
    if cur:
        entries.append(cur)
    meta: dict[str, Any] = {}
    for ent in entries:
        text = "\n".join(ent)
        if yaml is not None:
            try:
                parsed = yaml.safe_load(text)
                if isinstance(parsed, dict):
                    meta.update(parsed)
                    continue
            except yaml.YAMLError:
                pass
        flat = _parse_flat_yaml(text)
        if flat:
            meta.update(flat)
            continue
        key, _, first = ent[0].partition(":")
        parts = [first.strip()] + [ln.strip() for ln in ent[1:]]
        meta[key.strip()] = " ".join(p for p in parts if p)
    return meta


def _dump_flat_yaml(meta: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, val in meta.items():
        if isinstance(val, list):
            if not val:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend(f"- {v}" for v in val)
        elif isinstance(val, bool):
            lines.append(f"{key}: {'true' if val else 'false'}")
        elif isinstance(val, str) and "\n" in val:
            lines.append(f"{key}: |")
            for sub in val.splitlines():
                lines.append(f"  {sub}" if sub else "")
        elif isinstance(val, str) and (
            ":" in val or "#" in val or '"' in val or val != val.strip()
        ):
            escaped = val.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
        else:
            lines.append(f"{key}: {val if val is not None else ''}")
    return "\n".join(lines)


def read_frontmatter(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return {}
    try:
        end = content.index("\n---", 3)
    except ValueError:
        return {}
    block = content[4:end]
    if yaml is not None:
        try:
            loaded = yaml.safe_load(block)
            if isinstance(loaded, dict):
                return loaded
            return {}
        except yaml.YAMLError:
            return _parse_lenient_yaml(block)
    return _parse_flat_yaml(block)


def write_frontmatter(path: Path, meta: dict[str, Any]) -> None:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if content.startswith("---\n"):
        try:
            end = content.index("\n---", 3)
            if "id" not in meta and re.search(r"^id\s*:", content[4:end], re.MULTILINE):
                raise ValueError(
                    f"Refusing to overwrite frontmatter of {path}: new meta has no 'id' "
                    "while existing file has 'id:' (upstream parse failure guard)."
                )
            body = content[end + 4 :].lstrip("\n")
        except ValueError as exc:
            if "Refusing to overwrite" in str(exc):
                raise
            body = content.lstrip("\n")
    else:
        body = content.lstrip("\n")
    if yaml is not None:
        yaml_str = yaml.dump(
            meta, default_flow_style=False, allow_unicode=True, sort_keys=False
        ).rstrip()
    else:
        yaml_str = _dump_flat_yaml(meta)
    path.write_text(f"---\n{yaml_str}\n---\n\n{body}", encoding="utf-8")


def _render_field_lines(key: str, val: Any, list_style: str) -> list[str]:
    if isinstance(val, list):
        if list_style == "block":
            if not val:
                return [f"{key}: []"]
            return [f"{key}:", *[f"- {v}" for v in val]]
        return [f"{key}: [{', '.join(str(v) for v in val)}]"]
    if isinstance(val, bool):
        return [f"{key}: {'true' if val else 'false'}"]
    return [f"{key}: {val}"]


def update_frontmatter_fields(
    path: Path,
    updates: dict[str, Any],
    list_style: str = "flow",
) -> None:
    """Surgically update specified top-level frontmatter keys without touching others.

    Consumes the full top-level entry (including indented block scalars or `- item`
    block-list continuations) of any replaced key so neither multiline `note: |`
    fields nor block-list `links:` items are ever orphaned or corrupted.
    """
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return
    try:
        end = content.index("\n---", 3)
    except ValueError:
        return

    raw_lines = content[4:end].splitlines()
    chunks: list[tuple[str | None, list[str]]] = []
    for line in raw_lines:
        if line and not line[0].isspace() and not line.startswith("- "):
            m = TOP_KEY_RE.match(line)
            if m:
                chunks.append((m.group(1), [line]))
                continue
        if chunks:
            chunks[-1][1].append(line)
        else:
            chunks.append((None, [line]))

    seen: set[str] = set()
    out_lines: list[str] = []
    for key, chunk_lines in chunks:
        if key is not None and key in updates:
            if key not in seen:
                out_lines.extend(_render_field_lines(key, updates[key], list_style))
                seen.add(key)
        else:
            out_lines.extend(chunk_lines)

    for key, val in updates.items():
        if key not in seen:
            out_lines.extend(_render_field_lines(key, val, list_style))

    body = content[end + 4 :].lstrip("\n")
    path.write_text("---\n" + "\n".join(out_lines) + f"\n---\n\n{body}", encoding="utf-8")


def clamp_cell(raw: Any, max_chars: int = MAX_SUMMARY_CHARS) -> str:
    """Sanitize and clamp an L2 markdown table cell without touching L3 files."""
    if raw is None:
        return ""
    text = str(raw)
    text = HISTORY_TAIL_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("|", "/")
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1].rstrip()
    return cut + "\u2026"


def clamp_status(raw: Any) -> str:
    if raw is None:
        return ""
    text = str(raw).split(";")[0].strip()
    return clamp_cell(text, max_chars=MAX_STATUS_CHARS)


def extract_first_h1(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    if content.startswith("---\n"):
        try:
            end = content.index("\n---", 3)
            content = content[end + 4 :]
        except ValueError:
            pass
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return path.stem


def to_date(val: Any) -> date:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", val)
        if m:
            try:
                return date.fromisoformat(m.group(1))
            except ValueError:
                return date.today()
    return date.today()


def load_instance_config(agent_root: Path) -> dict[str, Any]:
    cfg_path = agent_root / ".agent-factory" / "instance.json"
    raw_cfg: dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            raw_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw_cfg = {}

    default_style = (
        "personal"
        if (agent_root / "short-term-memory").is_dir()
        and not (agent_root / "storage" / "short_term_memory").is_dir()
        else "code"
    )
    style = str(raw_cfg.get("index_style") or raw_cfg.get("mode") or default_style)

    if style == "personal":
        default_tiers = {
            "stm": "short-term-memory",
            "ltm": "long-term-memory",
        }
        default_half_life = {"stm": 30, "ltm": 180}
    else:
        default_tiers = {
            "stm": "storage/short_term_memory",
            "ltm": "long-term-memory",
            "archive": "archive",
        }
        default_half_life = {"stm": 30, "archive": 180}

    tier_rel = dict(default_tiers)
    if isinstance(raw_cfg.get("tier_dirs"), dict):
        tier_rel.update(raw_cfg["tier_dirs"])
    if isinstance(raw_cfg.get("stm_dir"), str) and raw_cfg["stm_dir"]:
        tier_rel["stm"] = raw_cfg["stm_dir"]
    tier_dirs = {k: agent_root / v for k, v in tier_rel.items()}

    kw = dict(DEFAULT_CODE_KEYWORDS)
    raw_kw = raw_cfg.get("category_keywords") or raw_cfg.get("keywords") or {}
    for k, vals in raw_kw.items():
        if isinstance(vals, list):
            kw[k] = [str(v).lower() for v in vals]

    cat_weights = dict(DEFAULT_PERSONAL_WEIGHTS)
    raw_weights = raw_cfg.get("category_weights") or raw_cfg.get("weights") or {}
    for k, val in raw_weights.items():
        cat_weights[k] = float(val)

    return {
        "index_style": style,
        "tier_dirs": tier_dirs,
        "half_life": {**default_half_life, **(raw_cfg.get("half_life") or {})},
        "category_keywords": kw,
        "category_weights": cat_weights,
        "default_base_weight": float(
            raw_cfg.get("default_base_weight", 0.7 if style == "personal" else 0.9)
        ),
        "min_link_overlap": int(raw_cfg.get("min_link_overlap", 3)),
        "max_outgoing_links": int(raw_cfg.get("max_outgoing_links", 5)),
        "max_keyword_df": float(raw_cfg.get("max_keyword_df", DEFAULT_MAX_KEYWORD_DF)),
        "link_boost_cap": int(raw_cfg.get("link_boost_cap", DEFAULT_LINK_BOOST_CAP)),
        "build_journal_index": bool(raw_cfg.get("build_journal_index", True)),
        "max_journal_index_entries": int(
            raw_cfg.get("max_journal_index_entries", DEFAULT_MAX_JOURNAL_INDEX_ENTRIES)
        ),
        "token_budget": int(raw_cfg.get("token_budget", 2000)),
    }


def list_md_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return [f for f in sorted(directory.glob("*.md")) if not f.name.startswith("_")]


# Backward-compatible module-level exports for `eval.py` and external callers
TIER_DIRS: dict[str, Path] = load_instance_config(DEFAULT_AGENT_ROOT)["tier_dirs"]


def list_tier_files(tier: str, agent_root: Path | None = None) -> list[Path]:
    dirs = (
        load_instance_config(agent_root)["tier_dirs"]
        if agent_root is not None
        else TIER_DIRS
    )
    target = dirs.get(tier)
    if target is None or not target.is_dir():
        return []
    return list_md_files(target)


def score_file(
    path: Path,
    meta: dict[str, Any],
    incoming: int,
    today: date | None = None,
    *,
    tier: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> tuple[float, int]:
    """Compute `(score, days_since_update)` for a single memory file.

    Preserves the exact 4-positional-arg signature `(path, meta, incoming, today)`
    expected by `eval.py`, while supporting `cfg` overrides when called from
    `run_for_agent`.
    """
    today = today or date.today()
    half_life_map = cfg["half_life"] if cfg else {"stm": 30, "archive": 180}
    default_bw = float(cfg["default_base_weight"]) if cfg else 0.9
    boost_cap = int(cfg["link_boost_cap"]) if cfg else DEFAULT_LINK_BOOST_CAP

    base_weight = float(meta.get("base_weight", default_bw))
    effective_tier = str(meta.get("type") or tier or "stm")
    days_since = max((today - to_date(meta.get("updated", today))).days, 0)

    if effective_tier == "ltm" or tier == "ltm":
        recency = LTM_FIXED_RECENCY
    else:
        recency = math.exp(-days_since / float(half_life_map.get(effective_tier, 30)))

    boost = 1.0 + 0.1 * min(max(incoming, 0), boost_cap)
    try:
        urgency = max(1, min(5, int(meta.get("urgency", 1))))
    except (TypeError, ValueError):
        urgency = 1
    urgency_boost = 1.0 + URGENCY_BOOST_PER_LEVEL * (urgency - 1)

    return (
        round((base_weight * recency * boost * urgency_boost) / NORMALIZATION, 4),
        days_since,
    )


def relation_targets(meta: dict[str, Any]) -> set[str]:
    """Extract target IDs from optional hand-curated `relations:` frontmatter mapping."""
    rel = meta.get("relations")
    out: set[str] = set()
    if isinstance(rel, dict):
        for val in rel.values():
            if isinstance(val, list):
                out.update(str(x) for x in val)
            elif val:
                out.add(str(val))
    return out


def _normalize_personal_token(tok: str) -> str:
    tok = tok.lower().strip()
    return re.sub(r"^i(\d{2})$", r"i-\1", tok)


def _extract_personal_keywords(path: Path, meta: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    tags = meta.get("tags", [])
    if isinstance(tags, list):
        tokens.update(str(t) for t in tags)
    if meta.get("category"):
        tokens.add(str(meta["category"]))
    if meta.get("status"):
        tokens.update(str(meta["status"]).split(";")[0].split("-"))
    tokens.update(path.stem.split("-"))
    normalized = {_normalize_personal_token(t) for t in tokens if t}
    return {
        t
        for t in normalized
        if t not in PERSONAL_STOPWORDS and len(t) > 1 and not t.isdigit()
    }


def _extract_code_keywords(
    path: Path, meta: dict[str, Any], category_keywords: dict[str, list[str]]
) -> set[str]:
    content = path.read_text(encoding="utf-8").lower()
    keywords: set[str] = set()
    tags = meta.get("tags", [])
    if isinstance(tags, list):
        keywords.update(str(t).lower() for t in tags)
    for kw in category_keywords.get(str(meta.get("category", "")), []):
        if kw in content:
            keywords.add(kw)
    return keywords


def _auto_link_personal(metas: dict[Path, dict[str, Any]], cfg: dict[str, Any]) -> int:
    files = list(metas)
    name_by_path = {f: f.name for f in files}
    path_by_name = {f.name: f for f in files}
    changed: set[Path] = set()

    def links_of(f: Path) -> list[str]:
        links = metas[f].get("links")
        return [str(x) for x in links] if isinstance(links, list) else []

    for f in files:
        for target_name in links_of(f):
            target = path_by_name.get(target_name)
            if target is None:
                continue
            back = links_of(target)
            if name_by_path[f] not in back:
                metas[target]["links"] = back + [name_by_path[f]]
                changed.add(target)

    kws = {f: _extract_personal_keywords(f, metas[f]) for f in files}
    added = 0
    min_overlap = cfg["min_link_overlap"]
    max_out = cfg["max_outgoing_links"]
    for i, a in enumerate(files):
        for b in files[i + 1 :]:
            if name_by_path[b] in links_of(a) or name_by_path[a] in links_of(b):
                continue
            if len(kws[a] & kws[b]) < min_overlap:
                continue
            if len(links_of(a)) >= max_out or len(links_of(b)) >= max_out:
                continue
            metas[a]["links"] = links_of(a) + [name_by_path[b]]
            metas[b]["links"] = links_of(b) + [name_by_path[a]]
            changed.update((a, b))
            added += 1

    for f in changed:
        update_frontmatter_fields(f, {"links": metas[f].get("links", [])}, list_style="flow")
    return added


def _auto_link_code(
    metas: dict[Path, dict[str, Any]],
    ltm_paths: set[Path],
    cfg: dict[str, Any],
) -> int:
    files = list(metas)
    ids = {f: str(metas[f].get("id", f.stem)) for f in files}
    kws = {
        ids[f]: _extract_code_keywords(f, metas[f], cfg["category_keywords"])
        for f in files
    }
    path_by_id = {ids[f]: f for f in files}

    n = len(kws)
    df: dict[str, int] = {}
    for kw_set in kws.values():
        for k in kw_set:
            df[k] = df.get(k, 0) + 1
    # Ensure small corpora (e.g. <10 files) can still link pairs (min threshold 2)
    max_df_count = max(2, int(cfg["max_keyword_df"] * n))
    allowed = {k for k, c in df.items() if c <= max_df_count}
    filtered = {fid: kw_set & allowed for fid, kw_set in kws.items()}

    computed: dict[str, set[str]] = {fid: set() for fid in kws}
    min_overlap = cfg["min_link_overlap"]
    sorted_ids = sorted(path_by_id)
    for i, a in enumerate(sorted_ids):
        for b in sorted_ids[i + 1 :]:
            if len(filtered[a] & filtered[b]) >= min_overlap:
                computed[a].add(b)
                computed[b].add(a)

    rewritten = 0
    for fid, path in path_by_id.items():
        meta = metas[path]
        old = meta.get("links") if isinstance(meta.get("links"), list) else []
        old_strs = [str(x) for x in old]
        keep = relation_targets(meta)
        if path in ltm_paths or str(meta.get("type", "")) == "ltm":
            keep |= set(old_strs)
        new_links = sorted((computed[fid] | keep) - {fid})
        if sorted(old_strs) != new_links:
            meta["links"] = new_links
            update_frontmatter_fields(path, {"links": new_links}, list_style="block")
            rewritten += 1
    return rewritten


def build_journal_index(
    agent_root: Path,
    style: str,
    today: date,
    write: bool = True,
    max_entries: int = DEFAULT_MAX_JOURNAL_INDEX_ENTRIES,
) -> int:
    journal_dir = agent_root / "journal"
    if not journal_dir.is_dir():
        return 0

    files = list_md_files(journal_dir)
    entries = []
    for f in files:
        meta = read_frontmatter(f)
        m = re.match(r"^(\d{4}-\d{2}-\d{2})-(.+)$", f.stem)
        entry_date = str(meta.get("date") or (m.group(1) if m else today.isoformat()))[:10]
        slug = str(meta.get("slug") or (m.group(2) if m else f.stem))
        raw_topic = (
            meta.get("hook")
            or meta.get("topics")
            or meta.get("summary")
            or meta.get("title")
            or extract_first_h1(f)
        )
        topic = clamp_cell(raw_topic, max_chars=MAX_SUMMARY_CHARS)
        rel_raw = (
            meta.get("related_tasks")
            or meta.get("related_tickets")
            or meta.get("distilled_to")
            or []
        )
        if isinstance(rel_raw, list):
            related = clamp_cell(", ".join(str(x) for x in rel_raw), max_chars=80)
        else:
            related = clamp_cell(rel_raw, max_chars=80)
        distilled_bool = str(meta.get("distilled", "")).lower() == "true"
        entries.append((entry_date, slug, f.name, topic, related, distilled_bool))

    entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    shown_entries = entries[:max_entries] if max_entries > 0 else entries
    omitted_count = len(entries) - len(shown_entries)

    archive_journal_dir = agent_root / "archive" / "journal"
    archived_count = (
        len(list_md_files(archive_journal_dir)) if archive_journal_dir.is_dir() else 0
    )

    if write:
        if style == "personal":
            lines = [
                "---",
                "title: Journal index (L3a - episodic)",
                "purpose: Auto-generated routing table for session journal entries. Load full body only when relevant.",
                f"updated: {today.isoformat()}",
                f"active_entries: {len(entries)}",
                f"shown_entries: {len(shown_entries)}",
                f"archived_entries: {archived_count}",
                "---",
                "",
                "# Journal Index",
                "",
                "> Auto-generated by `memory_scoring.py` from `journal/*.md` frontmatter. Do not hand-edit.",
                f"> Active: {len(entries)} (showing newest {len(shown_entries)}) | Archived (`archive/journal/`): {archived_count}",
                "",
                "| Date | Slug | Topics | Related STM/LTM | Distilled |",
                "|---|---|---|---|---|",
            ]
            for entry_date, slug, fname, topic, related, distilled_bool in shown_entries:
                lines.append(
                    f"| {entry_date} | [{slug}]({fname}) | {topic} | "
                    f"{related or '-'} | {'true' if distilled_bool else 'false'} |"
                )
        else:
            lines = [
                "# Journal Index",
                "",
                "> Auto-generated by `memory_scoring.py`. Scan newest-first by title.",
                f"> Last updated: {today.isoformat()} | Active: {len(entries)} (showing newest {len(shown_entries)}) | Archived: {archived_count}",
                "",
                "| Date | Slug | One-line | Distilled |",
                "|------|------|----------|-----------|",
            ]
            for entry_date, slug, fname, topic, _related, distilled_bool in shown_entries:
                mark = "true" if distilled_bool else "false"
                lines.append(f"| {entry_date} | [{slug}]({fname}) | {topic} | {mark} |")

        if omitted_count > 0:
            lines.extend(
                [
                    "",
                    f"> ... (+{omitted_count} older entries in `journal/`; archive distilled entries >30d via dream)",
                ]
            )
        lines.append("")
        (journal_dir / "_index.md").write_text("\n".join(lines), encoding="utf-8")

    return len(entries)


def run_for_agent(agent_root: Path, write: bool = True) -> dict[str, Any]:
    cfg = load_instance_config(agent_root)
    style = cfg["index_style"]
    tier_dirs: dict[str, Path] = cfg["tier_dirs"]
    today = date.today()

    tier_files = {t: list_md_files(d) for t, d in tier_dirs.items()}
    metas = {f: read_frontmatter(f) for files in tier_files.values() for f in files}

    linkable_tiers = [t for t in ("stm", "ltm") if t in tier_files]
    linkable_metas = {f: metas[f] for t in linkable_tiers for f in tier_files[t]}
    ltm_paths = set(tier_files.get("ltm", []))

    if write and linkable_metas:
        if style == "personal":
            _auto_link_personal(linkable_metas, cfg)
        else:
            _auto_link_code(linkable_metas, ltm_paths, cfg)

    incoming: dict[str, int] = {}
    if style == "code":
        # Only active STM links vote in code mode so archived/accumulated LTM links
        # do not permanently inflate completed tickets above the archive threshold.
        for f in tier_files.get("stm", []):
            links = metas[f].get("links", [])
            if isinstance(links, list):
                for link in links:
                    incoming[str(link)] = incoming.get(str(link), 0) + 1
    else:
        for f, meta in metas.items():
            links = meta.get("links", [])
            if isinstance(links, list):
                for link in links:
                    incoming[str(link)] = incoming.get(str(link), 0) + 1

    stats: dict[str, Any] = {}
    for tier, files in tier_files.items():
        scored = []
        for f in files:
            meta = metas[f]
            key_id = f.name if style == "personal" else str(meta.get("id", f.stem))
            try:
                stored_score = float(meta.get("score", 0.0) or 0.0)
            except (TypeError, ValueError):
                stored_score = 0.0

            if str(meta.get("score_frozen", "")).lower() == "true":
                scored.append((stored_score, key_id, meta))
                continue

            if style == "personal":
                days = max((today - to_date(meta.get("updated", today))).days, 0)
                bw = cfg["category_weights"].get(
                    str(meta.get("category", "")), cfg["default_base_weight"]
                )
                recency = math.exp(-days / float(cfg["half_life"].get(tier, 30)))
                boost = 1.0 + 0.1 * min(incoming.get(key_id, 0), cfg["link_boost_cap"])
                score = round((bw * recency * boost) / NORMALIZATION, 2)
                if write and abs(stored_score - score) > 0.001:
                    update_frontmatter_fields(f, {"score": f"{score:.2f}"}, list_style="flow")
            else:
                score, _ = score_file(
                    f, meta, incoming.get(key_id, 0), today, tier=tier, cfg=cfg
                )
                if write and abs(stored_score - score) > 0.0001:
                    meta["score"] = score
                    update_frontmatter_fields(f, {"score": score}, list_style="block")

            scored.append((score, key_id, meta))

        scored.sort(key=lambda row: row[0], reverse=True)

        if write and tier_dirs[tier].is_dir():
            one_line_for = lambda m: clamp_cell(
                m.get("hook") or m.get("summary") or m.get("title") or ""
            )
            if style == "personal":
                lines = [
                    "---",
                    f"updated: {today.isoformat()}",
                    f"token_budget: {cfg['token_budget']}",
                    "---",
                    "",
                ]
                if tier == "stm":
                    lines += [
                        "| File | Score | Category | Deadline | Status | One-line |",
                        "|------|-------|----------|----------|--------|----------|",
                    ]
                    for sc, name, meta in scored:
                        lines.append(
                            f"| {name} | {sc:.2f} | {clamp_cell(meta.get('category', ''), 24)} | "
                            f"{clamp_cell(meta.get('deadline', '-'), 24)} | "
                            f"{clamp_status(meta.get('status', ''))} | {one_line_for(meta)} |"
                        )
                else:
                    lines += [
                        "| File | Score | Category | One-line |",
                        "|------|-------|----------|----------|",
                    ]
                    for sc, name, meta in scored:
                        lines.append(
                            f"| {name} | {sc:.2f} | {clamp_cell(meta.get('category', ''), 24)} | "
                            f"{one_line_for(meta)} |"
                        )
            else:
                lines = [
                    f"# {TIER_NAMES.get(tier, tier.upper())} Index",
                    "",
                    "> Auto-generated by scoring engine. Do not edit manually.",
                    f"> Last updated: {today.isoformat()}",
                    "",
                ]
                if tier == "archive":
                    lines += [
                        "| Score | ID | Summary | Original Tier | Archived |",
                        "|-------|-----|---------|---------------|----------|",
                    ]
                    for sc, fid, meta in scored:
                        lines.append(
                            f"| {sc:.2f} | {fid} | {one_line_for(meta)} | "
                            f"{clamp_cell(meta.get('original_tier', 'stm'), 16)} | "
                            f"{clamp_cell(meta.get('updated', ''), 16)} |"
                        )
                else:
                    lines += [
                        "| Score | ID | Summary | Tags | Status | Category | Updated |",
                        "|-------|-----|---------|------|--------|----------|---------|",
                    ]
                    for sc, fid, meta in scored:
                        tags = meta.get("tags", [])
                        tag_str = (
                            " ".join(str(t) for t in tags[:8])
                            if isinstance(tags, list)
                            else str(tags or "")
                        )
                        lines.append(
                            f"| {sc:.2f} | {fid} | {one_line_for(meta)} | "
                            f"{clamp_cell(tag_str, 60)} | "
                            f"{clamp_status(meta.get('status', 'active'))} | "
                            f"{clamp_cell(meta.get('category', 'technical'), 24)} | "
                            f"{clamp_cell(meta.get('updated', ''), 16)} |"
                        )
            lines.append("")
            (tier_dirs[tier] / "_index.md").write_text("\n".join(lines), encoding="utf-8")

        scores = [s[0] for s in scored]
        stats[tier] = {
            "count": len(scores),
            "highest": max(scores) if scores else 0.0,
            "lowest": min(scores) if scores else 0.0,
            "median": sorted(scores)[len(scores) // 2] if scores else 0.0,
        }

    if cfg["build_journal_index"]:
        j_count = build_journal_index(
            agent_root,
            style,
            today,
            write=write,
            max_entries=cfg["max_journal_index_entries"],
        )
        if (agent_root / "journal").is_dir():
            stats["journal"] = {
                "count": j_count,
                "highest": 0.0,
                "lowest": 0.0,
                "median": 0.0,
            }

    return stats


def rescore_and_reindex(write: bool = True) -> dict[str, Any]:
    return run_for_agent(DEFAULT_AGENT_ROOT, write=write)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_AGENT_ROOT,
        help="target agent root directory (defaults to PAF_AGENT_ROOT or parent of scripts/)",
    )
    parser.add_argument("--stats", action="store_true", help="print stats only, no writes")
    args = parser.parse_args(argv)

    target_root = args.dir.expanduser().resolve()
    stats = run_for_agent(target_root, write=not args.stats)
    if not args.stats:
        print(f"Scores and indexes rebuilt for {target_root}")
    for tier, s in stats.items():
        if tier == "journal":
            print(f"  journal: count={s['count']}")
        else:
            print(
                f"  {tier}: count={s['count']} highest={s['highest']:.2f} "
                f"lowest={s['lowest']:.2f} median={s['median']:.2f}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
