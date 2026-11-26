# Is a "ready, no worker" card really unclaimed?

Start with `hermes kanban governors` (hub, a few seconds). It lists every hold with its card count and ids: lane
gate, labels/pickup, Board lease, open parents, scheduled. Then compare ready `pool:worker` cards on the hub with
running runs: dozens ready while the fitting workers sit at 0 active runs is a fleet pickup stall, not this card's
problem. Find its open fix card (titles with "pickup") and comment there; don't bump this card's priority again. A gated lane's held cards count as "waiting for a worker"
(green) on the Desktop strip, so a big green number is usually lane-gate holds (`hermes kanban lanes show` /
`lanes held`), not stuck ready cards. Read it before digging through the Desktop source or the per-card `why`.

Read the card's newest `task_events` before anything else. `respawn_guarded` with `active_pr` or `shared_blocker`, plus `dispatch_skipped labels_mismatch` on every tick, means the card is held until its PR's CI finishes. The Desktop still shows "ready · waiting for a worker to claim it", but no worker could take it. The dispatcher comment that goes with `shared_blocker` names the stuck run, e.g. `runners-offline.macos-latest`. Answer "why isn't it claimed" from the CI side:
- `gh pr checks <n>`: lists the PR's pending checks.
- `gh api "repos/<o>/<r>/actions/runs?status=queued&per_page=100"`: shows the hosted-runner backlog. Also query `pending` and `in_progress`.
- For each queued macOS run, compare `head_sha` with the branch's current head and the PR's state. A run on a closed PR, a superseded head or an old main push only takes a runner slot. Cancel it with `ci_run_cancel.py --run-id <id> --gh-repo <o/r> --expect-attempt <attempt from gh run view --json attempt>` (skill github-ci-wait). A plain `gh run cancel` is refused by the CI cancel guard.
- A backlog that keeps coming back usually means workflow `paths:` filters fire on unrelated crates. That is one CI card per repo: narrow the PR paths and keep the full build on main and in the merge queue.
Use `hermes kanban why <id>` for the board-rule view. There is no `diagnose` subcommand.

If the newest wake says the parking gateway stopped reporting its probe, check the recorded condition before seeking a worker: this is a watchdog wake, not producer completion. For a remote-card wait, run `hermes kanban wait-check task <producer> --gateway <home>` and inspect its output (a returned `todo` is not completion). If still unfinished and the waiter is unclaimed, re-register the wait on the live gateway:
`hermes kanban schedule <waiter> '<verified remaining condition>' --awaiting '{"kind":"card","ref":"<producer>","gateway":"<home>","board":"default"}' --wake-check 'hermes kanban wait-check task <producer> --gateway <home>' --check-every 900`.
Read back `status=scheduled`, the exact probe and `next_probe_at`; this verifies registration, not a future successful probe. Preserve the sole producer and placement rather than creating a replacement or raising priority for work that still awaits its producer.

The same few cards showing `claim_not_acquired` for hours in the workers' `~/.hermes/logs/gateway.log`
(`kanban dispatcher stuck ... Held cards: <id>=claim_not_acquired`), while those workers claim other cards with the
same labels, means the Board lease refuses those specific cards. It is not a capacity problem. `BS-ready-worker-free`
stays silent here because a recorded skip explains the wait. First check that each card is still wanted: a
fix/decision card whose target is already `done` is moot, so comment and archive it. File the lease refusal on a
dispatcher card for the rest.

