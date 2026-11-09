# Mirror 2073 verification

Frozen source: `081be082652487cb6ab42676880d947c9772826f`; exclusion tip: `6cff782b8d081a0ed382eaf75c432d452cd452cc`.

## Inventory and replay

The unchanged full verifier exited **0** as the first command of the README Applying Bash block:

```json
{"ancestry_only": 3, "base": "6cff782b8d081a0ed382eaf75c432d452cd452cc", "bridges": 39, "merge_base": "8c1a9c0b6da872cca5083b98a7a1022edc3dfb4b", "patches": 2073, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "081be082652487cb6ab42676880d947c9772826f"}
```

The actual README block was extracted and executed in a shared bare source clone, exiting **0**. Its independently applied tree `1684a0aa2817e8ef722f672d0a9386f32c39702f` also passed the unchanged `verify_mirror.compare` with policy 2073. This establishes contiguous ordinals, unique SHA headers, exact source inventory, ancestry-only proofs and normalized full-tree equality. All 2072 previously published patches, 39 bridges, historical policies/plans and the verifier are unchanged. No source merge occurs in this append. Patch 2073 is the frozen source commit above.

Preflight found four unavailable abbreviated historical index objects: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, `a3701890ce1`. Both complete replays succeeded without these fallback preimages; their presence is not claimed.

## Privacy and independent review

The author is normalized to the standard public noreply identity. Incident-specific mail prose, source/test docstring narrative and the test chat identifier are generalized. Two new fingerprints cover exactly those comment/fixture substitutions in `hermes_cli/kanban_watch.py` and `tests/tools/test_kanban_watch_park.py`; all executable production logic is unchanged. Existing fingerprints, literal/regex substitutions and renames are unchanged.

Independent read-only review regenerated the source patch and checked all differences, the fingerprint blob pairs, snapshot bindings, historical-byte preservation, README slice coverage and feature test claims. Its stale previous-snapshot link finding was corrected, and older-schema behavior was explicitly labeled untested. Privacy denylist checks returned no hits.

## Executed tests

A disposable environment was built on the README-applied public tree with `python3 -m pm.build_env --source . --out ../test-venv --group dev --group test`. The canonical isolated runner then executed:

```sh
HERMES_PYTHON=../test-venv/bin/python scripts/run_tests.sh -j 1 tests/tools/test_kanban_watch_park.py
```

Result: **5 passed, 0 failed**, one file, no retries. These cover tool-handler scheduling, local database/activity projections, sticky/cleared watch state, long/short hold hints and scheduled-card completion refusal. Live external notification delivery, remote projection, native Desktop rendering, CLI/REST, terminal-tool/execute_code end-to-end paths, older schemas and the full suite were not exercised.

## Publication gates

Coverage and normal pre-commit privacy gates must pass before publication; no hook or verifier bypass is used. Documentation/JSON whitespace is checked separately from intentional mail-patch context and trailer whitespace.
