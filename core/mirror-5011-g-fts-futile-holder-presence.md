# Session-search FTS: futility keyed on holder presence

Mirror entries 5007 and 5008.

## Behavior

A stale full-text index deferred its online rebuild thousands of times while foreign holders were present at every deferral, yet never became "futile", because the recorded PID set changed constantly (short-lived CLI holders; each process excludes itself) and any change restarted the window. Automatic recovery never ran.

Now the futility window measures **continuous presence of any foreign holder**. It restarts only when (a) the last deferral saw no holder, (b) the clock went backwards (negative gap), or (c) no deferral was recorded for longer than twice the retry backoff cap (a new episode). Log and doctor text name the current holders rather than "the same ones". 5008 documents all three resets and tests the clock-back and empty-holder branches.

## How it works

`hermes_state_schema.py` updates the deferral record: consecutive attempts accumulate across changing PIDs; `_fts_deferral_futile` is true once `_FTS_HOLDER_FUTILE_ATTEMPTS` deferrals span `_FTS_HOLDER_FUTILE_SECONDS`. `hermes_state_fts_online.py` and `doctor_state.py` use the new wording.

## Code

`hermes_cli/doctor_state.py`, `hermes_state_fts_online.py`, `hermes_state_schema.py`.

## Tests

Source tests (not executed here): `tests/hermes_state/test_fts_futile_holder_churn.py` (`test_holders_present_at_every_deferral_turn_futile_even_when_the_pids_change`, `test_a_quiet_gap_longer_than_the_retry_cap_starts_a_new_window`, plus reset-branch cases), `test_fts_runtime_rebuild.py`.

## Applying

Follow the full segmented recipe in the core mods [README](README.md) ("Applying"); never apply on its own.

## Gotchas

- "Futile" triggers the forced offline-style recovery, so a wrongly long window delays repair and a wrongly short one disturbs live holders.
- The record wording changed; log-scraping tests must match the new text.