The hub's `why`/`kanban_show` often says only `labels_mismatch`, which is normal for the hub and is not the real refusal. Get each worker's own verdict in one call: `~/.hermes/bin/hub-worker-exec all --timeout 30 -- hermes kanban why <id>`. The line `stopped by Board lease … origin=local, reason=<x>` names the refusal. Moving the card to `pool:worker` does not fix a refusal that every worker gives.
- `reason=usage_accounting_pending`: admission sees unresolved usage, not necessarily an absent final receipt for the latest run. Get the exact pending UID census with `kanban_usage_accounting.pending(conn, <id>)`, opening read-only (`file:…?mode=ro`, `uri=True`) the card's home `~/.hermes/kanban.db` or any host's Board replica `~/.hermes/kanban/sync-shadow/<board>.db`, then reconcile authentic carriers and immutable identities. Another gateway's own `kanban.db` may lack the card, and `pending()` returns `[]` for a missing row: select the task row first, or absent debt looks settled. Interrupted/restart markers can precede a later final; product exit0 is not a usage receipt, and an existing final carrier does not prove settlement. This is a money path: never reset history or renew spend to get around it. After a core update, re-read the current claim and worker-binding events before pursuing recovery: admission can resume while debt remains. Distinguish authentic receipt settlement from a gate changed to observation-only; a running card proves neither repaired accounting nor final acceptance. Find the open accounting-repair card (search titles for "usage accounting") and add the run ids there in one comment. Then add one comment to the stuck card that names the repair card, so its owner stops re-placing it.
  - When the user says the whole fleet is stalled ("only N slots used"), take this census over EVERY ready card on the Board replica before you chase worker versions. Tally the pending runs by whether they have a `worker_result` and by `outcome`. If most ready cards are held and their runs lack results (continue/block/schedule/retain/reclaim exits), the accounting gate explains the stall. Inspect the original producer store and outbox before calling a receipt absent: a replica census alone cannot distinguish missing production from undelivered evidence. Check whether the repair card is held too (`pending()` on its own id); if so, relaying to it is a deadlock. The fix is a core change to the gate (worktree, tests, independent review, CI, ship), and the ask-gate treats it as agent work, not a decision for David. Keep the budget and decision holds unchanged.
- A worker `why` that prints `<id>: no such task` means the pickup never copied the card, so no claim gate ever judged it. Measure where it sits in the pickup order on the worker replica: `w.candidates(w.open_replica(connect()), own)` passed through `kanban_dispatch_priority.order_with_remote_waiters(conn, …)` (`hermes_cli.kanban_worker_node`, `kanban_db_connect.connect`, run with `~/.hermes/bin/hermes-core-python`, ship the script base64-inline through `hub-worker-exec`). The pickup copies only `DEFAULT_PICKUP_BATCH` (4) cards per tick in that order, and aging outranks priority, so a priority bump rarely moves a card into the window. If the cards ahead are ones the local claim refuses (e.g. tally `pending()` over the candidates), they fill the window every tick and the workers sit idle (`dispatcher stuck … 0 workers spawned`). That is a missing pickup pre-filter in `_evaluate` (siblings: `_paced`, `_quota_held`): fix the pre-filter. First check whether the fix already shipped: when only some workers log `dispatcher stuck`, run `git merge-base --is-ancestor <fix sha> HEAD` on each, and read that worker's `journalctl --user | grep deploy-check` (`N shipped commit(s) ... NOT live` names the missing fixes; `update deferred — N busy ... waited Nm` means an always-busy worker can't take its idle update). Then the remedy is that worker's core update (skill worker-exec, "Core update on a worker that is never idle"), not new code. `move --runs-on machine:hub` is refused by hub admission when the reason is capacity.

