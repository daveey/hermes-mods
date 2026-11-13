# Re-audit: core 9eab517615 / 6696ec925b / ae4a81df16 (gateway-local quiet proof), main dd75da236b

Card t_01a1d6478f864849. Opened by account-usage-compat-watch: core moved `run_agent.py` off every audited digest.

Drifted pins (old digests kept, new digest added):
- `execution-compatibility.json` `run_agent.py`: dcaa59ee3812 (9daa6d93b9, last audited) -> 5bdf5390fe19
  (ae4a81df16). Three commits touch `run_agent.py` since 9daa6d93b9, all landed together: 9eab517615
  (feat(quiet): gateway-local quiet proof), 6696ec925b (CI regressions) and ae4a81df16 (stand-in review agents
  dispatch via defining class). Intermediate digests dbd2463e1e / 44eb02951e were never a landed main head, so
  they are not pinned.

`check_compat.py` against both installed core dd75da236b and an export of fork origin/main 5ae112272e reported only
this pin. The commits' other files (`agent/auxiliary_client.py`, `agent/turn_facade.py`, `agent/local_work_agent.py`,
`agent/claim_quiet*.py`, gateway/cron/kanban seams, ...) are not pinned by account-usage.

What changed in `run_agent.py` (+39/-12), all inside the background-review spawn:
- `_spawn_background_review_now` now reserves a `local_work_agent` token ("background_review") before any
  preparation and delegates to the new `_spawn_background_review_run` (the old body) through the defining class,
  so stand-in agents keep working. The review thread's outermost `finally` releases the token after the
  existing end-of-review + requeue logic; an early exit releases it in the caller.
- The requeue logic (update-pause requeue, then preempted-review requeue) is unchanged, only nested inside the
  token's try/finally.

Against selection.py's seams:
- Client factories, `recover_with_credential_pool`, `_failed_credential_identity`, `switch_model`,
  `interruptible_*_api_call`, `perform_api_call`: untouched. The change is background-review lifecycle/accounting.
- No new credential or route fence: the local-work registry only counts activity for the claim-quiet lease, and
  quiet gates only automatic local Kanban claims and kanban-wake execution, never a provider call, credential
  pick or route. `aux_call_scope` around `call_llm`/`async_call_llm` and `TurnFacadeMixin.run_conversation`'s
  scope are reservation wrappers with no credential state, so a request-local account snapshot bypasses nothing.
- selection.py unchanged, so recovery_wrapper and fast-rate-limit-fallback/account-usage-compatibility.json need
  no refresh.
