# Clearing blocked cards yourself

Classify each blocked card by the event that stopped it (`select kind,payload from task_events where task_id=? and kind in ('blocked','gave_up','crashed') order by id desc limit 1`), not by its body.

| Stop cause | Action |
|---|---|
| `gave_up` with a provider 429 / "usage limit resets in ~Nh" tail | Repin to a working subscription model (`hermes kanban set-model <id> <model> --provider <p>`), after a one-line live check of that model (`hermes chat -q "Reply with exactly: OK" -m <model> --provider <p> --quiet`). Also repin queued `todo`/`scheduled` cards pinned to the walled provider, or they fail on dispatch. If the gateway's root model has an empty fallback chain, add one: `hermes config set fallback_providers '[{"provider":"openai-codex","model":"gpt-6-astra"}]'`, then `hermes fallback list`. |
| `needs_input` saying "continuation limit reached" | `hermes kanban unblock`; the blocked run breaks the consecutive-continuation streak. |
| `gave_up` "exited cleanly without kanban_complete" (protocol violation) | Unblock; the worker re-verifies its prior work. |
| `capability`: the profile can't write that code | `hermes kanban reassign <id> default --reason …`, then unblock. |
| Needs a host the gateway can't SSH to | Comment the route (cross_session to a session on that host's gateway), then unblock. |
| Part of the card waits on an owner decision | Comment "finish everything else; record item X as waiting on decision Y", then unblock. |
| Owner decision / scope choice | Ask inline with `clarify` (see SKILL.md), then record and complete. |

Unblock in one call (`hermes kanban unblock <ids…> --reason …`). After about 3 minutes, read back status, `consecutive_failures` and the model override. Queued `ready` cards behind the concurrency limit are fine.

## Sign-in blockers (hub-session-sync sites)

1. Check: `/usr/bin/python3 ~/.hermes/hub-session-sync/hub_session_sync.py check <site>` (use the interpreter that has PyYAML; exit 0 = signed in).
2. The hub agent browser (`browser_exec`) already holds his Google sessions. Open the site's login page and choose "Sign in with Google". In the account chooser, click the row with JS: `document.querySelector('[data-identifier="<email>"]').click()`, or walk text nodes to that row's `role=link` ancestor. Coordinate clicks often miss there. The browser is shared with other sessions, so open your own tab with `new_tab` and re-read `location.href` before each step.
3. Verify it is HIS existing account (an existing plan, usage or billing history), not a freshly created one. If it is new, sign out and try the other address.
4. Re-run `check`, then `hub_session_sync.py resolve <site>`; the blocked card completes itself. Read its status back.
5. GitHub's OAuth "Authorize" button stays disabled for automated input. That is an anti-clickjacking guard, so never remove the `disabled` attribute; use a different SSO provider instead.
6. `run_in_user_terminal` only runs from the owner's own chat. From a dispatch session, do the hub-side route above rather than handing him a command to paste.
