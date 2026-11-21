# Kanban links projection: skip impossible alias scans and reuse key derivation

Mirror snapshot 5406; ordinals 5361, 5393.

Entries:

- **5361**: Skip impossible qualified-card legacy alias scans
- **5393**: perf(kanban): reuse exact publication membership derivation

## Behavior

- **5361**: the legacy alias scan is skipped for qualified cards, because `supersedes` rejects a non-None profile before reading any receipt; only that first rejection moves ahead of the scan.
- **5393**: the publication-membership derivation (one bounded immutable derivation of the ordered key inventory) is reused while every key is re-read before reuse.
- Producer-reported measurements were diagnostic, not live acceptance.

## How it works

- `kanban_session_activity.py`, `kanban_origin_ledger.py`, `kanban_publication_index.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_origin_ledger.py`
- `hermes_cli/kanban_publication_index.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_kanban_links_alias_work.py`
- `tests/hermes_cli/test_kanban_publication_index.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Origin-key lookup order is kept for malformed-origin failure ordering; no clocks, streams or deadlines are memoised.

## Why core instead of a plugin

The projection runs inside core kanban reads.
