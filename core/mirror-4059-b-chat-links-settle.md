# Settled chat-link parity across asynchronous stores

Patches: 3809, 3810, 3813.

## Behavior

`kanban sync links-diff` distinguishes transient propagation lag (`in_flight`) from persistent ledger/replica differences. Unreadable boards cannot silently erase or confirm a difference.

## How it works

The CLI defaults to a ninety-second settle window; `--settle 0` retains the raw single-snapshot path, while direct `links_diff` callers still default to zero. Poll cadence is twice the configured sync interval, at least five seconds. The report returns last-read counts plus persistent differences, transient entries and settle diagnostics, stopping early when nothing persists and all replicas are readable.

The follow-up replaces a plain set intersection with board-aware read history: a persistent entry is present in every sample able to read its board and has at least one such sample. Last-read unreadability always keeps the result unclean. An additional owner-on-board test ensures the second replica query cannot substitute another gateway's or another profile's carried card for this gateway's actual missing obligation.

## Code

- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_chat_links_settle.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_sync_cli.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_kanban_chat_links_settle.py` (patches 3809, 3810).
- `tests/hermes_cli/test_kanban_chat_links_profile_board.py` (patch 3813).

- **3809:** Settling tests require lag to become in_flight, persistent differences to remain unclean, early clean exit, later/reappearing entries, both diff directions, bounded final sleep, configured poll cadence and CLI raw/default behavior.
- **3810:** Unreadable samples neither clear nor confirm their board entries; last-read unreadability prevents clean; a difference first visible after unreadability is persistent until a readable absence clears it.
- **3813:** An owner’s only other carried card cannot hide this gateway’s missing ledger obligation, whether that other card belongs to a different gateway or profile; profile-board counts stay empty.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

`clean` describes the observed settle window, not a transactional global snapshot. Counts remain raw totals from the last read and need not equal only the persistent-diff counts. A difference first seen after earlier unreadable samples can still be persistent; unreadability is not a negative observation.

## Plugin?

No. This is a core parity reader and CLI report over the origin ledger and Board replicas.
