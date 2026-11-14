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
   for `dispatch_skipped` rows misses them. When every candidate worker logs `llm_paced`, priority and re-placement
   change nothing.
2. **Split claims by gateway before reporting that nothing claims.** A machine whose core predates the governor
   keeps claiming the cards pinned to it on the paced provider, and can spend the quota the governor is saving. On
   it, `git -C ~/.hermes/hermes-agent cat-file -e HEAD:hermes_cli/kanban_llm_pacing.py` fails and
   `~/.hermes/llm_pacing.json` is absent. Never re-place a held card onto such a machine to get it started.
3. **Read the provider state** in a governed host's `~/.hermes/llm_pacing.json`: `paced` with a `factor`, or `held`
   until a time, plus the per-account usage behind it. At a factor near 0.03 the pool claimed nothing for hours.

4. **A freshly signed-in account isn't on the workers yet.** The hub copies new pool rows to the workers only when its
   `hermes-oauth-relay.timer` fires (up to an hour later), so the workers' governors keep pacing on the old walled
   accounts in the meantime. After any `hermes auth add` on the hub, run `systemctl --user start hermes-oauth-relay.service`
   there. Its journal should show `fan-out worker-N: X/X rows match`. Then confirm the label on every box with
   `hub-worker-exec all -- <script listing credential_pool labels>`. `claimed` events in the replica should
   return within minutes.

## Report

A paced provider has no start time. Claims resume when the pool's burn falls below what the accounts with headroom
sustain; an account's weekly reset adds headroom but doesn't set a time. Quote that condition and the next resets,
never one account's reset as the ETA. A `held` provider waits for its `until`.
