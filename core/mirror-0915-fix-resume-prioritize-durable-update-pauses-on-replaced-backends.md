# fix(resume): prioritize durable update pauses on replaced backends [daveey]

Patch 0915, source `0514681bb4059a639d22bf5abd54465e8f38cbe9`.

## Behavior

fix(resume): prioritize durable update pauses on replaced backends [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_tui_setup_turn_still_auto_continues(emits, schedule_env, marker_home, u`
- `def test_config_widens_freshness_window(emits, schedule_env, marker_home, monkey`
- `def recovery(tmp_path, monkeypatch, request):`
- `def assert_continued(r):`
- `def test_a_continuation_that_never_schedules_stops_holding_automatic_turns(recov`
- `def _maybe_schedule_auto_continue(sid: str, session: dict, session_key: str) ->`
- `def continuation_due(session: dict) -> bool:`

## Code

- `tui_gateway/session_auto_continue.py`
- `tui_gateway/update_pause.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_auto_continue.py`
- `tests/tui_gateway/test_update_pause_recovery.py`

New or changed test definitions present in the source diff:

- `test_replaced_backend_notice_requeues_before_real_continuation`
- `test_replaced_backend_slot_refusal_backs_off`
- `test_two_readers_do_not_replay_a_consumed_pause`
- `test_pause_readers_use_exclusive_slot_without_detached_takeover`
- `test_typed_message_during_build_retires_scheduled_pause`
- `test_durable_hold_does_not_starve_unrecoverable_or_live_writer`
- `test_connected_desktop_reattach_retries_after_admission_hold`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
