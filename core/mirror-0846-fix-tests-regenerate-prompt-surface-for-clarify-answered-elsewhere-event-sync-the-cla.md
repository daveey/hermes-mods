# fix(tests): regenerate prompt surface for clarify answered_elsewhere; event-sync the clarify early-answer tests

Patch 0846, source `1bb8af6ddbd6ded615741caea4821c7347275c65`.

## Behavior

fix(tests): regenerate prompt surface for clarify answered_elsewhere; event-sync the clarify early-answer tests. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `"stable": 1935,`
- `"stable": 1445,`
- `"stable": 1866,`
- `"stable": 2640,`
- `"stable": 1317,`
- `"stable": 1579,`
- `"stable": 1349,`
- `"stable": 1382,`
- `{`
- `def _clean(monkeypatch):`
- `def test_decision_recorded_while_blocked_ends_the_clarify():`
- `def test_a_real_answer_that_wins_the_race_is_not_replaced():`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> 7cf108dab2 changed the clarify schema description without re-rendering
> tests/fixtures/prompt_surface (test_committed_prompt_surface_is_current red
> on main: stale surfaces.json + tools/clarify.json). Re-rendered; the diff is
> the answered_elsewhere sentence in clarify.json and the matching +42
> tool_tokens per surface.
>
> test_decision_recorded_while_blocked_ends_the_clarify failed in CI with
> `assert (True and 0 == 1)`: after a fixed sleep(0.2) the surface callback
> had not been reached yet. The path to it runs the on_human_input_request
> hook dispatch (plugin discovery, measured 0.18-0.36s per test locally), so
> 0.2s raced the runner. Two siblings had the same shape (sleep 0.4 / 0.2);
> the race test fails outright under the same delay because the pre-ask
> check sees the decision. Blocking now sets an `entered` Event and the tests
> wait on it (and on a provider poll count), never on the clock. Repro: a
> 0.5s delay injected into human_input_hooks._fire fails 2/7 before, 7/7
> pass after.

## Code

- `tests/fixtures/prompt_surface/surfaces.json`
- `tests/fixtures/prompt_surface/tools/clarify.json`
- `tests/tools/test_clarify_early_answer.py`

## Tests

Source test files changed by this commit:

- `tests/fixtures/prompt_surface/surfaces.json`
- `tests/fixtures/prompt_surface/tools/clarify.json`
- `tests/tools/test_clarify_early_answer.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
