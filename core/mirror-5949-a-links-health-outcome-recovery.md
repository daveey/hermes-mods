# Links-health transitions recompute unknown outcomes

Patch coverage: **5926**, **5927**, **5928**.

## Behavior

When the remote linked-card source fails or recovers, stored session outcomes are recomputed even if no card changed status. A session marked `source_unknown` returns to a real verdict after the source recovers, and a failure marks affected outcomes unknown, without an unrelated task event. Stored unknown outcomes with no remote card rows at all are covered too. Only sessions in the source's own home are touched; a same-named chat id in another profile is left unchanged. A failed recovery keeps its pending generation and retries; a newer transition during recovery supersedes the old one.

## How it works

`fact_links_health` is a memory-only registry keyed by origin scope: `changed(origin, available)` bumps a generation only when availability flips, records it as pending and calls watcher wake callbacks (event-set only, no I/O). `RemoteLinksSource` calls it after a committed publication and after a failure. The Kanban notification schedule's `BoundActions` owns a `HealthWatch` scoped to bound homes, so a health change wakes the existing executor and respects the retry floor instead of adding polling. In `notice_fanout._remote_change_pass` a pending generation disables the quiet-memo shortcut and the unchanged-snapshot skip; `notice_links_health.recover_into` then opens the origin home's own state store (no profile fallback), refreshes outcomes whose stored envelope carries an unknown-source marker, and emits fanout entries for them. The generation is acknowledged only when every refresh succeeded and the generation is still the one that was read. 5927 adds the no-card-rows and exact-store recovery; 5928 moves the recovery step into `recover_into`.

## Code

- `hermes_cli/fact_links_health.py`: generations, pending, acknowledge, `HealthWatch`.
- `hermes_cli/fact_remote_links.py`: publish hooks on commit and failure.
- `gateway/kanban_notification_schedule.py`: watch wired into `due()`, closed in `close()`.
- `tui_gateway/notice_fanout.py`: health-aware remote change pass, optional origin store opener.
- `tui_gateway/notice_links_health.py`: exact-store recovery (`recover`, `recover_into`).

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

- `tests/hermes_cli/test_fact_links_health.py`: wake only after an actual commit, no wake on unchanged health, owner isolation and same-generation acknowledgement.
- `tests/gateway/test_kanban_notification_schedule.py`: health wakes the existing executor with no file/database I/O in the callback, honouring the refusal floor.
- `tests/hermes_state/test_session_status_refresh.py`: unchanged remote card recomputed on health flips, own live turn independent of unknown remote cards, recovery with no remote rows, foreign profile untouched, failed recovery retains and supersedes generations.

Untested entry path: the real gateway executor thread wiring; tests call the change pass and schedule directly.

## Applying

Apply the covered patches only as part of the full ordered core series, following the whole segmented recipe in the [core README Applying section](README.md#applying) (replay bridges and required repair overlays included). The mirror is append-only and these entries depend on earlier ones; do not run a standalone `git am` on them and do not treat them as an independent cherry-pick set. A reimplementation on a newer upstream should keep the invariants above and run the named suites in a disposable source checkout.

## Gotchas

- Publication callbacks must stay I/O-free; recovery runs only in the normal executor.
- Do not fall back to the active profile's store: equal chat ids in other profiles would be aliased.
- Acknowledge only after all refreshes succeed, and only the generation that was read.
- Depends on [the unknown linked-card state](mirror-5949-a-unknown-linked-card-status.md).

## Why not a plugin

No: it sits between core fact publication, the gateway notification schedule and the outcome store, none of which expose plugin hooks.
