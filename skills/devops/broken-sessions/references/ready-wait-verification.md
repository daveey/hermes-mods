# Is a "ready, no worker" card really unclaimed?

Start with `hermes kanban governors` (hub, a few seconds). It lists every hold with its card count and ids: lane
gate, labels/pickup, Board lease, open parents, scheduled. A gated lane's held cards count as "waiting for a worker"
(green) on the Desktop strip, so a big green number is usually lane-gate holds (`hermes kanban lanes show` /
`lanes held`), not stuck ready cards. Read it before digging through the Desktop source or the per-card `why`.

The same few cards showing `claim_not_acquired` for hours in the workers' `~/.hermes/logs/gateway.log`
(`kanban dispatcher stuck ... Held cards: <id>=claim_not_acquired`), while those workers claim other cards with the
same labels, means the Board lease refuses those specific cards. It is not a capacity problem. `BS-ready-worker-free`
stays silent here because a recorded skip explains the wait. First check that each card is still wanted: a
fix/decision card whose target is already `done` is moot, so comment and archive it. File the lease refusal on a
dispatcher card for the rest.

The hub's local kanban.db can keep showing a card as `ready`, with no spawned/run_route/worker_bound event, while a pool worker already holds the Board lease and is running it. Board-lease claims can stop reaching the hub replica. Before bumping priority, re-placing or filing a stall card, check the workers themselves:

```sh
~/.hermes/bin/hub-worker-exec all -- 'ls -t ~/.hermes/kanban/workspaces | head -4; ps -eo lstart,args | grep -m2 <card id>'
```

(`hub-worker-exec` needs `--` before the command.)

- A fresh workspace dir, a worktree named after the card, or a live process means the hub view is stale and the card is running. Comment that on any open stall or dispatcher card. Do not re-place it or reprioritize it.
- Note which workers answered. Missing workers mean the real capacity is below the board's reported slot count ("6/16 busy" can really be 2 live machines).
- Workers' own `~/.hermes/logs/agent.log` lines `claim_deferred: lease_held|not_claimable` show the claim activity the hub replica is missing.
- `hermes kanban dispatch --dry-run --json` returning only `skipped_locked: true`, with every list empty, is no verdict: a tick held the lock. Do not read it as "nothing to do".
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
