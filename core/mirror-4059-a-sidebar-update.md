# Update-paused turns remain logically live

Mirror ordinals: 3763.

## Behavior

- Sidebar session activity keeps `turn_live` for core-update-paused turns that may still auto-continue, even though the turn lease is temporarily absent.
- Stale, exhausted, disabled or non-update pause markers do not keep a row working forever.

## How it works

`turn_session_activity` unions ordinary active leases with two additional sets. Gateway started admissions join their update-checkpoint metadata and last checkpointed message timestamp, with a one-hour freshness bound. Desktop/TUI interrupted-turn markers require `core_update_pause`, auto-continue enabled, a recent maximum of started/parked/paused times, and fewer than two attempts; their freshness bound is fifteen minutes. Collection reads markers per discovered profile home and tolerates missing/older database tables.

## Code

- `hermes_cli/turn_session_activity.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_turn_session_activity.py` — Real scratch state databases assert gateway paused and leased rows annotate live while unrelated rows do not. Negative gateway checkpoint/admission/freshness cases, positive Desktop markers, disabled/stale/exhausted/non-update markers, and a profile-home marker are covered.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

This changes activity annotation, not the stored outcome, lease ownership or actual continuation scheduling. Fixed freshness limits mirror defaults; the test does not establish arbitrary custom auto-continue settings or actual sidebar rendering.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.
