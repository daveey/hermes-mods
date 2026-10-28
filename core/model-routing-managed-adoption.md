# Live agents adopt the managed routing policy; aux and main counters split

Patch: 0619 (daveey/hermes `341a8f5c09`).

## Behavior

When an operator sets `model_routing.enforce_subscription_only`, agents already alive (gateway-cached Slack/Desktop sessions, running Kanban workers) used to fail closed at their next request with "lacks verified subscription provenance", because provenance was minted only at construction. `adopt_managed_policy()` now re-resolves the live agent's own tuple as a managed exact pin before any request is shaped, rebuilds its client from the fenced subscription runtime and drops fallback chains. Unapproved tuples (OpenRouter, paid keys, unknown models) still raise before any send.

Separately, main sessions and their auxiliary allocations shared one round-robin counter per class, so with two providers each session's main allocation always landed on the same provider. Aux owners (`aux:<owner>:<task>:<class>`) now keep their own counter; the main counter key is unchanged so existing positions survive.

## How it works

Adoption is called from `_build_api_kwargs_for_mode` and the attempt path (`agent/turn_api_request.py`, `agent/chat_completion_helpers.py`); state lives in `agent/model_routing_state.py`.

## Code

- `agent/model_routing_agent.py` (new), `agent/model_routing_state.py`, `agent/turn_api_request.py`, `agent/chat_completion_helpers.py`.

## Tests

- `tests/agent/test_managed_adoption.py`: streaming and non-streaming adoption with OAuth-only wire auth, unapproved pin fails closed with zero completion sends, no-op when unenforced, aux counter split, main counter compatibility.

## Applying

Apply with `git am -3` in series order from the merge-base with upstream; the patch is self-contained apart from the earlier patches it extends. If it no longer applies, re-implement from Behavior and Code.

## Gotchas

- Adoption is skipped when enforcement is off, so unenforced hosts see no change.

## Plugin?

No. It changes core code that runs before or outside plugin loading.
