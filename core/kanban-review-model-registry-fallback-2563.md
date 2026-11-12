# Review model falls back when this host's registry refuses it

Patches: **2529**.

## Behavior

- A review handoff (`kanban_request_review`) may carry metadata `review_model` (and `review_provider`). Previously that became the review override with no routing-registry check, so a pool worker that did not register the model spawned the review anyway, died at startup with a managed-pin rejection, and the card eventually gave up to triage.
- Now the dispatcher runs the worker's own startup pin check against the assignee's config first. A host that registers the override (for example the hub) still runs it unchanged (`review_override`).
- A refused override reviews on a registered model distinct from the implementer: the configured strong model, else another eligible deep route, each also registry-checked. The `model_tier` event records reason `review_override_refused`, the implementer model, and `requested` (the asked model, provider, refusal code and the registry's allowed pins); a warning is logged.
- With no known implementer model (card pin, else the handoff run's `run_route` receipt) or no independent registered model, `ReviewModelUnavailable` refuses admission before spawn. It is an infrastructure failure: the card stays in review, holds no claim lock and the retry budget is untouched.
- Quota-episode routing treats `review_override_refused` like the other review overrides (no re-route).

## How it works

`hermes_cli/kanban_review_model.py`: `_handoff` reads the latest `review_requested` run; `registry_refusal` calls `_check_managed_pin` via `_assignee_config`; `refused_override` builds the receipt; `_implementer_model` and `_independent(..., registered=True)` pick the fallback; `resolve_review_model` ties them together (constant `OVERRIDE_REFUSED`). `_apply_model_tier` in `hermes_cli/kanban_model_tier.py` adds `requested` to the payload; `decision` in `hermes_cli/kanban_quota_episode.py` includes the new reason.

## Code

`hermes_cli/kanban_model_tier.py`, `hermes_cli/kanban_quota_episode.py`, `hermes_cli/kanban_review_model.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_managed_pin_policy.py`. Entry path: `dispatch_once` (the dispatcher) with a fake spawn function, after a real `request_review` handoff: a refused override falls back to a registered reviewer with the receipt and zero failures; no independent reviewer never spawns and records an infrastructure `spawn_failed`; a registered override is kept. The quota-episode change, and CLI, tool and Desktop surfaces, are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The fallback needs the implementer's model to be knowable; otherwise the review waits (with repeated `spawn_failed` events) until an independent registered model or a valid `review_model` exists.
- Registry membership is judged on the dispatching host, so the same handoff can resolve differently per host.

## Plugin

Core change; no plugin required.
