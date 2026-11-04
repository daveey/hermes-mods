# Mirror 1918 verification

Frozen source: `b9eaa8e842f5bc0c0e513e255f28a2df0850968c` (daveey/hermes `origin/main` on 2026-11-04).
Upstream exclusion: `fd267bb3242a14c33d4899cffc60ee4be816f14d` (was `f0d94882e89a`). It is also the merge-base:
the fork merged upstream `717447e421`, `fbf6c6c91d8`, `c314285f077` and `fd267bb324` after the 1821 tip.
This pass appends **97** unpublished non-merge commits as **1822–1918**, in
`git rev-list --reverse --no-merges` order, numbered from the last published ordinal + 1.
Patch files 0001–1821 and their ordinals are unchanged.

## Why new bridges

The previous replay ends at the 1821 tip (`2050733bc65`). Between that tip and `b9eaa8e842f`, the fork's first
parent includes four upstream merges and two feature-branch merges that needed hand resolution. The replay pins each
bridge to a hash and places it immediately before the first patch that depends on it (`mirror-replay-1918.json`).
The seven 1821 bridges are kept unchanged and seven new ones are added:

| Before | Bridge | Source | Why |
|---|---|---|---|
| 1822 | `bridges/upstream-717447e-via-02df0dd38e8.diff` | merge `02df0dd38e8` (upstream `717447e4217`) | Upstream content. Its two conflicts (`tools/process_registry_awaiting.py`, `tui_gateway/session_notifications.py`) are resolved as the fork finally resolved them in `ceff8ab7358`. |
| 1823 | `bridges/upstream-fbf6c6c-via-95e40c5b81b.diff` | merge `95e40c5b81b` (upstream `fbf6c6c91d8`) | First-parent delta of that merge. |
| 1824 | `bridges/context-1824-prompt-turn-branch-side.diff` | `tui_gateway/prompt_turn.py` at 1824's own parent | 1824–1827 were written on `fix/upstream-six-717447` before that branch merged main's notice-commit change (`1bf6faea509`). In this replay, main's version is already applied, so 1824 needs the branch-side file context. |
| 1828 | `bridges/resolution-1bf6faea509.diff` | merge `1bf6faea509` | That merge's hand resolution: `prompt_turn.py`, `exit_job_receipts.py` and three tests, taken at the merge. |
| 1866 | `bridges/context-1866-kanban-db-before-cpu-gate.diff` | `hermes_cli/kanban_db.py` at `1460c200ab0` (1866's parent) | The capsule stack (1866) was written before the CPU-gate branch (1848–1860) changed `kanban_db.py`. |
| 1893 | `bridges/upstream-through-27576ed88c6.diff` | merges `fe003ae5188` + `27576ed88c6` (upstream `c314285f077`, `fd267bb3242`) | Combined first-parent delta. Its conflicts are resolved as in `ac3196b7610`. |
| 1896 | `bridges/resolution-04bc5b36944.diff` | merge `04bc5b36944` | That merge's hand resolution of `hermes_cli/kanban_db.py` (capsule stack + CPU gate). |

How the bridges were built: in a source-space replay (a detached worktree of the source clone at the 1821 tip's tree),
each bridge was recorded as `git diff --binary` from the replay state just before patch N, and the published-form patches
were then applied with `git am -3 --empty=keep`. Every bridge file was privacy-normalized with the policy substitutions.
None of them changes a published patch byte. The 0001–1821 replay path is identical to the 1821 plan.

## Executed acceptance

The unchanged verifier CLI exited **0** on the complete inventory and the normalized full-tree comparison.
The source repo was a `git clone --bare --shared` of the hub's fork checkout. The run took 106 s.

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip b9eaa8e842f5bc0c0e513e255f28a2df0850968c \
  --base fd267bb3242a14c33d4899cffc60ee4be816f14d \
  --patch-dir core/patches --policy core/mirror-policy-1918.json \
  --replay-plan core/mirror-replay-1918.json
```

```json
{"ancestry_only": 3, "base": "fd267bb3242a14c33d4899cffc60ee4be816f14d", "bridges": 14, "merge_base": "fd267bb3242a14c33d4899cffc60ee4be816f14d", "patches": 1918, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "b9eaa8e842f5bc0c0e513e255f28a2df0850968c"}
```

The README **Applying** recipe was also run as written, with its new slices from `"${patches[@]:1821:1}"` through
`"${patches[@]:1895:23}"`. It ran in a throwaway worktree at `831637fe8d5`, and every `git am`/`git apply` step
exited 0. The resulting commit's tree passed `verify_mirror.compare` against the frozen tip under
`mirror-policy-1918.json`.

## Privacy

Each new patch was normalized with the standard substitutions and the `From:` rewrite. It was then scanned with the
private denylist (`grep -cEi`): every patch file, every new bridge and every new or edited doc printed `:0`.
Each new patch has exactly one `From <sha>` separator.

gitleaks flagged two `generic-api-key` hits, both in upstream test files carried by
`bridges/upstream-through-27576ed88c6.diff`. Line 25628 is `tests/agent/test_nous_transient_401_interrupt.py`'s dummy
placeholder `api_key` test string. Line 27052 is the synthetic `OPAQUE_IDENTITY_TOKEN` fixture in
`tests/gateway/relay/test_relay_transient_recovery.py`. Neither is a credential. Each is ignored by its own line-scoped
`.gitleaksignore` entry, following the `upstream-bbb69887a1c.diff` precedent. The bridge bytes are unchanged.

## Reviewed fingerprints

No fingerprint, substitution or rename rule was added. Five files that already had reviewed fingerprints changed in
this batch, so they were re-pinned: `hermes_cli/kanban_overlap.py`, `hermes_cli/kanban_placement.py`,
`tests/hermes_cli/test_kanban_placement.py`, `tests/tools/kanban_overlap_failure_cases.json` and
`tests/tui_gateway/test_durable_takeover_recovery.py`. For each one, I compared the added and removed lines of the old
pair's diff with those of the new pair's diff. They are byte-identical: the same reviewed normalization, with only the
context shifted.

## Tests run

On a tree identical to the source tip (`3c08f343ad7`), using `scripts/run_tests.sh` with a scratch `HERMES_HOME`,
6 files ran: **48 passed, 0 failed**.
- `tests/tui_gateway/test_prompt_start_lock_order.py`
- `tests/agent/test_skill_index_triggers.py`
- `tests/hermes_cli/test_kanban_reopen_dispatch.py`
- `tests/tools/test_kanban_comment_writer_kind.py`
- `tests/tools/test_kanban_create_overlap_race.py`
- `tests/agent/test_worker_receipt_sequence_diag.py`

This pass did not rerun the other test files named in the feature docs.

## Feature docs

This pass adds 28 new feature docs and extends 8 existing ones. Each extension is a `## Follow-up …` section in:
- `kanban-pr-merge-time-acceptance.md`
- `kanban-worker-first-placement.md`
- `kanban-red-ci-failure-owner.md`
- `kanban-dependency-review-continuation.md`
- `kanban-create-overlap.md`
- `kanban-queue-reasons-repin.md`
- `sessions-mute-live-owner.md`
- `kanban-respawn-guard-hold.md`

They are indexed, together with this report, in README § Mirror follow-ups 1822–1918. Each doc lists its patches,
behavior, code, and the tests that landed with the source commits.
