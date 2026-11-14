# Successor-aware durable notice retry deadlines

Mirror batch 6; ordinals 4039, 4040.

## Behavior

A known different-code successor may re-evaluate a long failed notice wake at its durable short deadline instead of waiting for the predecessor’s reset wall. Failed speculative checks preserve the reset-time attempt budget.

## How it works

Long error deferrals store frozen host-loop code identity and a fixed short deadline. Read-only discovery shortens only for known different code and valid finite timestamps; legacy/same/unknown provenance retains the original wall. Existing compare-and-swap owns the wake. The handler determines whether the due check is before the original wall; only an error on that early recheck refunds its increment, then rebooks with current code. Busy backoff and normal reset-time accounting remain separate.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/tui_gateway/test_notice_retry_successor.py`
- `tui_gateway/notice_guarantee.py`
- `tui_gateway/notice_retry.py`

Mail entries in the ordered series:

- 4039: [`e7e64cf70e40de16c3f8d7e678d94b5a4c252c42`](patches/4039-daveey-let-successor-code-re-evaluate-durable-wall-retries.patch)
- 4040: [`5de279d002abb9d481fa244699d69d2eb04b5edc`](patches/4040-fix-notices-preserve-reset-budget-across-successor-rechecks-daveey.patch)

## Tests

`tests/tui_gateway/test_notice_retry_successor.py`: successor discovery works after process-local replay reset; same/unknown/legacy/malformed provenance retains the wall, busy backoff is not shortened, booking never extends due and stale CAS cannot claim. Repeated different builds fail early without consuming reset attempts; at the actual wall the normal budget increments and exhausts as before.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

No migration or new replay budget is added. A process restart on the same build is not a successor-code exception; speculative failures do not prove the underlying quota/reset cause fixed.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
