# Proposal: an empty read must never be able to reach a writer

**Status**: inbox (unreviewed). Fails the cross-domain bar: two of the three cases are the
same repo's memory tooling (the LIS agent's own scripts); the third is product code that an
existing lesson (#82, shadow comparison) already covers from another angle.

## Lesson (candidate text)

When a reader can fail, "nothing found" and "could not read" must be two different values,
and any writer fed by that reader must refuse the second one. A reader that answers a parse
error with an empty result turns every downstream update into a destructive overwrite: the
writer sees "empty", adds its one field, and saves; the file now contains only that field.

Discipline: a fallible read returns a distinct failure (exception, `None`, a tagged state),
never the empty collection; a writer that receives a value derived from a read checks for a
field it knows must exist (an id, a schema version) before replacing anything.

## Evidence

- 2026-10-08 (LIS agent memory scripts): `read_frontmatter` returned `{}` on a YAML parse
  error. `reconcile-jira.py --apply` added `jira_status` to that `{}` and wrote it back; two
  STM files were left with a one-line frontmatter. The nightly scoring pass would have done
  the same to a third. The trigger was ordinary: a plain scalar with `: ` inside `summary:`.
- 2026-08-15 (same scripts): the flat fallback parser read `tags:` as an empty string, so
  every rewrite emitted `tags:` with no items; the tags of every file it touched were
  destroyed silently.
- 2026-09 (LIS trans v1, VP-18483): a request helper swallowed every failure into
  `{ transbadstatus: 500 }`; the caller evaluated a boolean on it and reported a failed lookup
  as a definite "no report". Same shape (failure rendered as a legitimate empty answer) on the
  read-only side; covered by lesson #82.

## Why it is not a PR yet

Two of three cases are the same subsystem; the third is already a lesson. If a case from a
different domain shows up (a config loader, a sync job, a migration tool), promote with all
three as evidence.

## Example (de-identified)

```
meta = read(path)            # returns {} when the file cannot be parsed
meta["status"] = "done"
write(path, meta)            # the file now has one line
```

Fix shape: `read` raises or returns a tagged failure; `write` refuses when `meta` lacks the
identity field the file on disk has.
