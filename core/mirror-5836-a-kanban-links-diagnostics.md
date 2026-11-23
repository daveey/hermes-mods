# Kanban links check diagnostics by reason and source (mirror 5502–5504)

## Behavior
- Passive, bounded counters attribute `sync.links` checks by board reason/component, strict source work vs background builds, with per-board inclusive CPU, separate fingerprint/projection/ledger slicing CPU and start/end in-flight brackets.
- Per the source doc and module docstring there is no cache-policy, TTL or failure/retry change; the diff does restructure `_links_entry` (the strict path now takes the fingerprint before the lock and re-checks inside it, and a warm display read still precedes fingerprint and lock work) and adds a `degraded` flag in `ChatReads` for reasons only. At most 16 board slugs of at most 80 characters keep their names; others fold into `other`. `links_strict` includes strict callers other than sync.

## How it works
`hermes_cli/kanban_links_diagnostics.py` holds the counters; `kanban_links_chat_key.py`, `kanban_session_activity.py` and `kanban_sync_client.py` report into it. Doc in source: `docs/kanban-links-diagnostics.md`.

## Code
`hermes_cli/kanban_links_diagnostics.py`, `kanban_links_chat_key.py`, `kanban_session_activity.py`, `kanban_sync_client.py`.

## Tests
Source: `tests/hermes_cli/test_kanban_links_reason_counters.py` (real SQLite same-profile membership insertion, required-read failure/age retry, one-read pass reuse, concurrent strict/background ownership, cold and combined-expiry attribution, background CPU lane, stale non-probe display, board-name bound), `test_kanban_links_reason_integration.py` (same-profile insertion refreshes real membership and publishes; a required-read error stays uncertified and retries by the original age). 5504 touches only the counters test file and adds no named function. Not executed in this mirror pass.

## Applying
Apply in order.

## Gotchas
Diagnostic only. The source doc describes a measurement protocol (a complete window of at least 900 s, gateway uptime of at least 3000 s, stable PID/birth/SHA) but no measurement was made or is claimed in these patches.

## plugin?
No.
