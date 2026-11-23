# Is a "ready, no worker" card really unclaimed?

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
