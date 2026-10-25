# Clearing blocked cards yourself

Classify each blocked card by the event that stopped it (`select kind,payload from task_events where task_id=? and kind in ('blocked','gave_up','crashed') order by id desc limit 1`), not by its body.

| Stop cause | Action |
|---|---|
| `gave_up` with a provider 429 / "usage limit resets in ~Nh" tail | Repin to a working subscription model (`hermes kanban set-model <id> <model> --provider <p>`), after a one-line live check of that model (`hermes chat -q "Reply with exactly: OK" -m <model> --provider <p> --quiet`). Also repin queued `todo`/`scheduled` cards pinned to the walled provider, or they fail on dispatch. If the gateway's root model has an empty fallback chain, add one: `hermes config set fallback_providers '[{"provider":"openai-codex","model":"gpt-6-astra"}]'`, then `hermes fallback list`. |
| `needs_input` saying "continuation limit reached" | This is an agent issue, not a user ask. A plain `hermes kanban unblock` is enough for a first exhaustion, because the blocked run breaks the consecutive-continuation streak. If the card has exhausted before, or its checkpoint's NEXT list is still large, split the remaining items into bounded slice cards instead. Give each slice one existing worktree (no two slices editing the same tree), a body that says read the checkpoint and do not redo finished phases, and an exhaustion instruction the card can actually follow. A goal_mode card's worker can't block kind=transient: the kernel rejects it, and the worker falls back to needs_input, which paints the session red again. So either create slices without `--goal`, or tell goal slices "if continuation runs out, block kind=time with the checkpoint". Then `hermes kanban link <slice> <root>` for each, comment on the root that it now owns only integration, review, ship and live acceptance, and unblock it. The root goes `todo`, which counts as active and not human-blocked, so the session stops showing red. |
| `gave_up` "exited cleanly without kanban_complete" (protocol violation) | Read the tail of `~/.hermes/kanban/logs/<id>.log`. If runs die at ~60 s on a heavy test command (`[exit -15]`), comment: run heavy tests backgrounded or memory-capped, single fork, never broad `pkill`/`kill 0`, checkpoint by comment after heavy steps, and always end with a terminal kanban call. Then unblock; the worker re-verifies its prior work. |
| `capability`: the profile can't write that code | `hermes kanban reassign <id> default --reason …`, then unblock. If a worker created the card (`creator_task_id` in its `created` event), comment on that creator card to assign any further code, mirror or CI cards to default, or it keeps creating misrouted cards. |
| `needs_input` asking David to merge a PR or push main "because this profile may not" | That restriction belongs to the profile, not to the user. Reassign to a profile with merge authority under the normal PR/CI workflow, and comment "merge once required checks pass, then verify main CI". |
| Needs a host the gateway can't SSH to, or "reconnect Desktop so session X can receive" | `cross_session read` the target session first; if it answers, the relay works. Comment the route (cross_session to a session on that host's gateway, e.g. the one already running `bb land` merges), then unblock. |
| Part of the card waits on an owner decision | Comment "finish everything else; record item X as waiting on decision Y", then unblock. |
| Owner decision / scope choice | Ask inline with `clarify` (see SKILL.md), then record and complete. |

After fixing one misrouted card, sweep for its siblings before they block: `select id,status,title from tasks where assignee in (<narrow/sandboxed profiles>) and status not in ('done','archived')`. Worker-created follow-ups inherit the same wrong assignee, and each one that blocks turns the session red again.

Unblock in one call (`hermes kanban unblock <ids…> --reason …`). After about 3 minutes, read back status, `consecutive_failures` and the model override. Queued `ready` cards behind the concurrency limit are fine.

## Sign-in blockers (hub-session-sync sites)

1. Check: `/usr/bin/python3 ~/.hermes/hub-session-sync/hub_session_sync.py check <site>` (use the interpreter that has PyYAML; exit 0 = signed in).
2. The hub agent browser (`browser_exec`) already holds his Google sessions. Open the site's login page and choose "Sign in with Google". In the account chooser, click the row with JS: `document.querySelector('[data-identifier="<email>"]').click()`, or walk text nodes to that row's `role=link` ancestor. Coordinate clicks often miss there. The browser is shared with other sessions, so open your own tab with `new_tab` and re-read `location.href` before each step.
3. Verify it is HIS existing account (an existing plan, usage or billing history), not a freshly created one. If it is new, sign out and try the other address.
4. Re-run `check`, then `hub_session_sync.py resolve <site>`; the blocked card completes itself. Read its status back.
5. GitHub's OAuth "Authorize" button stays disabled for automated input. That is an anti-clickjacking guard, so never remove the `disabled` attribute; use a different SSO provider instead.
6. `run_in_user_terminal` only runs from the owner's own chat. From a dispatch session, do the hub-side route above rather than handing him a command to paste.
