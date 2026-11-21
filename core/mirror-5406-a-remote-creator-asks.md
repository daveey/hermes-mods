# Remote origin-chat delivery for current creator decisions

Mirror entries: 5127, 5128, 5129, 5131.

## Behavior

- A current scheduled creator decision can travel with owner snapshots to its origin chat, through remote indexing, observation and unloaded-session wake gates.
- An own-card `wait-comment` without a named producer is classified as a creator question, including inferred/external declarations; `--from-gateway` and `--from-card` keep genuinely external producers external.
- Eligible hub subscribers must be well formed and counted by the publisher; notice-only `touch`/`send_refused` rows do not qualify.
- Blocked-card notice identity and genuine user-directed human-request behavior remain unchanged. Only scheduled decision asks gain envelopes.

## How it works

`kanban_remote_ask.py` publishes the latest park only when its current event is `decision_requested`, with event ID/time/payload validation. Owner publication carries `ask_notice`; remote projection/state/summary paths recognize it and deduplicate by event identity. A later non-chat park does not resurrect a historical ask.

Remote wake handling treats an unstartable creator wake as deferred, not passively acknowledged, allowing retry. Subscriber admission shares the hub collector's uncounted-reason exclusions and validates qualified destinations. The rollout correction excludes blocked cards from envelopes, retaining their existing terminal-notice keys. Creator notice wording addresses the agent, without changing who can answer or grant approval.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `gateway/kanban_remote_owner.py`
- `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_decision_ask.py`
- `hermes_cli/kanban_origin_ledger.py`
- `hermes_cli/kanban_remote_ask.py`
- `tui_gateway/kanban_remote_notice_state.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/kanban_remote_observations.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/kanban_unloaded_gates.py`
- `tui_gateway/remote_index_projection.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Origin-decision tests compose accepted adoption, parking, owner publication and remote sweep/wake, including explicit/inferred/external own-comment variants, time blocking, human-request controls, budget retry, durable dedup, stale/repeated asks and malformed envelopes. Comment-origin tests cover named external producers and refusal of an invisible own-card creator park. Rollout tests assert existing blocked notices are not re-notified. Notice-only hub-row tests assert no park/event without a counted subscriber. Natural live cross-gateway delivery and installed receiver behavior are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_decision_ask.py`
- `tests/hermes_cli/test_kanban_origin_decision_wake.py`
- `tests/hermes_cli/test_kanban_comment_origin.py`
- `tests/hermes_cli/test_kanban_remote_ask_rollout.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Known incomplete path: there is no receiver-version or ask-envelope capability handshake. A mixed-version receiver can still leave an ask unseen until it updates. An accepted origin binding proves link custody, not end-to-end decision delivery. Do not turn a user-directed human request into an agent approval or ack a question without starting its consumer.

## Why core instead of a plugin

End-to-end delivery spans core subscriber eligibility, owner snapshots, durable notice identities and session wake custody. A plugin at only one endpoint cannot make those admission and retry contracts consistent.
