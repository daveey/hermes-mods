# Cards homed on another gateway

`no such task ... not imported here; last pickup skip: ...` on the hub means the card lives on the gateway that
created it (a worker's run filed it pinned to the hub). `hermes kanban edit` here fails with "unknown id" and a
plain twin `kanban_create` is refused `create_overlap`. Reach the origin box (`cross_task` read names the
gateway; `aws ssm send-command` with `sudo -u ubuntu -i bash -lc "hermes kanban ..."`, instance role,
us-west-2). A priority raise there is not enough when the skip count keeps climbing (top priority still sat
unimported for an hour): replace the card. Create a hub-local twin (`placement='local'`,
`distinct_from=[<id>]`, `distinct_reason` = it cannot import and is archived this turn, body = run the
original's body with all its limits, first step = stop if the original started), then comment and
`hermes kanban archive <id>` on the origin box and read back `archived`, so one owner remains.

A hub card parented on such an unimported card sits `todo` forever (`parent_missing` in its events, parent
absent from the hub's `tasks`): `kanban_unlink` it once its own acceptance is met or the twin exists.

## Red chats and acting on other hosts

"No red cards" from the hub's sqlite is not "no red chats". The sidebar paints a chat red from
`sessions.outcome_json` kind `decision` (hub `state.db`), fed by blocked cards on any gateway (the MacBook board
alone can hold many) and by chat-level asks (`us_*` item ids) that are not cards. `red_cards.py list` covers both.
Remote cards are commented and unblocked on their own host; `cross_task` only reads and comments. The Desktop
"For you" panel lists asks from every gateway; read another gateway's cards with `cross_task`, not this host's sqlite.
- Macs: `tailnet_exec` with the tailnet host (`mbp`, `mini`), not the gateway name.
- Workers: `~/.hermes/bin/hub-worker-exec worker-N -- bash -lc "hermes kanban comment <id> -- '...' && hermes kanban unblock <id>"`.
  A `foreign_card: ... was created on worker-aN and lives there` refusal on archive/edit is the same route:
  `hub-worker-exec worker-aN -- 'hermes kanban archive <id>'` (the `--` is required).
- Live counts across gateways (the inventory printed `unreachable ...`, or the report needs this turn's statuses):
  one read-only sqlite script looping over `kanban.db` and `kanban/boards/*/kanban.db`, copied to the hub and run
  there, on every online worker with `~/.hermes/bin/hub-worker-exec all --timeout 60 -- /tmp/<script>` (offline ones
  are listed and skipped), on each Mac over ssh, and locally.
- Hand-run a card's `wake_check` the way the dispatcher does (`bash -lc`): plain `ssh host cmd` lacks `~/.local/bin`,
  so a `token-broker`/`hermes` call fails FileNotFoundError (rc 2) where the dispatcher gets a real answer.

## Fleet inventory readers

`red_cards.py list` reads other gateways' live boards (beyond lagging replicas) only through readers configured in the monitor host's
`state/red-sweep-sources.json` (file missing: `unreachable fleet coverage-config-missing`).
For managed workers, check the monitor host's already-authorized instance-role SSM route before leaving a
placeholder unreachable reader. Discover running instances by exact configured identity, require a unique match,
and use GetCommandInvocation (ListCommandInvocations may be intentionally denied). A bounded read-only snapshot can
ship a reviewed pinned collector when the worker's installed CLI is older. Keep the whole reader below the inventory
caller timeout, bound delivery/execution too, compress stdout to stay within SSM's response cap, and fail closed on
truncated output. Never grant new IAM access or restart a worker to make inventory green.
