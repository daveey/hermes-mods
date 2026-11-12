# Core update: upstream sync before the pause, and cron-turn accounting at checkpoints

Patches: **2640, 2661**.

## Behavior

- 2640: `hermes update` on a fork used to keep the install's gateways paused through the whole upstream sync: a network fetch of `upstream` (77 s on one blobless clone), the comparison, and, with `updates.fork_merge_upstream`, the off-tree merge, its conflict card and the CI-gate handoff. None of that writes a file of the live checkout. It now runs after the origin fetch and the pre-commit-point refusals, before the gateways pause. The upstream fast-forward (a real tree move) still runs paused, inside `checkout_move` bound to the upstream commit.
- Consequences to know: the conflict report and CI gate now run from the pre-update checkout's code; an update the pause later defers has already fetched upstream and filed or staged its merge. The merge is built on `origin/main` (where the origin pull leaves HEAD) when HEAD is behind or at it; when HEAD has commits `origin/main` lacks, it is still built on HEAD in the paused sync, as before. The paused sync never fetches upstream again. Direct callers with no prepared plan behave as before.
- 2661: a `cron_cut` instant core update tolerates confirmed Kanban workers plus the cron runs it cuts. An agent cron job also runs its turn on the session owner, so the gateway's work total counted it twice (scheduler run and authority turn). The checkpoint then saw one unit of unknown work and refused every attempt for the whole run (an hourly ~11 minute job; six refused attempts observed on one host). The agent turns of cron runs are now added to the tolerated count, under a `cron_cut` intent only.

## How it works

- 2640: `update_cmd_git` splits the sync into `_plan_upstream_sync` (offer remote, fetch, compare; returns an `_UpstreamPlan` with action `done`, `ff`, `merge` or `merged`), `_merge_upstream_into_fork` (merge-tree, conflict report, CI gate) and `_prepare_upstream_sync`, which runs the plan before the pause and, on the fork-merge path with an `origin/main` that HEAD is an ancestor of, also builds the merge there (`merged`). The plan is stored in module state and handed out once by `_take_prepared_upstream_sync` only for the same resolved root; `_prepare_upstream_sync` resets it every attempt. `_sync_with_upstream_if_needed` takes the plan (else plans afresh), downgrades `merged` to `merge` if HEAD is not the commit it was built on, then merges or fast-forwards as before. `_synthesize_upstream_merge` gained an optional `head`. `update_cmd._cmd_update_impl` calls `_prepare_upstream_sync` and passes the same `sync_upstream` flag on.
- 2661: `gateway.session_runtime_workers.cron_turn_count(runner)` counts executing authority turns whose frozen policy source is `cron`, each session once and only in a bucket the work total counts (running agents, unmapped executing sessions, open managed workers); an unreadable policy is not a cron turn (stays awaited). `update_checkpoint.checkpoint_for_update` and `run_shutdown_restart_wait` (new `_cut_cron_count`) add it to the active cron job count under `cron_cut`, for both the checkpoint verb and the restart wait's Kanban/cron cap.

## Code

`hermes_cli/update_cmd.py`, `hermes_cli/update_cmd_git.py`, `gateway/session_runtime_workers.py`, `gateway/update_checkpoint.py`, `gateway/run_shutdown_restart_wait.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_update_upstream_before_pause.py`, `tests/gateway/test_cron_turn_count.py`, `tests/gateway/test_update_checkpoint.py`, `tests/gateway/test_core_update_cron_cut.py`. Entry path: a real `cmd_update` over local git fixtures (no network) with a call-order recorder for fetch, conflict report, CI gate, pause and `merge --ff-only` (fork-merge conflict and clean cases, upstream fast-forward under `checkout_move`, take-once, rebuild when HEAD differs, no early merge when HEAD has local commits, disabled or foreign root); `cron_turn_count` over fake authorities; `checkpoint_for_update` and `_await_active_work_before_restart` with `cron_turn_count` stubbed (cut versus plain intent, extra unit that is not a cron turn).

**Untested**: a real network fetch, a real gateway pause with live sessions, `_push_synced_fork` (unchanged, still paused), and `cron_turn_count` against a real session authority and policy store.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The prepared plan is module-global state, valid for one update attempt in one process.
- If the pause defers the update, the upstream fetch and any staged merge or conflict card have already happened.
- `cron_turn_count` is deliberately conservative: it never exceeds what the total holds, so a miscount can only keep refusing, not waive real work.
- A plain (non `cron_cut`) intent still awaits cron turns for the full wait.

## Plugin

Core change; no plugin required.
