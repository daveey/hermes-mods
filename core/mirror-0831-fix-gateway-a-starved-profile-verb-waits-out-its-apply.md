# fix(gateway): a starved profile verb waits out its apply [daveey]

Patch 0831, source `816c454ef01358d5a0e8db7dd8e4c9e3fa1bc0f5`.

## Behavior

fix(gateway): a starved profile verb waits out its apply [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def rescan_gateway_profiles(home: Path, *, timeout: float = 8.0) -> Optional[dic`
- `_PROFILE_SIGNATURE_FILES = ("config.yaml", ".env")`
- `def _profile_lifecycle_verb(runner, *, serve: bool):`
- `def test_draining_status_names_chat_and_cron_units_and_clears_when_running(tmp_p`
- `class TestSecondaryProfileConfigHandling:`
- `async def test_profile_control_verbs_round_trip_and_refusals(tmp_path, monkeypat`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Problem: test_profile_control_verbs_round_trip_and_refusals failed once
> on CI run 40739917536 with FileNotFoundError reading gateway_state.json,
> then passed on retry. SIGSTOP/SIGCONT (0.4s/0.03s) on the same test also
> returned pending with no served/unserved key.
>
> Why 1: the verb handler waited future.result(timeout=5) of wall clock,
> and the test read the status file after a 2s flush. Both expire while
> the process is stopped; monotonic time keeps running.
> Why 2: the apply is milliseconds of CPU and the status write is an
> off-thread fsync+rename, so a quiet machine hides both caps.
> Why 3: nothing stalled the apply past 5s, and the CI retry kept the job
> green. The 2s flush read is already fixed on main (7eb4ddb0); the verb
> cap was the one the prescribed starvation still hit.
> Root (happened): a wall-clock liveness cap sized for a quiet host was
> used as proof the coroutine had finished.
> Root (not caught): no test held the apply past 5s.
> Recurrence: first filing of this id; the flush half landed separately.
>
> Countermeasures: verb wait is 30s and returns as soon as apply finishes;
> the socket client waits 45s so it reads that answer. A 6s apply must
> return unserved, not pending. Sibling flush-then-read tests assert a
> 30s flush instead of ignoring a timed-out one.

## Code

- `gateway/control_socket.py`
- `gateway/run_profile_reconcile.py`

## Tests

Source test files changed by this commit:

- `tests/gateway/test_drain_active_work_report.py`
- `tests/gateway/test_multiplex_adapter_registry.py`
- `tests/gateway/test_multiplex_hot_serve.py`

New or changed test definitions present in the source diff:

- `test_profile_verb_waits_out_a_slow_apply`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
