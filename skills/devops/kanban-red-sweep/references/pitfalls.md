# Pitfalls

Reference for skill `kanban-red-sweep` (moved verbatim from SKILL.md).

- A remote-homed card's replica row (`kanban/sync-shadow/`) is only as fresh as its home gateway's newest event: a
  sleeping Mac's heartbeats arrive late in a batch. Check that age first, then the host's own board, and report
  "unknown" rather than "stalled" when it is stale (`references/replica-freshness.md`).
- `gh` on the hub default profile is not logged in. Use `token-broker exec --scope user/github -- gh ...`.
- `gh pr edit` can fail on a deprecated-Projects GraphQL error and change nothing. Use REST and read it back:
  `gh api -X PATCH repos/<o>/<r>/pulls/<N> --input body.json`, `gh api -X POST repos/<o>/<r>/issues/<N>/labels -f 'labels[]=<l>'`.
- Released links (`released=1`) look linked in a raw join. Filter `released=0`.
- A `startup_orphan_reap` session owns no chat work, yet still wakes on its cards' notices and publishes For-you
  asks. A `decision_session` row with choices for a card means the user can answer it any second: leave that card to him
  unless he told you to decide; then write only behind a guard that aborts on any event or comment since the ask.
  The row vanishes once the owner session starts another turn (outcome `running`), yet the ask stays open until the
  card leaves `blocked` (archive logs `request_withdrawn`): `decision_sessions=0` does not mean no live ask.
- Don't publish the "may I sweep?" question as an approval tile first: this skill is the approval.
- Write the sweep report as plain markdown. A `::user-step{...}` directive in a reply publishes its text as a
  For-you request, so a status table inside one becomes a fake ask that has to be withdrawn.
- "No red cards" from the hub's sqlite is not "no red chats", and a remote card is acted on on its own host. Before
  reading or writing another host's cards or hand-running its `wake_check`, read `references/remote-homed-cards.md`.
- A chat still painted red after its cards finished: run `hermes sessions outcome <id> --refresh` on its gateway. It
  re-derives the outcome from live cards, so a decision about a done card becomes `done`. Never edit `state.db`.
- Chat-level asks (`us_*` extracted user-steps, For-you requests) can only be withdrawn by the chat that owns them;
  `action_request` withdraw acts on the calling session only. Send that chat (reaped ones too) a self-contained
  `cross_session send` that quotes the user's delegation and names the exact asks; it re-tests and withdraws. Read the reply.
- The For-you list injected at turn start can lag a withdrawal. The `withdraw` result's `state`/`withdrawal.at` is the
  live answer; check it before re-withdrawing or telling the user something waits on him.
- `kanban_create`/`move` refuse a machine on Board hold (`machine_held`). `hermes-mail admin machine-audit <m>` on the
  hub shows the hold reason and release time. A hold is deliberate, so never clear it to place a card.
- A sequential core rollout (one worker at a time) moves its hold to the next worker minutes after the last one
  lifts. Before moving or unblocking a card onto a worker, check that worker's hold live: `kanban_pickup_skips`
  rows with reason `hold` in the last 5 min, or `claim_deferred(held)` in its gateway.log `dispatcher stuck` line.
  Re-place ready cards pinned to a newly held worker. Leave cards whose worktree there has unpushed commits (their
  last `worker_result` says so): they resume when the hold lifts.
- "Pickup held on <m>" and "Pickup queue full on <m>" alarm cards are pinned to the stalled machine itself, so no agent
  works them while it is held or full. Re-place the waiting cards yourself. List `ready` cards whose `runs_on` names
  `machine:<m>` on each board. Find free slots on each target: `max_in_progress` in its `kanban:` config minus its
  running count. `kanban move` the portable ones to an unheld worker with free slots, then read back and comment why.
  Leave ship-stage cards whose exact-head approval was recorded on that host, because cross-host review custody can
  refuse the ship. Read the hold's owner card (rollout or core-drain) for its hard release time.
- An exact-head approval can be stale for landing. `hermes-core-wt ship` rebases, which makes a new head that the
  approval no longer covers, whenever main changed any PR file since the merge-base. Before shipping or re-placing an
  approved PR's card, intersect `git diff --name-only $(git merge-base HEAD origin/main) HEAD` with the same diff
  against `origin/main`. On overlap, the card needs a rebase plus a new review, so review custody no longer pins it to
  the old host: move it to a worker with free slots, and have the new review pinned to that worker.
- Finding a PR's owner card, the open-PR owner census and restoring an archived owner: `references/pr-owner-cards.md`.
- Count a worker's free slots before moving cards to it: `max_in_progress` minus running, and check
  `kanban_pickup_skips` for recent `budget_exhausted` rows. One free slot with several contenders is not capacity.
- A burst of `rate_limited` runs (`HTTP 429 ... quota unavailable for openai-codex/...`) is not an outage by itself.
  Read the `run_route` events. A `tier_reason` of `default+wall:<provider>` or `+quota_reroute` means later spawns
  already go to another provider. A host whose core predates first-spawn wall routing wastes only the first spawn of
  each new card; the quota-wall alert card names the fix commit, so check it with `git merge-base --is-ancestor`. Count
  `gave_up` events and check the fallback pool's weekly headroom (resetwatch `probe.py --fresh`) before calling it harm.
- A card that stopped `capability_unavailable` on a guard's "unreadable"/"cannot read complete ... records" refusal
  (state-push-guard, ship-only-guard) may only have hit GitHub's GraphQL quota: those guards read through
  `gh api graphql` and report any failed read as unreadable. Test with a real call,
  `gh api -i graphql -f query='{viewer{login}}'`, and read `X-RateLimit-Used`/`-Reset`; `gh api rate_limit` can show
  thousands remaining while the real call is refused. If exhausted, wait for the reset, confirm a 200, then
  `release` with a note: publish the already-validated head without re-running its tests, check the quota before
  the push, and wait for `resetAt` inside the run instead of stopping (no bypass env, no guard edit; any other
  refusal text is quoted and stops). Find the burner in `~/.hermes/logs/gh-calls.log` (tab-separated epoch, caller,
  parent command, gh args): count the last hour by caller and by args with PR numbers masked.
- A card missing from a board isn't absent. Its row, events and run results live on the gateway that created it, not
  on the machine that ran it; `cross_task read <id>` finds it there.
- `notify-subscribe` from a Desktop chat warns "binding a different chat" and suggests `agent:main:local:dm:<id>`.
  Ignore that suggestion and keep the bare session id: following it writes a shepherd row for an id that is not a chat.
- A sweep report that relays an ask owned by another surface (BB Inbox, another chat) gets turned into a new For-you
  request owned by your chat by the user-step extractor. Right after the reply, withdraw it (`action_request` withdraw),
  giving a reason that names the owning surface, so the user isn't asked twice.
- An old ask missing what unblock and archive mean: amend it in place with `hermes kanban block <id> "<same reason>"
  --human-request "$(cat hr.json)" --metadata-only` (prints `Classified ... (metadata only; remains parked)`). Read the
  current human_request and block reason first (newest `blocked` event payload), so only the action changes.
- Re-read each card's status and new events inside the script that writes to it; skip it if another chat changed it.

- Before re-parking a linked card for days or ending a sweep chat, read `references/sweep-chat-status.md` (why the chat stays grey).

