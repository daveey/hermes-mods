# Cron: a quota hold is released once its refusing pool serves again

Patches: **1907** (`642f5ca456`, `[daveey] fix(cron): release a quota hold once its refusing pool serves again`), **1908** (`592ad3fe5d`, `[daveey] test(cron): cover fallback-chain stamp, tz-pinned release, queued run-now and a real credential pool for the quota-hold early release`), **1909** (`c52b48a6e5`, `[daveey] test(cron): build extra_fire.reschedule state in the queued run-now quota-hold test`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A quota hold stamps the refusing route (`quota_hold_route`); the due scan releases the hold early once that route's local pool would serve again, instead of skipping every slot until the refusal's `resets_at` upper bound (1907).
- Interval holds and holds with a queued run-now intentionally stay one-way (1908/1909).

## How it works

`cron/quota_hold.py` (`release_if_serving`) checks the pool; `cron/jobs.py` and `cron/scheduler.py` call it when evaluating due jobs.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1907: x-reply-discovery (hub ac8b62d38a0b) was parked until 2026-11-04T15:07Z by the 11-02 22:07Z SubscriptionQuotaUnavailable openai-codex/gpt-6-astra refusal, while the same pool served sibling cron sessions (bsky-reply-discovery) all of 11-03; ~26 in-window slots were skipped.
- 1908: Review of 7e5337c70db (exact-head, Opus): logic correct; asked for these four cases. Interval holds and holds with a queued run-now intentionally stay one-way (an errored interval job would be fired at once by the stale-error re-arm; a run-now waits for the boundary via extra_fire.reschedule): both keep pre-fix behaviour.
- 1909: Exact-head review of 819a5d07 (Opus, session local-031c8b4e…): the old test passed with both run-now guards removed. It now builds manual_run_at == next_run_at == parked with the restore slot saved and asserts hold, next_run_at and queued all survive; verified it fails with both guards (quota_hold.release_if_serving + jobs._evaluate_due_job) removed.

## Code

`agent/model_routing_unavailable.py`, `cron/jobs.py`, `cron/quota_hold.py`, `cron/scheduler.py`, `cron/scheduler_bookkeeping.py`, `gateway/session_cron.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/cron/test_quota_hold_early_release.py`: `test_pool_serving_releases_hold_to_next_natural_slot_without_firing`, `test_pool_still_exhausted_keeps_hold`, `test_pool_read_fault_keeps_hold`, `test_legacy_hold_without_route_is_unchanged`, `test_pre_stamp_hold_from_pool_refusal_releases`, `test_no_earlier_natural_slot_keeps_hold`, `test_interval_hold_stays_one_way`, `test_mark_job_run_stamps_route_and_model_reach_clears_it`, `test_route_from_pool_refusal_and_fallback_chain`, `test_scheduler_tick_on_pool_refusal_stamps_route`, `test_fallback_chain_stamps_terminal_route`, `test_timezone_pinned_job_releases_to_its_own_zones_next_slot` (+2 more)

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
