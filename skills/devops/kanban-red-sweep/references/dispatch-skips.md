# Ready card, no claim: dispatcher skip reasons

Read the skip reason from the notice or the worker log (`grep "<id>" ~/.hermes/logs/gateway.log` via
`hub-worker-exec <w>`), then fix that reason. A priority bump clears none of these.

## `skipped_ineligible(refusal_exhausted)`
The card has no unrefused provider left right now (refused providers are excluded for good).
- On the worker: `kanban_provider_walls.active_walls(now=time.time())` gives walled providers and when they end.
- For the remaining peers, check `live_pool_serving(provider, model)` and the pool entry
  (`_oauth_pool(p).entries()` inside `_home_secret_scope()`): `last_status=exhausted`, `last_error_code=402`,
  `failure_reason=billing` means the account is out of credit, which is a user top-up, not a bug.
- The card starts by itself when the wall ends: comment the end time. Don't bump, move or release it again.
- If the worker log's 429 names one model but the wall covers the whole provider, that is a quota/pacing defect:
  file it, because it holds cards routed to the provider's other, still-serving models.

## `worker_disk_below_floor`
The floor is the worker's config `kanban.admission.disk_free_floor_bytes`. Run `df -h /` and
`du -sh ~/.hermes/kanban/workspaces/*`, then look up each big card's status. Remove rebuildable build output
(cargo `target/`) only for cards that are not `running`, and only after `lsof +D <dir>` shows nothing using it.
The card is claimed on the next tick. Note on the card that idle workspaces are never reclaimed.

## "label(s) machine:<w> carried by no machine"
The pinned worker has left the fleet. Confirm the PR head is on GitHub, then
`hermes kanban move <id> --pool --reason "..."`. Comment that the next run rebuilds the worktree from the pushed
branch, and that any review or create request queued from the dead worker must be looked up by its idempotency
key first and re-requested only once if it is missing.

## Spend hold that re-holds right after a release
- While the card holds an open stop, `spend-budget` refuses with `stop_decision_required`. Run `release` first;
  its `--runs` value is additional runs, not an absolute limit. Pass `--seen` only when a refusal asks for it
  (`seen_not_needed` otherwise).
- If `release` prints `Recorded renewal authority: <session>` and that is not this chat, retrying from here
  only repeats the refusal. `cross_session send` that chat the user's words, the gate facts you verified and the
  exact `release`/`spend-budget` commands, and ask for the claimed run id or the refusal text.
- A spend-reviewer release that keeps a cache-read cap below the card's cumulative usage re-holds the card
  straight away. Release from the owning chat so the cap is widened as well.
