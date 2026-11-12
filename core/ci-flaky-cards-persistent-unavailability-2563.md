# ci-flaky-cards: only persistent unavailability turns the cron red

Patches: **2537**.

## Behavior

The `ci-flaky-cards` poller (`scripts/ci/flaky_kanban.py --from-github`) exited 1 on every poll while any recent run had unreadable flaky metadata, which kept the cron red for hours on harmless cases. Now:

- A `cancelled` run whose test jobs are incomplete is `not_applicable` when a newer run of the same workflow and branch exists (any status), i.e. a concurrency cancel. If the lookup fails, or the run lacks a workflow id or branch, it stays unavailable.
- A job copied into a "Re-run failed jobs" attempt (same name, `started_at` and `completed_at` as an earlier attempt's job) is read from the earlier attempt's artifact and job id. A slice that really re-ran still needs its own attempt's artifact.
- A `missing` artifact on a run completed within the grace window (default 15 minutes) logs `RETRY run=<id>/<attempt> code=missing age=<s>s` and is retried next poll.
- Other unreadable data logs `WAITING run=… code=… polls=<n>`. It becomes `FAILED run=… polls=<n>` (exit 1) only when unavailable in two consecutive polls and completed at least the grace window ago.
- A card-filing failure and a failed run listing (`FAILED runs_unavailable`) are still `FAILED` at once.
- New `--grace-minutes` option (default 15).

## How it works

`scripts/ci/flaky_artifact_poll.py` gains `superseded()` (one read of `/actions/workflows/<id>/runs?branch=…`; unreadable means not superseded) and `_carried_over()`, which remaps carried-over slice jobs to the attempt that ran them for attempts above 1. `_poll_run` now also returns the unavailability code. `poll_github` in `scripts/ci/flaky_kanban.py` keeps `state["poll_seq"]` and `state["unavailable"]` (attempt → last poll sequence and consecutive count, pruned to attempts still listed and unprocessed, and capped), and `_unavailable_line` picks RETRY, WAITING or FAILED. `main` still exits 1 only on a line starting with `FAILED`. Related: [flaky-metadata-artifacts-2409a.md](flaky-metadata-artifacts-2409a.md), [kanban-overlap-url-slug-and-flaky-cards.md](kanban-overlap-url-slug-and-flaky-cards.md).

## Code

`scripts/ci/flaky_artifact_poll.py`, `scripts/ci/flaky_kanban.py`, `docs/plans/flaky-artifact-metadata-design.md` (design amendment).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests):

- `tests/ci/test_flaky_exit_policy.py`: `poll_github` and the `flaky_kanban.main` CLI entry (`--from-github`, fake GitHub and board) for supersession, grace-window retry, consecutive-poll escalation, bounded state and carried-over slices.
- `tests/ci/test_flaky_artifact_poll.py`, `tests/ci/test_flaky_protocol_boundaries.py`: updated receipts (`WAITING` first, `FAILED … polls=2` second) via `poll_github` and `main`.
- `tests/ci/flaky_artifact_fakes.py`: fake per-workflow run listing.

Live GitHub polling and the actual cron are untested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Receipt wording changed: consumers matching `FAILED run=…` for the first unreadable poll now see `WAITING`.
- Persisted poller state gains `poll_seq` and `unavailable`; older state files load without them.
- A genuinely broken artifact is reported at least one poll later than before.

## Plugin

Core change; no plugin required.