A board with its own start gate (a launchd gate that admits at most `max_open` cards, e.g. the bb board's `bb_gate.py`) can stall the whole board even when no Hermes hold is open. Run the gate's `status` first. If it shows `holds: []` and `active == max_open`, but `hermes kanban list --status running` is empty and the gate log's last `started` line is hours old, then the "active" cards are `ready` cards that the local claim refuses (`dispatch_skip claim_deferred`). The gate counts them, so every card queued behind them waits forever. Confirm it by running `kanban_usage_accounting.pending(conn, <id>)` on each active id and on the waiting card: the active ids return run ids and the waiting card returns `[]`. The fix belongs in the gate: claim-refused ready cards must not count toward `max_open`. It is one fix card on that gate. Do not bump the waiting card's priority, and do not settle the usage debt.

`Held cards: <id>=decision_pending` in those worker logs means an open spend-stop episode still holds the card in the
workers' view, while the hub list shows `ready` and `hermes kanban why` says `triage: nothing holds it`. The hub's
`kanban_show` queue note (labels_mismatch, "do not re-place") hides this, so grep the worker logs first. If the stop
is already superseded (re-scoped, budget set, parent done), the owning chat runs
`hermes kanban promote <id> --seen <newest comment id> --dry-run --json "<what changed>"`, then the same without
`--dry-run`. If `hermes kanban release` refuses with `budget_decision_unread` ("Budget decision comment unavailable"),
re-running sync does not help; use promote. Then confirm a worker claims it (spawned/worker_bound event).

## Release succeeds, then immediately reholds on old spend

When zero new runs still exceed a newly granted allowance, inspect the current stop, latest `spend_budget_set` events and checkpoint instead of repeating promotion. Compare `spent.runs`, `since`, and effective limits: historical scope restrictions can deliberately retain cumulative accounting despite a promotion's fresh-window marker. In that case, represent additional runs as retained count plus authorized allowance, not the allowance alone.

1. Trace cache-limit provenance separately. A reviewer writing `cache_read_tokens:null` can restore the board default and lose earlier explicit agent attribution. A release refusal saying the cap *may* be human-set is not proof a human imposed it. Inspect the prior explicit event; preserve actual human or unresolved limits.
2. When the owning chat has existing full-goal authority and records establish an overwritten agent-set limit, use supported `hermes kanban spend-budget <id> --runs <retained-plus-authorized> --cache-read-tokens <verified-prior-limit> --reason '<provenance and unchanged goal>' --json`. Compute the total with a tool. Zero means unlimited: restore it only when that exact prior limit and authority are evidenced, never as a generic unblock.
3. Satisfy this command's own read-first gate: read the records it prints, then retry with its exact requested `--seen` identifier. It may require a budget **event** ID; a numerically newer comment or the acknowledgment used by `release` is not interchangeable.
4. Read back effective limits and retained spend. Run supported `hermes kanban release <id> --runs <authorized-additional>`, satisfying its separate read-first gate, including the newly recorded budget decision. Verify release receipt, persisted state and eventual claim separately. Ready without an immediate rehold establishes release, not execution.
5. Put the reproduction on the existing spend-review producer owner: preserve explicit cache provenance and reconcile fresh-round grants with restricted cumulative accounting. For this reconciliation, retain historical usage and global defaults; the budget correction does not settle unknown receipts or release independent accounting gates.

The hub's local kanban.db can keep showing a card as `ready`, with no spawned/run_route/worker_bound event, while a pool worker already holds the Board lease and is running it. Board-lease claims can stop reaching the hub replica. Before bumping priority, re-placing or filing a stall card, check the workers themselves:

```sh
~/.hermes/bin/hub-worker-exec all -- 'ls -t ~/.hermes/kanban/workspaces | head -4; ps -eo lstart,args | grep -m2 <card id>'
```

(`hub-worker-exec` needs `--` before the command.)

- A fresh workspace dir, a worktree named after the card, or a live process means the hub view is stale and the card is running. Comment that on any open stall or dispatcher card. Do not re-place it or reprioritize it.
- Note which workers answered. Missing workers mean the real capacity is below the board's reported slot count ("6/16 busy" can really be 2 live machines).
- Workers' own `~/.hermes/logs/agent.log` lines `claim_deferred: lease_held|not_claimable` show the claim activity the hub replica is missing.
- `hermes kanban dispatch --dry-run --json` returning only `skipped_locked: true`, with every list empty, is no verdict: a tick held the lock. Do not read it as "nothing to do". Do not treat it as a side-effect-free preview either: a run of it has spawned ready cards. Use `governors`, `why` and the candidate receipts to inspect.
- A gateway shutdown writes thread stacks to `~/.hermes/logs/gateway.log`. Find the dispatcher watcher thread (`_dispatch_once_locked`) to see where a tick was stuck.

## Hand fix for a long ready wait (once the hub view is confirmed)

1. Why nothing claims it: the per-pass refusal journal on each machine that could claim it,
   `cd ~/.hermes/hermes-agent && .venv/bin/python -m hermes_cli.kanban_candidate_receipts --db ~/.hermes/kanban.db
   --task-id <id> --limit 2000` (tally `worker`/`gate`/`reason`). Only `labels_mismatch` while every machine with
   the label is `pinned_only` (`hermes_mail.py admin machines`) means no machine can ever claim it.
2. Before re-placing it, check that the work is still wanted: read the newest comments on the card and on its
   parent/implementation card for a "superseded" or "close this" note, and look for a user waiver or a receipt card
   that already did the check. Re-placing a superseded card starts a worker on live Desktop or checkout work nobody
   needs.
3. Re-place it: `kanban move --runs-on machine:<mac>` is refused (`placement_unavailable`) when that machine only
   picks up its own gateway's cards. Use `kanban_create(placement="<mac>")` with the same body; it is refused as
   `create_overlap` until you pass `distinct_from=[<old>]` with the reason. Archive the old copy once the new task id
   appears (`hermes-hub requests` shows the queued create).
4. A card that lives on another gateway: `kanban_comment` / `kanban_archive` from the hub are refused
   (`foreign_card`). Run `hermes kanban archive <id>` on its home host (`tailnet_exec`); that also stops its worker.
5. The systemic gap (labels nothing can claim) is one card: search every board and ask the chat working on it
   before filing, because another chat may file it within the same minute.
