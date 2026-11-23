# Golden-fixture recipe

Public structural cases live in `plugins/session-panel/tests/test_model.py`; `desktop-plugins/session-panel/test/hub-model-fixtures.py` serializes the **real reducer** into `hub-model.test.mjs`. Extend both sides, not a hand-built JavaScript imitation of PanelModel.

Shared blocker groups must apply the same owner-qualified canonical request retirement precedence as ordinary rows before consulting preserved `human_request`/`wait_state=human`. Test dismissed, answered and withdrawn requests plus a live-open positive control through the real reducer and renderer; an off-list parent must never turn red again after its ask retires. Run the compatible-core panel suite as well as no-core CI: no-core skips can hide stale real-core triage assertions. Give shared-group fixtures nested DOM assertions rather than applying the flat-row golden count contract.

Through `terminal`, run:

```sh
python3 scripts/test-all --no-core --exact -k plugins/session-panel -k plugins/ask-gate -k plugins/user-steps
python3 scripts/test-all --no-core --exact -k repo-guards
node --experimental-strip-types --test desktop-plugins/session-panel/test/hub-model.test.mjs
```

When pinning core for `scripts/test-all`, pass `--hermes-core <isolated checkout>` (or `HERMES_CORE_CHECKOUT`) and verify the emitted core path in `--json` results; setting `HERMES_CORE_SOURCE` alone does not select the runner's checkout. Before the Node command, provision dependencies exactly as `.github/workflows/desktop-plugin-tests.yml` specifies. For full core-backed coverage set `HERMES_CORE_SOURCE` to an isolated compatible core checkout, `HERMES_DESKTOP_DIR`/`HERMES_DESKTOP_PACKAGE` to its installed test dependencies, and make nanostores resolvable from that core. Run serially and require zero core-dependent skips before claiming full coverage. No backend `plugins/session-tasks` suite exists; its tests are Desktop tests. Run all `desktop-plugins/*/test/*.mjs` when changing shared rendering/answer integration.

For a private historical golden, retain raw input, observation time, source revision and expected output in private task attachments. Pin `now` and source clocks to the recorded snapshot; distinguish historical replay from current production. Never publish personal/employer/network details or normalize source identifiers/timestamps just to make a replay pass. Include local, remote, triage, C/A ordering, stale human block kind, retired requests, canonical header and consent boundaries. Inspect every resulting assertion and test exit code; missing dependencies or a timeout are not behavioral proof.
