# SessionDB schema memo keeps retrying failed repairs

Patches: **3616–3617**.

## Behavior
- A NULL `active` value written by an older-build writer after the first init is healed by the throttled per-store heal on memo hits, not only on the first open per process.
- A store whose unique title index is missing (failed duplicate-title repair) is not memoized, so the next open reruns the init.
- A failed delegate-marker heal rolls back together with its trigram backfill, logs a warning, and is retried by a later open.
- A persistently failing heal backs off 30 s instead of retrying on every open, and warns once per failure streak (debug afterwards). A full init whose heal failed overrides an earlier memo key's interval claim.

## How it works
- 3616 extracts `_heal_null_active_rows`, runs it from the full init and `_run_throttled_heals`, makes `record()` refuse stores lacking `idx_sessions_title_unique`, drops the per-process code digest from the memo key, and wraps the marker UPDATE and trigram backfill in one SAVEPOINT; `_maybe_heal` rewinds its interval claim on failure.
- 3617 adds `HEAL_RETRY_S` (30 s) backoff, once-per-streak warnings, the full-init override of earlier claims, and a fallback message that no longer says "rolled back".

## Code
- `hermes_state_schema.py`
- `hermes_state_schema_memo.py`
- `tests/hermes_state/test_schema_init_memo_heals.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_state/test_schema_init_memo_heals.py`: `test_a_null_active_row_written_after_the_first_init_is_healed_by_the_throttled_heal`, `test_a_failed_unique_title_repair_is_retried_on_the_next_open`, `test_a_heal_that_fails_mid_way_rolls_back_and_is_retried_by_the_next_open`, `test_a_persistently_failing_heal_backs_off_and_warns_once`, `test_a_full_init_heal_failure_overrides_an_earlier_keys_interval_claim`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Removing the code digest from the memo key relies on the memo being per-process; see the module docstring.
