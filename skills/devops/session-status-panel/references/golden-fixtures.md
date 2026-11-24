# Golden-fixture recipe

For canonical-header/row disagreements, compose the real core `refresh_outcome` in a disposable stored chat through the real panel reducer and mounted renderer; handcrafted canonical envelopes cannot catch an adapter that admits stale/partial card status. Include retained terminal rows and stale/partial mirrored human asks with a known-current-human positive control: trust checks must precede both terminal filtering and human ownership, including unused mirror fallback. Preserve independent active-local-turn precedence and require reason-aware unresolved labels.

Public structural cases live in `plugins/session-panel/tests/test_model.py`; `desktop-plugins/session-panel/test/hub-model-fixtures.py` serializes the **real reducer** into `hub-model.test.mjs`. Extend both sides, not a hand-built JavaScript imitation of PanelModel.

Shared blocker groups must apply the same owner-qualified canonical request retirement precedence as ordinary rows before consulting preserved `human_request`/`wait_state=human`. Test dismissed, answered and withdrawn requests plus a live-open positive control through the real reducer and renderer; an off-list parent must never turn red again after its ask retires. Run the compatible-core panel suite as well as no-core CI: no-core skips can hide stale real-core triage assertions. Give shared-group fixtures nested DOM assertions rather than applying the flat-row golden count contract.

## Per-card fields

A per-card fact (a count, a run property) belongs in `dashboard/plugin_api.py:_detail` as a key of `detail['task']`. It is the single card-detail producer (board capture calls it too), and `reduce_panel` merges `cards[id].detail.task` under each row; core's session-task rows carry only `_TASK_FIELDS`, so adding the field there needs a core change. Guard the SQL with `except sqlite3.Error` -> `None` (unreadable, not zero) and render nothing for missing/None/non-int. Test both ends: `tests/test_core_readers.py` (`r.card(id, 'default')['detail']['task'][field]` on a real board with `task_runs` rows) and a `test_model.py` case whose bundle carries hydrated `cards={id: {'detail': {'task': {...}}}}`. A detail-only append needs no Desktop change: the renderer prints `detail` verbatim.

Done rows are the exception: `Feed.compute` hydrates cards only by walking from open linked rows and stops at terminal status, so `bundle['done']` rows never get `cards[id]`. Attach a done-row field to the row itself in both producers: `CoreReaders.done` (copy rows; core's listed rows can be memoized) and `board_capture.capture_local` for refreshed rows (the snapshot path never calls `done`). Per-run cost data: tokens come from each run's newest `worker_result` event `usage` (`cost_usd` is present only on some runs); the model is that run_id's `run_route` event. `agent.usage_pricing.estimate_usage_cost` returns `included` for `openai-codex` and `unknown` for `xai-oauth`: price those at `openai`/`xai` list rates. Class-routed runs record `class:*` and have no priceable model: say so, never a guessed figure.

## Running the suites

Through `terminal`, run:

```sh
python3 scripts/test-all --no-core --exact -k plugins/session-panel -k plugins/ask-gate -k plugins/user-steps
python3 scripts/test-all --no-core --exact -k repo-guards
node --experimental-strip-types --test desktop-plugins/session-panel/test/hub-model.test.mjs
```

For a quick core-backed run of the panel tests on the hub, use core's venv: `~/.hermes/hermes-agent/.venv/bin/python -m pytest -q -p no:cacheprovider plugins/session-panel/tests/` (`hermes-core-python` has no pytest). Under `pytest -n`, `test_fact_transport.py::test_host_large_reset_recovers_panel_without_resubscription_loop` fails with `AF_UNIX path too long` (xdist's deeper tmp path); rerun it alone before treating it as a regression.

When pinning core for `scripts/test-all`, pass `--hermes-core <isolated checkout>` (or `HERMES_CORE_CHECKOUT`) and verify the emitted core path in `--json` results; setting `HERMES_CORE_SOURCE` alone does not select the runner's checkout. Before the Node command, provision dependencies exactly as `.github/workflows/desktop-plugin-tests.yml` specifies. For full core-backed coverage set `HERMES_CORE_SOURCE` to an isolated compatible core checkout, `HERMES_DESKTOP_DIR`/`HERMES_DESKTOP_PACKAGE` to its installed test dependencies, and make nanostores resolvable from that core. Run serially and require zero core-dependent skips before claiming full coverage. No backend `plugins/session-tasks` suite exists; its tests are Desktop tests. Run all `desktop-plugins/*/test/*.mjs` when changing shared rendering/answer integration.

For a private historical golden, retain raw input, observation time, source revision and expected output in private task attachments. Pin `now` and source clocks to the recorded snapshot; distinguish historical replay from current production. Never publish personal/employer/network details or normalize source identifiers/timestamps just to make a replay pass. Include local, remote, triage, C/A ordering, stale human block kind, retired requests, canonical header and consent boundaries. Inspect every resulting assertion and test exit code; missing dependencies or a timeout are not behavioral proof.
