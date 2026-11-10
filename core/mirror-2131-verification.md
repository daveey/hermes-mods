# Mirror 2131: executed verification

## Snapshot and inventory

This pass appends 58 non-merge source commits at ordinals **2074–2131**. It preserves every published patch through 2073 and all 39 previously published bridges byte-for-byte. The source was frozen at `f953f662ccd84cbb643d71e113f9a13f939c4a94`; upstream exclusion is `8471f614b4879144d36e8919fb4e1f3c430d2100`. A newer exclusion tip removes upstream history from the inventory, not fork patches; the verifier checks full header-set equality rather than trusting the earlier backlog estimate.

The unchanged `verify_mirror.py` CLI, invoked by the actual README Applying recipe, exited 0 and printed:

```json
{"ancestry_only": 3, "base": "8471f614b4879144d36e8919fb4e1f3c430d2100", "bridges": 41, "merge_base": "b3746e35ddd32079697728e2df57a9d78f1e9e32", "patches": 2131, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "f953f662ccd84cbb643d71e113f9a13f939c4a94"}
```

This includes contiguous ordinals, unique source headers, exact source inventory, three source-parent ancestry-only checks, complete replay and full-tree comparison under the explicit privacy policy. No verifier changes or skipped inventory entries were needed.

## Merge bridges

- Before **2083**: normalized first-parent diff of integration merge `d5566cffa9dc905652f8dd5868b53f4e69876869`.
- Before **2087**: normalized first-parent diff of integration merge `aac2fb4d711979d5cb692d641b50bb86dd69784c`, excluding only the six files whose complete binary diffs are byte-identical to the separately mirrored commit `f0a1ed47e94f0e22f6d70b512f3b9c07d848b6f4`. Those files are `tests/ci/just_recipes.py`, `tests/ci/test_fast_runner_routing.py`, `tests/ci/test_runtime_install_shape.py`, `tests/gateway/test_class_model_switch_persistence.py`, `tests/gateway/test_gil_budget.py`, and `tests/gateway/test_session_race_guard.py`. Each equality was asserted before bridge creation. The replay anchor is ancestral upstream branch merge `a719f68af0320809bf5166cc2a6a77cc53a4be8a`, because the later integration merge is not ancestral to patch 2087.

Both bridge hashes are pinned in [the replay plan](mirror-replay-2131.json). The final tree comparison proves newer mainline changes were preserved when the upstream delta was introduced early for the branch-side contract patch.

## Privacy policy

The existing author normalization, literal substitutions and hostname-family regex rules were applied to new mail patches and bridges. The new contributor map uses a public example-domain path, pinned by unchanged source/applied content blobs in the rename policy. Multi-word completion fixtures replace the four-character example token with `demo` (and its bot variant consistently), preserving query lengths and prefix behavior.

Four existing fingerprints were repinned after their source files changed: `hermes_cli/kanban_placement.py`, `tests/hermes_cli/test_kanban_placement.py`, `tests/tui_gateway/test_notice_guarantee.py`, and `tui_gateway/kanban_unloaded.py`. The removed/added privacy lines were compared to the previous policy's blob pairs and asserted identical; these are historical normalization re-pins, not new exemptions for code changes. No historical patch or policy was rewritten.

## Applying recipe and prerequisites

The README's literal Bash recipe was extracted and executed, including its initial unchanged full verifier invocation. All 2131 entries, 41 bridges and three ancestry markers applied successfully. A separate call to the unchanged policy-aware tree comparator accepted the recipe's final tree at replay commit `05fe0ca9867fbd1476db1141be6b34a1e8246975`. That ephemeral commit is execution evidence, not a required source ref. The recipe worktree was removed after comparison.

Preflight checked **12055** nonzero patch-index IDs. Four historical abbreviated objects were unavailable: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, `a3701890ce1`. Both complete replays succeeded without those fallback preimages; their hunks applied without requiring the missing objects. This does not certify arbitrary independent patch subsets or a different source object store.

## Evidence boundary

Thirty feature documents describe the source regression coverage and explicitly distinguish it from tests executed in this mirror pass. This pass verifies the full public mirror, not a new run of all source Python/JavaScript suites, live Desktop behavior, credential migration, old-chat recovery or global exactly-once delivery. The parked-completion, cron-ownership, pending-corruption and passive-notice documents retain those limits. Public mod deployment is separate from installing the underlying fork runtime.
