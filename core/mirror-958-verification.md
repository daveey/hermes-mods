# Public core mirror: canonical 0960-entry snapshot

## Snapshot and inventory

Fetched source snapshot on 2026-10-29:

- Upstream main: `e298669b44ce032a995c8a67ceae025801b0f4bf`.
- Fork main: `8d288f1b685e4188a1ef08d5de334a7f68e5eb3c`.
- Merge-base: `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`.
- `git rev-list --count --no-merges upstream/main..origin/main`: **960**.
- Public series: **960 files**, contiguous `0001` through `0960`; every `From <sha>` header matches the corresponding `git log --reverse --no-merges` source SHA. Each source commit has exactly one entry.

The first fetch/recount was 958 through `43cb700f69`. The final source refresh added `4c2a3c5e25` (0959, guarded terminal scratch GC retry) and `8d288f1b68` (0960, deterministic replica-clock assertions).

The previous public directory had 742 files ending at number 0741, including two files numbered 0671 and two entries carrying the same source SHA. A naive header comparison against the first 958-commit snapshot found 281 absent source SHAs and 64 stored headers outside the range. Those are not 281 new features: many old headers belonged to rebased or hand-adapted versions. Matching normalized subjects as well as SHAs preserved the prior linear adaptations. The README and feature docs now use canonical fork-log ordinals, including both entries formerly numbered 0671. Historical verification counts remain historical, not a description of this snapshot.

There are 213 new feature-document rows for newly represented changes. Seven actionable-evidence source entries are documented together in the existing actionable-evidence feature. The host-reboot feature has its own [doc](kanban-host-reboot.md) and README row naming `43cb700f69`.

## Fetch and ownership

No active mirror owner appeared in the cross-gateway task search; public remote content and recent commits were checked before editing. Unrelated plugin/README edits in the shared checkout were left alone.

The broad upstream fetch encountered `upload-pack: not our ref`. Both authoritative main tips fetched successfully without moving the live checkout's HEAD using:

```sh
git -c fetch.negotiationAlgorithm=noop fetch --no-tags --no-recurse-submodules upstream +refs/heads/main:refs/remotes/upstream/main
git -c fetch.negotiationAlgorithm=noop fetch --no-tags --no-recurse-submodules origin +refs/heads/main:refs/remotes/origin/main
```

This verifies the refs used for the inventory; it does not claim the remote's unrelated branch refs were repaired.

## Apply proof and adaptations

Executed against a detached throwaway worktree at the merge-base:

```sh
git am -3 --empty=keep <public-series>/[0-9][0-9][0-9][0-9]-*.patch
```

The final single-command replay of all **960** files exited **0**, with no manual resolutions during that final run. Its preparatory replay and exact-tree comparison found the historical merge-context issues described below; the published patches contain those resolved adaptations. Whitespace warnings were nonfatal.

`--empty=keep` is required: source duplicates and pre-existing empty entries are retained to make the inventory exact. In particular, 0364 is a deliberately empty duplicate of 0343; the two original source diffs have the identical stable patch-id `a205c93204217848e8642bc368aa91482b4950ea`. Other duplicate diffs may be recognized by three-way application as already applied. The number of newly created commits can therefore be less than the number of patch files.

Resolved contexts in canonical entries: 0343, 0363, 0364, 0408, 0462, 0467, 0508, 0536, 0556, 0690, 0698, 0705, 0743, 0796, 0809, 0941 and 0942. These preserve human-request state when signatures/refactors move, keep existing routing/skill validation when worker placement arrives, and respect the actual order of replica memoization and ghost-link cleanup.

0958 includes the host-reboot source change plus an explicit source-merge reconciliation overlay. That overlay restores source-main behavior not carried by the older curated non-merge exports:

- The final agent-WebSocket design from source merge `6abe376deb`, including case-normalized client kinds and explicit hidden intent; removes the earlier duplicate helper/plumbing and restores source tests.
- Supervisor cleanup of `human_request` and the supervisor lifecycle regression case from `323c8d17be`; the failure-trip test expects triage, matching implementation.
- Unknown-but-unfinished replica rows remain active (`6d2be1e857`).
- Fresh-observation input in the blocked-project stripe test (`25a633ef51`).
- Consistent waiting-card fixture/assertion in the projects RPC test (`09f30ee9f4`).
- The respawn-guard `worker_ran` fixture and both never-run / previously-ran regression tests omitted by old 0453 (now 0459).
- `kanban_stalled` in stripe transition diagnostic fields, omitted by the old curated adaptation (now 0558).

The implementation/test files in this overlay were copied from the source snapshot, with existing public privacy substitutions preserved. This is intentional merge linearization, not an additional unnumbered source commit.

## Executed tests

Canonical runner on the final applied candidate, with an existing test interpreter and two file workers:

