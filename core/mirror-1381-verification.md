# Mirror 1381 verification

Frozen source tip: `b1af31437760e139cc4729fdbd513ca9c276e8f8`.
Upstream exclusion base: `6bb377a51d5c8e279a7a5e34d717d653d421d064`.

## Inventory and replay

Fetched both source remotes and the public remote before inventory discovery; checked the public remote again immediately before exporting. The six absent SHA headers were appended in reverse non-merge rev-list order as 1376–1381. Published patch bytes and ordinals are unchanged. The 1375 policy and replay plan were copied with only `source_tip` changed; no fingerprint exception was added or changed.

Executed from the mirror workspace (shared bare source clone):

```sh
python3 -B mods/core/verify_mirror.py \
  --source-repo "$PWD/src.git" \
  --source-tip b1af31437760e139cc4729fdbd513ca9c276e8f8 \
  --base 6bb377a51d5c8e279a7a5e34d717d653d421d064 \
  --patch-dir mods/core/patches \
  --policy mods/core/mirror-policy-1381.json \
  --replay-plan mods/core/mirror-replay-1381.json
```

Exit **0**, exact JSON:

```json
{"base": "6bb377a51d5c8e279a7a5e34d717d653d421d064", "bridges": 1, "merge_base": "ef35e8b9e1a8bc7f0efb50e4f2d68f1ec052f6a8", "patches": 1381, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "b1af31437760e139cc4729fdbd513ca9c276e8f8"}
```

This checks contiguous ordinals, unique SHA headers, complete source-header set, full bridge-aware replay, and final tree equivalence under the public normalization policy. Preflight checked 8138 distinct nonzero patch-index object IDs: historical abbreviated preimage `9ff9444f10` was unavailable. Full replay nevertheless succeeded without that object; no patches were rewritten or verifier rules weakened.

## Runtime regression checks

In a detached worktree at the frozen source tip:

```sh
scripts/run_tests.sh tests/ci/test_fork_os_lane_hook.py \
  tests/ci/test_runtime_install_shape.py \
  tests/ci/test_fast_runner_routing.py \
  tests/ci/test_tui_install_shape.py \
  tests/hermes_cli/test_backup_preflight_staging.py
```

Exit **0**: **5 files, 60 tests passed, 0 failed**. Per-file counts: hook 25; runtime install shape 9; fast-runner routing 10; TUI install shape 12; backup preflight staging 4. These were source-worktree tests, not tests rerun on the normalized public replay. The complete hosted CI workflow and Desktop end-to-end updater were not exercised.

## Publication checks

New patches use the public author header and standard path/app-name substitutions. Each new patch is checked separately against the private denylist; only zero matches are acceptable. Documentation coverage and the normal public pre-commit gates are required before publishing. README Current inventory and Applying refer to 1381 entries, including the 54-patch segment after the bridge.
