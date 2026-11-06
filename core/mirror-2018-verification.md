# Mirror 2018 verification

Frozen source: `d84d70b45253b673264f427dcc50ea089833be58`; exclusion base: `a2db0efb2969d2527d27bf95a53066f5e064d9ad`.

The unchanged `verify_mirror.py` completed with exit 0, checking complete source inventory, contiguous unique headers, replay and policy-normalized full-tree equality:

```json
{"ancestry_only": 3, "base": "a2db0efb2969d2527d27bf95a53066f5e064d9ad", "bridges": 24, "merge_base": "a2db0efb2969d2527d27bf95a53066f5e064d9ad", "patches": 2018, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "d84d70b45253b673264f427dcc50ea089833be58"}
```

The README Applying recipe was also executed separately and its resulting tree compared with the same policy. The added bridge is the policy-normalized first-parent diff of integration merge `b447a1f13d2dc82cf419b90f135c4dd17d67ef81`. Previous patches and bridges are unchanged. Policy fingerprints and normalization rules are unchanged; only source-tip/base bindings advance.

Source and README-replayed public trees each ran `scripts/run_tests.sh -j 1 tests/ci/test_plugin_catalog_release_identity.py tests/ci/test_plugin_catalog_ci_confinement.py`: **69 passed, 0 failed, 1 skipped** on each. This includes both new real-Git release-identity tests. No full-suite replay-tree test claim is made.

The repaired trusted-base workflow was additionally exercised by temporary catalog-only PR #911, without changing the OMH source pin. [Plugin Catalog CI](https://github.com/daveey/hermes/actions/runs/42185314096) and its [pinned-source job](https://github.com/daveey/hermes/actions/runs/42185314096/job/127784140858) succeeded. The log verifies bubblewrap 0.9.0, real OMH pin `8ea5569fa9d4d17c4103c330b148484b58a74462`, isolated `register()` execution, matching declared tools/hooks/middleware, and `Validation passed`. PR #911 was closed unmerged after collecting evidence; its comment-only change is not in this mirror.
