# Pool idle under the LLM pacing governor

Symptom: pool cards sit `ready` while workers have free slots, and stall notices report 0 claims, sometimes with
"admission reason unknown". The governor (`hermes_cli/kanban_llm_pacing.py`, `hermes_cli/kanban_llm_pacing_dispatch.py`)
observes pool burn and stops claims for genuinely held pools. Check effective `kanban.llm_pacing.pace_starts` before interpreting `paced`: current default is false, so paced burn alone imposes no admission limit or claim-time pace reroute. Only an explicit true restores proportional start limits. Do not re-enable those limits to manufacture a rollout acceptance event; reconcile old paced-trigger criteria with the current approved policy. Read actual route payloads by schema: `run_route` uses `provider`/`model` on current cores, while `worker_result` uses `route_provider`/`route_model`; `selected_provider` alone can miss real runs.

## Check

On reserve-only installed cores, first read `kanban_llm_pacing_dispatch.reserve_hold` and execute it against the existing snapshot. Such cores removed `pace_starts` and proportional start limits entirely: the authoritative hold is fresh `v2.pools[provider].at_reserve=true`, not legacy `pace.state=paced`. Corroborate `at_reserve=false` / no hold with recent provider-specific worker results before attributing idle slots to capacity. Repeatedly waking an old capacity incident at individual account resets does not fix an agent-owned fault when the collector is advancing and provider runs succeed. Report the obsolete alert premise separately from literal legacy `open` recovery; do not claim the latter or silently waive the original owner's acceptance. The proportional-limit guidance below applies only to older installed cores.

Scope failed worker results to their recorded route model before calling a whole provider unavailable. When recent worker results cover only a rejected model, inspect existing natural cron/desktop sessions on that provider for actual assistant/tool exchanges and persisted usage. A successful alternate-model session plus fresh reserve admission proves partial provider service, not recovery of the rejected model, every gateway, or literal legacy `pace.state=open`. Use this distinction to close a provider/reset investigation without altering dispatch; never manufacture a fresh usage reader or paid probe merely to resolve a legacy alert.

1. **Who is held, and who still claims.** On the hub, read the Board replica; it holds every gateway's events with
   the gateway that wrote them:

   ```bash
   sqlite3 -readonly ~/.hermes/kanban/sync-shadow/default.db \
     "select origin_gateway, kind, count(*) from task_events
      where kind in ('llm_paced', 'claimed') and created_at > strftime('%s', 'now', '-2 hours')
      group by 1, 2 order by 1, 2"
   ```

   Pacing skips are their own event kind, `llm_paced` (payload `reason`, `detail`, `providers`, `until`); a query
   for `dispatch_skipped` rows alone misses them. When every candidate worker logs `llm_paced`, priority and
   re-placement change nothing.

   For one waited-on card, read its own rows there (`select origin_gateway, kind, datetime(created_at,'unixepoch'),
   substr(payload,1,300) from task_events where task_id='<id>' order by created_at desc limit 8`): a worker can
   record the skip as `dispatch_skipped` with reason `llm_paced` and `limit: 0`. A card logs that skip once per
   reason, so its newest row can still say `paced` hours after the provider went `held`: take the provider's state
   from step 3 on every notice, never from the card's last skip. The hub's `kanban show` cannot tell
   you this for a pool card: it reports only its own `labels_mismatch` (the hub never leases pool cards) and
   "admission reason unknown". The newest rows also show whether another chat already changed the card's model or
   placement, so you don't make a second change.
2. **Split claims by gateway before reporting that nothing claims.** A machine whose core predates the governor
   keeps claiming the cards pinned to it on the paced provider, and can spend the quota the governor is saving. On
   it, `git -C ~/.hermes/hermes-agent cat-file -e HEAD:hermes_cli/kanban_llm_pacing.py` fails and
   `~/.hermes/llm_pacing.json` is absent. Never re-place a held card onto such a machine to get it started.
3. **Read the provider state** in a governed host's `~/.hermes/llm_pacing.json`, under `<provider>.pace` (`state`,
   `factor`, `until`, `detail`), not at the provider's top level: `paced` with a `factor`, or `held`
   until a time, plus the per-account usage behind it. At a factor near 0.03 the pool claimed nothing for hours.
   `hub-worker-exec <worker> -- <file>` runs the file as a shell script, so a `.py` file fails at `import`; pass a
   shell command or a shell wrapper around the Python.

4. **A freshly signed-in account isn't on the workers yet.** The hub copies new pool rows to the workers only when its
   `hermes-oauth-relay.timer` fires (up to an hour later), so the workers' governors keep pacing on the old walled
   accounts in the meantime. After any `hermes auth add` on the hub, run `systemctl --user start hermes-oauth-relay.service`
   there. Its journal should show `fan-out worker-N: X/X rows match`. Then confirm the label on every box with
   `hub-worker-exec all -- <script listing credential_pool labels>`. `claimed` events in the replica should
   return within minutes.

## Report

A paced provider has no start time. Claims resume when the pool's burn falls below what the accounts with headroom
sustain; an account's weekly reset adds headroom but doesn't set a time. Quote that condition and the next resets,
never one account's reset as the ETA. A `held` provider stops claiming until its `until`, but `until` is when the
hold lifts, not when your card starts. The release admits only what the accounts that reset then can sustain (read
their weekly % in the pace `detail`), through the installed dispatch ranking and admission gates, and it can re-hold within minutes. In one case, one 5-hour
window reset while 7 of 8 accounts sat at 7d 100%. The release ran 4 cards from the head of the queue, and cards 9th
to 31st waited another hour. Report the time the hold lifts, which accounts it adds and the card's position. Do not promise a
start from provider headroom or an advisory rank alone; require an actual claim receipt to report execution. The coach's `hold_lift_forecast` rule holds a reply that
says cards "start when Claude comes back/resets". When `until` is hours or days away, inspect the effective route before moving cards by hand: current core reroutes unpinned defaults, `review_unpinned` reviewers and non-mandatory concrete pins off a fresh stored hold beyond `kanban.llm_pacing.reroute_after_s` (default 3600), or an unknown reset, to an eligible unheld/unwalled same-class peer. Look for `default+paced:<provider>` (or the corresponding review/pin reason) in `run_route`; the persisted card pin need not change. Older core lacked this pacing reroute. Mandatory pins, class selectors and `require_model` cards retain their existing behavior, and a `paced` pool alone does not trigger this reroute. The destination remains subject to its normal pacing gate, so rerouting does not guarantee an immediate claim.

