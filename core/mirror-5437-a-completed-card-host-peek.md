# Display annotation: recover fresh completed-card snapshots during bounded refresh

Mirror entries: 5419, 5420.

## Behavior

- A display read of the Kanban annotation no longer keeps showing a stale "active" card when the refresh lock is occupied and a newer completed copy exists on the host.
- The newer completed copy is restored with its own observation timestamp, so the row carries the real observation age.
- Expired display evidence (older than the 180-second observation horizon) is shown as unknown, never as a synthetic "done" or empty state.
- Strict (non-display) closeout reads are unchanged: they ignore the display horizon and the host peek.
- A restart seed loaded from the disk cache that is newer than or equal to the host copy is preserved rather than overwritten.

## How it works

The display-copy read was split into a side-effect-free validator that reads and checks an immutable host copy without running annotation sources or touching the annotation cache. The existing lock-held adoption path still uses it and then reruns undeclared sources.

A new read-only `peek_display` runs after the bounded display snapshot returns. It takes the previous parts and the validated host read, and returns the previous parts unless the host copy is complete and strictly newer. When it replaces them, it returns an `ObservedParts` tuple that carries its own `observed_at`, independent of the per-scope read registry, so a later concurrent publication cannot change its date, and the peek never publishes to the cache.

Entry 5420 fixes the age comparison: a restart seed keeps its provenance in the disk cache, not in the bounded-read registry, so the peek consults the disk observation time first and falls back to the registry. Newer-or-equal seeds win; an older seed loses to the host copy. Single-flight ownership of the annotation refresh is preserved: the peek never invokes waiting-on sources outside the refresh lock.

Related context: [annotation prewarm](annotation-prewarm.md) and [annotation content revision](kanban-annotation-content-revision.md).

## Code

- `hermes_cli/kanban_annotation_host.py` (`_display_read`, `adopt_display`, `peek_display`)
- `hermes_cli/kanban_annotation_refresh.py` (`ObservedParts`, `read_at`, `unavailable`)
- `hermes_cli/kanban_session_activity.py` (`_annotation_snapshot` wrapper, expiry check in `annotate_kanban_activity`)

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

The source tests cover: a busy-lock display read adopting a completed host copy; a real published completion beating a stale local positive; expiry marking unknown for display reads while fresh and strict controls are unaffected; peek preserving a newer previous copy and refusing undeclared waiting sources; peek timestamps surviving later scope publication; no overwrite of a concurrently published local read; and, for 5420, older/equal/newer restart-seed controls through the real disk seed path.

Producer-reported, not executed by the mirror: 518 passed/1 skipped (original consumers), 241 passed/1 skipped (provenance-corrected), 19 final focused, 44 independent focused. No exact-source idle-green claim is made.

Source test paths:

- `tests/hermes_cli/test_kanban_annotation_display_recovery.py`
- `tests/hermes_cli/test_kanban_annotation_refresh_bounded.py`

## Applying

Apply these entries only as part of the **whole ordered 5437 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches in isolation.

## Gotchas

- The peek only restores copies the validator accepts as complete; an incomplete or unreadable host copy leaves the previous snapshot in place.
- Equal timestamps favor the previous (local or seed) copy.
- The 180-second horizon applies to display reads only; do not use display output for strict closeout decisions.

## Why core instead of a plugin

The annotation cache, refresh lock and host-copy validation are private to core's session-activity module; a plugin cannot take part in their single-flight ownership or timestamp contracts.
