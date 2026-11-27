# Auditing scheduled (parked) cards

Use this when he quotes a count of cards "waiting on CI" or "waiting on external" (the lanes strip's pink pip),
or asks whether parked cards' waits are legitimate.

1. **The pink pip is not a CI count.** It counts scheduled cards, so most of the pink cards are usually parked on
   another card, a clock or an outside event. Split the scheduled cards by park `awaiting.kind` (card, ci, deploy,
   clock, external, chat) and lead with that table, so his number reconciles.
2. **Where the park lives.** Read the newest `scheduled` event, **or** the newest `continued` event whose payload
   has `status: scheduled`. Re-parks are written as `continued`, and a query that reads only `scheduled` events
   reports their `awaiting` as missing.
3. **Replica files.** Glob `kanban/sync-shadow/*.db` but skip `*.lock.db`, `*.init.lock.db` and history files,
   because they have no `tasks` table. Open them with `~/.hermes/bin/hermes-core-python` (sqlite3 is guarded).
4. **Check each ref live.** For a `ci` ref, run `gh run view <id> --json status,conclusion`; for a `card` ref,
   read that card's status; for a `deploy` ref, check whether the PR merged and its rollout window. A park whose
   ref has already finished sleeps until its backstop: comment the outcome on it, then `kanban_unblock` it. The
   unblock can be refused because the card woke itself meanwhile, so read the status back.
   For a dependency-gated `todo` card, compare the latest `dependency_wait.reason` and checkpoint with the
   actual parent edge. A parent can have delivered the specific prerequisite while continuing unrelated
   acceptance work; its open status alone does not prove the child still needs to wait. Independently verify
   the named artifact (for a PR, `gh pr view <n> --repo <repo> --json state,mergedAt,mergeCommit`), check for newer
   restrictions, and record why it satisfies the child's exact prerequisite. Then use
   `kanban_unlink(parent_id=<parent>, child_id=<child>)` only for that satisfied edge; leave the parent's own
   acceptance and the child's remaining gates intact. Read back the child's status and actual claim/process
   binding before saying execution resumed. If the prerequisite requires the whole parent's acceptance, keep
   the edge. This prevents an artifact-level wait from silently becoming a wait for an expanding task.
5. **Wrong-kind parks.** Look for `kind=clock` parks whose `why` names a reviewer card, a PR or a run. They don't
   wake when that event happens. The legitimate kind there was `card`/`ci`. Count them as a root cause, separate
   from the miscount.
6. **Stale shadow rows.** Before counting a card on a shadow board, confirm it with
   `hermes kanban --board B show <id>`. A `sync-shadow` row can still say `scheduled` long after its home archived
   the card, and lanes and pip counts read that row.
7. **Verdict table.** Give one row per card: verdict, what it waits on, the ref, how long it has been parked, its
   backstop, and a note. He wants the full table, not a summary; deliver it as a file when it runs past ~40 rows.