Paced is not stopped. While each worker's `limit` is 1 or more, freed slots are available through its current candidate ranking and admission gates. Check the installed ranking implementation rather than assuming raw SQL priority order is final. Before a priority change, compare the latest `priority_evidence.effective_priority` with `lane_median`, not `tasks.priority`: a p0 parent can inherit urgency from a waiting child and already meet the median. Consider a bounded bump only below that median; it still cannot lift pacing. For exact-card pickup evidence, use the eligible worker's `~/.hermes/bin/hermes-core-python` with read-only connections to its local board and full replica. Call `kanban_llm_pacing_dispatch.pickup_hold(local, replica, card)`: it records nothing and tests this card against that worker's admission. A foreign pool card can correctly be absent from the local board, and `kanban show` then has no current pickup explanation; that absence is not a replication defect. For candidate order, call `kanban_worker_node.candidates(replica, registry['me'])`, then `kanban_dispatch_priority.order_with_remote_waiters(local, rows)` and filter with `kanban_worker_node.eligible`. Treat `registry['me']` as the gateway-name string, not a machine record; read machine fields from `registry['machines'][registry['me']]` only after validating that entry. Verify one actual worker response before fan-out, and persist each worker's result as it completes so a gateway update cannot discard the entire collection. Do not use bare `DISPATCH_ORDER` as the final ranking: current cores apply `kanban_admission_score.rank` afterward. Label the result as that board's foreign-candidate order before quota/guard filtering, not a fleet-wide dispatch position or pickup ETA. Check the installed implementation when versions differ.

### Independent-review admission

Before unblocking `No eligible deep review model distinct from ...`, reproduce the review resolver's full eligibility calculation on the claiming worker. Candidate enumeration alone is insufficient: `independent_routes(...)` can list a model and `live_quota_wall(provider, model)` can return `None` while the pacing governor still excludes its provider. On cores using `held_pools`, inspect the installed signature and combine `active_walls(now=now)` with `held_pools(cfg.get('kanban', {}).get('llm_pacing'), now)` when `cfg` is the full loaded configuration; the resolver itself may receive only the kanban subsection. Preserve implementer exclusions. Re-read the resulting run after an unblock: a fresh `spawn_failed` means admission is still unresolved, even if the card returned to ready. This check prevents repeated retries based on an apparently free candidate that the actual resolver cannot use.

### Select and verify a fallback route

1. **Read the registry before choosing a provider.** Run `hermes config get model_routing` on an eligible worker. In the class-keyed shape, iterate `routes.items()` and then each class's list of route objects; print class, provider, model, `eligible` and `funding_verified`. Treat all-`None` rows as a parser-shape failure, not absent routes: the outer `deep`/`standard`/`fast` keys are classes, not provider records. Under `enforce_subscription_only`, use only same-class entries with both flags true. A working login alone does not establish an eligible or funded route.
2. **Restore capability routing, not a concrete fallback pin.** Re-read the card's status and overrides before acting so another chat's change is not overwritten. Clear stored overrides with `hermes kanban set-model <id> none`, or request a class with `hermes kanban set-model <id> class:<level> --no-mandatory` (level: deep, standard or fast). Remove body-level exact-model requirements separately. Leave routing unselected or request an optional `class:deep`, `class:standard` or `class:fast` preference, with provider omitted and never mandatory. Clearing with `none` clears model, provider and the mandatory mark. Older cores preserve the mandatory mark when replacing a pin with a class unless `--no-mandatory` is supplied; current pin-retirement cores clear the mark on either write. Inspect installed command help and read back saved fields; do not assume an older worker has the new behavior. Let the class allocator choose an eligible funded route under normal quota/pacing gates. For review, use the supported review handoff with genuine separate run/session custody; do not select another model identity as a substitute.
3. **Separate saved policy from execution.** Read back the class/no-selector policy, absent provider restriction and disabled mandatory setting. A row in a Board replica proves replication to that replica, not adoption by every worker. Require a worker-origin `claimed` event for this card and its resolved run route before reporting execution. Generic fleet claim counts prove activity, not which provider served it. Until then report only that the preference changed and the card remains queued. If every eligible route is held, use supported deferral; do not bypass quota, funding or billing gates.

When every eligible provider is paced, inspect the existing capacity owner before proposing another route. Verify its funding before enabling it in the routing source (fleet keys: shared-config on `state`), on a card; an unverified route can bill metered usage. Priority and re-placement do not lift a pacing hold.

A paced fleet keeps many free slots, so the worker autoscaler (`free_slots` in its tick log) correctly launches
nothing. More machines can't raise LLM throughput, so say that instead of re-placing cards or asking for scale-up.
The capacity question already has owners: the `Model capacity: <provider> pool projected exhaustion (paced)` cards.
There is one per reset window, with idempotency key `model-capacity-pace:<provider>:<reset epoch>`. Their bodies forbid
buying capacity, enabling paid overflow, or asking David unless an authorization boundary is reached. Point to
those cards. Never turn a stall notice into a capacity-purchase ask.
