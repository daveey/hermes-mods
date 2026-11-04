# Mirror 1932 verification

Frozen source tip: `fb3e4519f4bfec0bf480b026dd8dbc152b564b65`.
Upstream exclusion: `71736c9f32597bbca9d89cede5aa136e55946b2f`.

Nine missing SHA headers are appended as 1924–1932 in reverse non-merge rev-list order. Published 0001–1923 patch bytes and ordinals are unchanged. Feature documentation: [1924–1932](mirror-1932-features.md).

## Policy and replay

`mirror-policy-1932.json` copies policy 1923 with only `source_tip` changed. All literal/regex substitutions, fingerprints and renames are unchanged. `mirror-replay-1932.json` likewise changes only the source tip: its sixteen bridges and three ancestry-only entries are unchanged. The verifier itself is unchanged.

The historical index-object preflight checked 11,067 distinct nonzero IDs. Four abbreviated historical IDs remained unavailable: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, `a3701890ce1`. The full replay succeeded without requiring those fallback preimages.

## Executed verifier

This command exited **0** using a shared bare clone of the source object store:

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip fb3e4519f4bfec0bf480b026dd8dbc152b564b65 \
  --base 71736c9f32597bbca9d89cede5aa136e55946b2f \
  --patch-dir core/patches --policy core/mirror-policy-1932.json \
  --replay-plan core/mirror-replay-1932.json
```

```json
{"ancestry_only": 3, "base": "71736c9f32597bbca9d89cede5aa136e55946b2f", "bridges": 16, "merge_base": "a66e74dc67097219b6bb1807d18459859125be62", "patches": 1932, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "fb3e4519f4bfec0bf480b026dd8dbc152b564b65"}
```

This checks contiguous ordinals, unique SHA headers, exact equality with the complete non-merge source inventory, full replay, and normalized full-tree equivalence. The README Applying Bash recipe was separately extracted and executed unchanged (exit **0**). Its resulting commit `cc8c9968a1e409d30429b87c00a3b57eb1269ea8` passed `verify_mirror.compare` under the same policy.

The first invocation used the old snapshot-bound replay plan and was correctly rejected. Why: changing the policy tip alone does not change the replay plan's independent tip binding. Root (happened): incomplete snapshot preparation, not a replay defect. Root (not caught): the full verifier caught it before publication. Recurrence: prior snapshot documentation already names both files. Countermeasure: copy both snapshot documents, change only their source-tip fields, and run the unchanged verifier and actual README recipe; both now pass. No gate or published bytes were weakened.

## Tests

On the frozen source tree, the canonical `scripts/run_tests.sh -j 1` runner with an isolated `HERMES_HOME` returned **468 passed, 0 failed** across all twelve changed Python test files. The same selection also passed on the applied public tree; see the publication handoff for that run's result.

| File | Source passed |
|---|---:|
| `tests/acp_adapter/test_gateway_acp_session_end.py` | 2 |
| `tests/agent/test_interrupt_issuer_attribution.py` | 4 |
| `tests/agent/test_redact_outbox_secrets.py` | 332 |
| `tests/agent/test_terminal_batch_timeout_continues.py` | 3 |
| `tests/cron/test_update_admission_hold.py` | 7 |
| `tests/gateway/test_acp_shutdown_budget.py` | 4 |
| `tests/hermes_cli/test_admission_fence.py` | 21 |
| `tests/hermes_cli/test_kanban_dependency_review_exact_head_alias.py` | 12 |
| `tests/hermes_cli/test_kanban_dependency_review_sync.py` | 22 |
| `tests/hermes_cli/test_kanban_metadata_files.py` | 12 |
| `tests/hermes_cli/test_kanban_sync_hub_keepalive.py` | 29 |
| `tests/tui_gateway/test_credential_failure.py` | 20 |

macOS-only cases were skipped on Linux. No full suite, live Desktop UI, production gateway shutdown, or production Board operation is claimed. Redaction tests exercise both terminal and execute_code entry paths; the feature doc names remaining untested paths.

## Publication gates

The new mail patches use the policy's literal and regex normalization and the public mail author identity. The per-file private-denylist scan returned zero hits. Staged coverage, gitleaks, normal pre-commit hook, exact remote readback, CI and deployment evidence are recorded in the publication handoff. The source freeze deliberately excludes later commits, which need a subsequent contiguous pass.
