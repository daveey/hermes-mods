# Mirror 2072 verification

Frozen source: `92ac64143d7ab113ff2b1d49780f245ff3d630c3`; upstream exclusion tip: `6cff782b8d081a0ed382eaf75c432d452cd452cc`.

## Inventory and replay

The unchanged `verify_mirror.py` exited **0**, including as the first command of the README's actual Applying recipe:

```json
{"ancestry_only": 3, "base": "6cff782b8d081a0ed382eaf75c432d452cd452cc", "bridges": 39, "merge_base": "8c1a9c0b6da872cca5083b98a7a1022edc3dfb4b", "patches": 2072, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "92ac64143d7ab113ff2b1d49780f245ff3d630c3"}
```

The README Bash block was extracted and executed verbatim in a shared bare source clone. Its separately applied tree also passed `verify_mirror.compare` under the frozen policy. These checks establish contiguous ordinals, unique headers, exact source inventory, source-parent ancestry-only proofs and normalized full-tree equality. All 2067 published patches, 39 bridges, historical policies/plans and the verifier remain unchanged. No source merges occur in this append.

| Patch | Source |
|---|---|
| 2068 | `3282aa2a47b6d1b362631ae824cadf702cf2d68b` |
| 2069 | `e705d05c715054f05c7026c06f5cf559ce056cc1` |
| 2070 | `75f66cff3c7a8412eadd2ff9a042b200688ea796` |
| 2071 | `af3ea6ae52d677a6ae01340a3dd91d997e98baa6` |
| 2072 | `92ac64143d7ab113ff2b1d49780f245ff3d630c3` |

Historical index preflight found four unavailable abbreviated objects: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, `a3701890ce1`. The full verifier and actual README application succeeded without requiring these fallback preimages. Their presence is not claimed.

## Privacy and review

New mail authors and the standard literal/hostname substitutions are normalized. Three new exact fingerprint pairs cover only generic sample repository, branch, path and fixture names in `hermes_cli/kanban_db_workspace.py`, `tests/hermes_cli/test_kanban_worktree_restore.py` and `tests/hermes_cli/test_kanban_parent_first_dispatch.py`. The restoration fixture uses consistent generic names throughout setup and assertions; a project-specific parent title becomes an example title. Existing fingerprint pairs are unchanged.

Independent read-only review confirmed inventory, source fidelity, fingerprint correspondence, unchanged historical bytes and README slice coverage. It additionally flagged a private incident narrative in patch 2068; its host/date/card identifier were generalized, and the affected test fingerprint was re-pinned. Both full replay paths, the full-tree comparison and all targeted tests were rerun after that correction. No executable production behavior is hidden by these normalizations.

## Executed tests

A disposable environment was built with `python3 -m pm.build_env --source . --out ../test-venv --group dev --group test`. On the final README-applied public tree, the canonical isolated runner executed:

```sh
scripts/run_tests.sh -j 1 tests/hermes_cli/test_kanban_worktree_restore.py tests/hermes_cli/test_kanban_parent_first_dispatch.py tests/hermes_cli/test_kanban_runs_on_label_admission.py tests/agent/test_turn_stop_gates.py
```

Result: **35 passed, 0 failed**, four files, no retries: restoration 4, parent dispatch 14, label admission 12, pre-verify status 5. Full-suite testing, live message delivery, native Desktop status rendering, production latency measurement, CLI/REST end-to-end creation and terminal-tool/execute_code parity were not exercised in this mirror pass. Feature documentation separates these limits from unit and database/tool-handler coverage.

## Publication gates

Coverage and normal pre-commit privacy checks exited **0** before publication. Documentation/JSON whitespace checks also passed, separately from intentional mail-patch context and trailer whitespace. No verifier, scanner or hook bypass is used.
