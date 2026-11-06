# Mirror 2029 verification

Frozen source: `39115a6b6799f4935ee2263c1f6f5ff34cea4821`; upstream exclusion tip: `6b21d0564de0492e2ebd0d8f63fca86c03b5c892`.

## Inventory and replay

The unchanged verifier exited 0 with this exact output:

```json
{"ancestry_only": 3, "base": "6b21d0564de0492e2ebd0d8f63fca86c03b5c892", "bridges": 27, "merge_base": "6b21d0564de0492e2ebd0d8f63fca86c03b5c892", "patches": 2029, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "39115a6b6799f4935ee2263c1f6f5ff34cea4821"}
```

This checks contiguous ordinals, unique SHA headers, exact complete source inventory, ancestry-only source-parent proofs and policy-normalized full-tree equality. The README Applying Bash block was separately extracted verbatim and executed successfully; `verify_mirror.compare` also passed against that recipe's resulting HEAD. A successful `git am` alone was not used as proof.

All 2018 earlier mail patches and all earlier bridge bytes remain unchanged, including the bridge stored outside `bridges/`. Existing normalization rules, fingerprints and renames are unchanged; policy bindings alone advance. The 11 appended mail files match independently regenerated, policy-normalized `git format-patch` output with noreply author headers.

Preflight found four unavailable historical abbreviated index objects: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, `a3701890ce1`. Both complete replays succeeded without needing their three-way fallback preimages. They are not silently claimed present.

## New bridge provenance

| Before patch | Normalized binary source diff | Source merge anchor |
|---|---|---|
| 2019 | `d84d70b45253..178fbdc2f76` | `178fbdc2f76` |
| 2021 | `60aadf32807..666df8e427b` | `666df8e427b` |
| 2026 | `82b06902139..14e7480d76b` | `7ae947dfbc0` |

Independent review verified every hash pin and byte-normalized source delta. Each range has no non-merge fork commits outside the new exclusion base: these bridges carry upstream integration and merge resolution, not duplicate appended patches. The diff from `0bdfa756c17` to `60aadf32807` matches relay patch 2020, so applying that old-parent test patch after 2019 reconstructs the integration tree. The intervening tested-sync merge trees equal their branch tips. The last bridge touches none of the bounded-deadline fix's paths, preserving patch 2025.

## Targeted runtime tests

Both frozen source and README-replayed public trees independently provisioned their Python test environments and ran `scripts/run_tests.sh -j 1` with all eleven files below. Each returned **119 passed, 0 failed** (source 87.6 seconds; public replay 81.8 seconds).

| File | Passed per tree |
|---|---:|
| `tests/agent/test_content_policy_refusal_stop_details.py` | 17 |
| `tests/ci/test_fork_runner_routing.py` | 11 |
| `tests/ci/test_runtime_install_shape.py` | 11 |
| `tests/ci/test_tui_install_shape.py` | 13 |
| `tests/cron/test_cron_pause_retirement_controls.py` | 9 |
| `tests/cron/test_cron_shared_uncertainty_pause.py` | 3 |
| `tests/cron/test_cron_terminal_pause_retirement.py` | 10 |
| `tests/gateway/relay/test_relay_status_anchor_per_turn.py` | 8 |
| `tests/gateway/test_content_policy_live_notice.py` | 14 |
| `tests/hermes_cli/test_kanban_sync_metrics_budget.py` | 10 |
| `tests/tools/test_checkpoint_merge_staging.py` | 13 |

The JavaScript stall-probe suite, macOS source-build path, full install-shape builds, mutation harness, Nix build and full repository suite were not rerun here. The incoming cron handoff's broader 30-test/12-mutant/CI results remain attributed evidence, not this pass's results. No Mac deployment or historical incident recovery is claimed.

## Publication gates and limits

Coverage checker and docs/JSON whitespace checks passed. The private denylist scan found no matches in new patches, bridges, docs or policy files. Independent review identified one upstream synthetic credential fixture in the 2019 bridge; a reviewed, line-scoped `.gitleaksignore` fingerprint preserves the source bytes. The subsequent staged gitleaks scan passed with no findings.

Cron docs retain the preexisting cross-process-lock fallback, the ambiguity of an identical bare disabled write, and the unproven actor behind historical journal deletion. See [feature details](mirror-2029-features.md) and [cron recovery](mirror-admission-and-test-followups.md).
