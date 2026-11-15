# Pool idle under the LLM pacing governor

Symptom: pool cards sit `ready` while workers have free slots, and stall notices report 0 claims, sometimes with
"admission reason unknown". The governor (`hermes_cli/kanban_llm_pacing.py`, `hermes_cli/kanban_llm_pacing_dispatch.py`)
stops new claims on a provider while its pooled accounts burn faster than their remaining windows sustain.

## Check

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
their weekly % in the pace `detail`), in `DISPATCH_ORDER`, and it can re-hold within minutes. In one case, one 5-hour
window reset while 7 of 8 accounts sat at 7d 100%. The release ran 4 cards from the head of the queue, and cards 9th
to 31st waited another hour. Report the time the hold lifts, which accounts it adds and the card's position. Promise a
start only for a card inside what that headroom admits. The coach's `hold_lift_forecast` rule holds a reply that
says cards "start when Claude comes back/resets". When `until` is hours or days away, move the waited-on cards (below) instead of
reporting the wait: a `held` state is `limit 0` on every worker, and default-routed cards leave a provider by
themselves only on a wall, never on a pacing hold, so they sit until the reset. A `paced` peer with free slots claims
re-pinned cards within minutes.

Paced is not stopped. While each worker's `limit` is 1 or more, freed slots keep going out in `DISPATCH_ORDER`: effective priority, then cards with waiters, then oldest first. A p0 card can sit behind dozens of higher-priority cards while the fleet claims every few minutes. Report its position among the ready cards a worker can take (`status='ready' AND claim_lock IS NULL` with a `pool:`/`capability:` label in `runs_on`), never its rank in the hub's `hermes kanban queue`, which is a different order: evaluate `hermes_cli.kanban_db_dispatch_queries.DISPATCH_ORDER` against the replica with `~/.hermes/hermes-agent/.venv/bin/python` (the `hermes` launcher is a shell script, so its interpreter is not Python). Don't write "nothing claims". A low-urgency card that is waiting its turn needs no change. Bump it only under the lane-median rule.

**Move a waited-on card to a provider that has room.** Pacing is per provider: when its card is paced on one provider and another provider's `llm_pacing.json` state is neither `paced` nor `held`, or when its provider is `held` with an `until` hours or days away while a peer is `paced` with `limit` >= 1 and a free slot on some worker, switch the card's model on its home board with `hermes kanban set-model <id> <model> --provider <p> --no-mandatory`. Without `--no-mandatory`, a later wall on that provider holds the card instead of moving it. Read back a `claimed` event a few minutes later. Once that provider is `held` too, clear the pin (`hermes kanban set-model <id> none`) so the default route can choose another provider. Priority and re-placement do not lift a pacing hold.

**Only registered, eligible routes are alternatives.** Under `enforce_subscription_only`, cards (pinned or default-routed) run only on `model_routing.routes` entries with `eligible: true, funding_verified: true`. Read `hermes config get model_routing` before naming a fallback provider: a working login (`hermes auth list` shows Grok/Gemini) whose route is `eligible: false` is refused (`set-model` fails `model_pin_not_in_routing_registry`, oneshot fails `Managed subscription policy rejects unregistered ... pin`), and clearing the pin doesn't reach it. When every eligible provider is paced, the fix is verifying that route's funding and enabling it in the source of the routing block (fleet keys: shared-config on `state`), on a card. Don't flip eligibility by hand before funding is verified, because an unverified route can bill metered usage.

A paced fleet keeps many free slots, so the worker autoscaler (`free_slots` in its tick log) correctly launches
nothing. More machines can't raise LLM throughput, so say that instead of re-placing cards or asking for scale-up.
The capacity question already has owners: the `Model capacity: <provider> pool projected exhaustion (paced)` cards.
There is one per reset window, with idempotency key `model-capacity-pace:<provider>:<reset epoch>`. Their bodies forbid
buying capacity, enabling paid overflow, or asking David unless an authorization boundary is reached. Point to
those cards. Never turn a stall notice into a capacity-purchase ask.
