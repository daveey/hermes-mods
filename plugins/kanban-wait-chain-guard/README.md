# Kanban wait-chain guard

A card that can't finish tends to link itself behind another card and park. When that other card is itself parked
behind a third, you get a chain of watchers and no doer. The tools used to answer each park with a plain `ok`, and
agents reported "an owner has it".

This plugin hooks `kanban_link`, `kanban_block(kind="dependency")` and `kanban_block`/`kanban_schedule` with
`awaiting={"kind": "card", "ref": ...}`, including nested deferred `tool_call` batches. It reads the board
read-only. The card waited on is **stalled** when it is `todo`/`scheduled`/`blocked`/`triage` and either

- waits on an open parent that is itself parked, or
- has made no progress for 24 h.

Progress means a `created`, `completed`, `review_requested`, `changes_requested`, `edited`, `specified`,
`goal_verified`, `pr_acceptance` or `assigned` event; a run that ended `completed`/`review_requested`/
`changes_requested`; or a `linked` event where the card linked itself behind a new parent (the link key means
re-parking behind the same card never writes another one). When the card has an open parent that is active
(`running`, `ready`, `review`), its `dependency_wait` events and that parent's own progress and latest run start
count too: waiting on a doer is a handoff. Wakes, unblocks, claims, other runs, heartbeats and comments don't
count, and a `dependency_wait` behind a parked parent is the watcher loop, so it doesn't either.

- Stalled parent: the result gets a `warning`, e.g. `t_B is itself waiting on t_A (scheduled, idle 120h); ...
  parking here is not a started fix: do the step yourself, re-place the card, complete it with what is proven
  (NO_PROOF), or file the one decision for David`.
- Stalled parent **and** the waiting card already has 20+ runs since its own last progress: the call is refused
  with the same text.

Never flagged: a running/ready/review parent, a fresh pipeline behind a running head, and a parent that is David's
blocked ask (`human_request` set) or whose chain ends at one. Waiting behind the ask card is the prescribed pattern.
Cross-gateway `awaiting` refs (`gateway` set) are skipped. A missing board or any DB error fails open.

Install with the repository installer, or link this directory into `$HERMES_HOME/plugins/kanban-wait-chain-guard`
and run `hermes plugins enable kanban-wait-chain-guard`. Undo: `hermes plugins disable kanban-wait-chain-guard`.

Tests: `python3 -B -m unittest discover -s plugins/kanban-wait-chain-guard/tests` from the repo root.

Origin: self-heal defect agent-ff9c2f154c. A shepherd card ran 141 times behind a todo incident card, which waited
on a scheduled coordinator, which waited on an unrelated admission card.
