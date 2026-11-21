# Kanban overlap gate: owner/repo-aware PR references

Mirror snapshot 5406; ordinals 5400, 5401.

Entries:

- **5400**: fix(kanban): the overlap gate tells owner/repo#N PRs apart
- **5401**: fix(kanban): o/r.git#N is o/r#N; linear-time pr ref match

## Behavior

- **5400**: refs are `(repo or None, N)`; `owner/repo#N` and `/pull/N` URLs carry repo; bare forms match any repo.
- **5401**: `.git` suffix dropped; regex made linear-time.

## How it works

- `kanban_overlap.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_overlap.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/tools/test_kanban_create_overlap_pr_ids.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Bare refs still match broadly.

## Why core instead of a plugin

Duplicate gating is core.
