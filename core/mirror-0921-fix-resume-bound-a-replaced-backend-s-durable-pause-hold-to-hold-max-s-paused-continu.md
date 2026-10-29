# fix(resume): bound a replaced backend's durable pause hold to HOLD_MAX_S; paused continuation takes over idle detached holders [daveey]

Patch 0921, source `4897e78880e91de5027c9a2128c5fbb78598ed43`.

## Behavior

fix(resume): bound a replaced backend's durable pause hold to HOLD_MAX_S; paused continuation takes over idle detached holders [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_replaced_backend_slot_refusal_backs_off(recovery, monkeypatch):`
- `def test_pause_readers_use_exclusive_slot_without_detached_takeover(recovery, mo`
- `def _maybe_schedule_auto_continue(sid: str, session: dict, session_key: str) ->`
- `def _session_moved_refusal(session: dict):`
- `def _ensure_active_session_slot(sid: str, session: dict, *, allow_takeover: bool`
- `def _detached_lease_holder(session: dict, key: str) -> tuple[str, dict] | None:`
- `def _take_over_detached_runtime_lease(sid: str, session: dict, key: str) -> bool`
- `def _now() -> float:`
- `def _durable_continuation_due(session: dict) -> bool:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Review of 0514681bb4: the durable core_update_pause hold had no time bound, so a
> continuation refused forever (live foreign owner, startup sweep gave up after 600s)
> refused every automatic notice for the whole freshness window (240 min). The hold
> now starts when this backend first sees the pause, keyed by the pause identity
> (started_at, paused_at, pauses), and releases after HOLD_MAX_S (305s) like the
> in-place hold; re-reading the same marker never re-arms it, a new pause does.
>
> Paused continuations used allow_takeover=False, so an idle detached sibling holding
> the lease blocked them until the WS orphan reap (20s grace, longer while a
> detached turn runs). They now take over only an idle detached holder; a mid-turn
> holder (notice, another reader's continuation) is never interrupted and the 30s
> refusal backoff retries.

## Code

- `tui_gateway/session_auto_continue.py`
- `tui_gateway/session_lifecycle.py`
- `tui_gateway/update_pause.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_update_pause_recovery.py`

New or changed test definitions present in the source diff:

- `test_pause_reader_takes_over_only_an_idle_detached_holder`
- `test_replaced_backend_durable_hold_is_bounded`
- `test_typed_message_during_build_retires_scheduled_pause`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