```sh
HERMES_PYTHON=<existing-test-python> bash scripts/run_tests.sh \
  tests/hermes_cli/test_kanban_host_reboot.py \
  tests/hermes_cli/test_kanban_request_lifecycle.py \
  tests/hermes_cli/test_kanban_pr_continuation.py \
  tests/hermes_cli/test_api_ws_client_kind.py \
  tests/hermes_cli/test_kanban_session_activity.py \
  tests/hermes_cli/test_kanban_replica_decode_memo.py \
  tests/tui_gateway/test_projects_rpc.py \
  tests/tui_gateway/test_human_input_record.py \
  tests/hermes_cli/test_kanban_scratch_gc.py \
  tests/hermes_cli/test_kanban_wait_replica.py \
  -j 2 --file-timeout 180
```

Result: **196 passed, 0 failed**, 10 files, 25.6 seconds. After the final secret-scanner fixture normalization, repeated all ten files and added `tests/agent/test_step_boundary_pause.py`: **200 passed, 0 failed**, 11 files, 29.4 seconds. The added file's four tests exercise the step-boundary pause fixture.

| Source file | Passed |
|---|---:|
| `tests/hermes_cli/test_kanban_host_reboot.py` | 7 |
| `tests/hermes_cli/test_kanban_request_lifecycle.py` | 10 |
| `tests/hermes_cli/test_kanban_pr_continuation.py` | 22 |
| `tests/hermes_cli/test_api_ws_client_kind.py` | 4 |
| `tests/hermes_cli/test_kanban_session_activity.py` | 55 |
| `tests/hermes_cli/test_kanban_replica_decode_memo.py` | 3 |
| `tests/tui_gateway/test_projects_rpc.py` | 28 |
| `tests/tui_gateway/test_human_input_record.py` | 19 |
| `tests/hermes_cli/test_kanban_scratch_gc.py` | 22 |
| `tests/hermes_cli/test_kanban_wait_replica.py` | 26 |

Desktop Vitest: `gateway-device-icon.test.ts` passed **4** tests. `session-dot-state.test.ts` did not collect: the borrowed dependency tree was incomplete (last missing import: `@tanstack/react-query`). Initial missing `driver.js`, testing-library and icon/core packages were supplied in an isolated scratch dependency directory; no live install or test source was modified. No stripe-test pass, full Desktop suite, typecheck, live reboot or full repository test pass is claimed.

Feature docs list source tests for porting. Except for the executions above, those lists and quoted source-author results are not fresh validation claims.

## Why raw regeneration was insufficient

Problem: raw `git am -3` stopped at 0234; later candidate replays exposed further context conflicts and an exact-tree audit found missing behavior/tests even after replay reached 0958.

Why 1: exported diffs assumed file contexts or combined behavior supplied by source merges — evidence: the 0234 conflict and source merge remerge diffs above.

Why 2: the earlier public series was a curated linearization, with rebased/synthetic headers, reordered commits and omitted duplicate/integration entries — evidence: the prior reconciliation documents and the 742-file inventory versus source log.

Root (happened): non-merge `format-patch` inventory alone does not encode merge resolutions, and carrying a curated patch to a different ordinal can drop or pre-apply hunks. A successful application is not source-tree equivalence.

Root (not caught): `check_coverage.py` checks document rows, not source equivalence or behavior. The earlier verifier explicitly recorded an omitted stalled trace field as a limitation rather than repairing it. Prior full-series replay could therefore pass with missing tests and merge-only behavior.

Countermeasures in this publication: canonical one-source-entry inventory, explicit duplicate retention, preserved and repaired linearization, a clean final full-series replay, an independent source-tree audit, restoration of the identified source hunks and targeted behavior tests. Permanent automated source-equivalence gating is separate follow-up work; this report does not claim such a gate exists.

## Privacy and coverage

All exported identities use the public noreply identity. User/application paths, private addresses, AWS account/profile markers and contributor fixtures use existing example substitutions. The private denylist scan covers the entire candidate and feature documentation; normal pre-commit secret/privacy and coverage checks are required, not bypassed.

`python3 -B core/check_coverage.py` exited 0 with no gaps. The staged inventory has exactly 960 patch files. The normal pre-commit hook (gitleaks, private denylist, coverage), a full-content candidate/document denylist scan and exact patch-link checks all passed. Mail-patch context lines and Git signature separators intentionally retain their required spaces; document-only whitespace checks pass.

Gitleaks flagged a dummy test credential in 0750; it was shortened to `test` rather than bypassing the scanner. All 960 public files were replayed again after that change, exiting 0. Final source-tree review found only public substitutions (including host/account matchers that must be configured for a user's own deployment), reordered equivalent tests/type fields/keyword-only parameters, formatting/comments and harmless extra parentheses. No remaining omitted implementation or regression test was accepted. This is reviewed equivalence, not byte-identical source trees.

Permanent automated source-equivalence gating is tracked separately by Kanban task `t_01a187fd856151d6`; it does not delay this repaired snapshot. The completion handoff records the remote readback and disposable worktree removal.
