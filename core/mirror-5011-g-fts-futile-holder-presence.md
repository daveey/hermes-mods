# Session-search FTS: futility keyed on holder presence

Mirror entries 5007, 5008, 5012 and 5013.

## Behavior

A stale full-text index deferred its online rebuild thousands of times while foreign holders were present at every deferral, yet never became "futile", because the recorded PID set changed constantly (short-lived CLI holders; each process excludes itself) and any change restarted the window. Automatic recovery never ran.

Now the futility window measures **continuous presence of any foreign holder**. It restarts only when (a) the last deferral saw no holder, (b) the clock went backwards (negative gap), or (c) no deferral was recorded for longer than twice the retry backoff cap (a new episode). Log and doctor text name the current holders rather than "the same ones". 5008 documents all three resets and tests the clock-back and empty-holder branches.

5012/5013: futility survives older record writers (an older writer resetting the shared record cannot starve the window; a broken presence record starts a new window; a peer-started episode does not inherit this handle's window). Doctor never names the gateway alone as the process to stop; two gateways are told they block each other; holder descriptions skip inline bootstrap `-c` code.

## How it works

`hermes_state_schema.py` updates the deferral record: consecutive attempts accumulate across changing PIDs; `_fts_deferral_futile` is true once `_FTS_HOLDER_FUTILE_ATTEMPTS` deferrals span `_FTS_HOLDER_FUTILE_SECONDS`. `hermes_state_fts_online.py` and `doctor_state.py` use the new wording.

## Code

`hermes_cli/doctor_state.py`, `hermes_state_fts_online.py`, `hermes_state_schema.py`, `hermes_state_dbfile.py`, `hermes_state_holders.py`.

## Tests

Source tests (not executed here): `tests/hermes_state/test_fts_futile_holder_churn.py` (`test_holders_present_at_every_deferral_turn_futile_even_when_the_pids_change`, `test_a_quiet_gap_longer_than_the_retry_cap_starts_a_new_window`, `test_an_older_writer_resetting_the_shared_record_cannot_starve_futility`, `test_a_new_episode_started_by_a_peer_does_not_inherit_this_handles_window`, plus reset-branch cases), `tests/hermes_cli/test_doctor_fts_deferral_holders.py` (`test_gateway_alone_is_never_named_as_the_process_to_stop`, `test_two_gateways_are_told_they_block_each_other`, `test_holder_description_skips_inline_bootstrap_code`).

## Applying

Follow the full segmented recipe in the core mods [README](README.md) ("Applying"); never apply on its own.

## Gotchas

- "Futile" triggers the forced offline-style recovery, so a wrongly long window delays repair and a wrongly short one disturbs live holders.
- The record wording changed; log-scraping tests must match the new text.
