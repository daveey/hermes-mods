# Test: queued-work replay waits for completion publication

Patch: **1638** (`39820c1415`, `test(gateway): await queued-work completion publication`). Test-only.

## Behavior

`tests/gateway/test_journey_new_session_queued_work.py::test_new_session_never_absorbs_or_drops_earlier_queued_work`
checks that a new session neither absorbs nor drops earlier queued follow-ups. Its viewer replayed
`session.events.since` once from a saved cursor, but on the worker thread the SQLite terminal commit precedes the
`message.complete` publication, so a single replay could see the admission commit without the follow-ups' completion
messages and fail intermittently. The test now replays from the same cursor until both `RECOVERY_ACK_*` completion
texts are visible (bounded by a 30 s `asyncio.timeout`, only for hangs).

A third parametrization, `delayed-publication`, makes the gap deterministic: the new fixture holds the terminal commit
of the second follow-up before its completion event is published until the test touches a `release-publication` file,
and asserts the viewer does not see that completion before release.

## How it works

`tests/gateway/fixtures/queued_work_publication_daemon.py` wraps `gateway.session_results.finish_result`: after the real
call for request `A_FOLLOWER_2` it waits (up to 60 s, no stream lock held) for `$HERMES_HOME/release-publication`, then
runs `gateway.run` as `__main__`. The test starts the daemon with that fixture and `barrier=publication_gap`.

## Code

`tests/gateway/fixtures/queued_work_publication_daemon.py` (new), `tests/gateway/test_journey_new_session_queued_work.py`.
No production code changes.

## Tests

The patch is the test: ids `live`, `restart`, `delayed-publication`. Not rerun by this mirror pass.

## Applying

Apply in series order; depends on the journey test and daemon harness already in the series.

## Gotchas

The replay loop exits on the expected completion texts, not on an event count; extra events do not break it.

## Plugin?

No. Test harness only.
