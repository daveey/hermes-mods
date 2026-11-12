# Review independence reads the model that ran; repeated review refusals block visibly

Patches: **2591**.

## Behavior

- Review model independence (a reviewer must not be the implementer's own model) now identifies the implementer from the model that actually ran. A class-routed implementer run (route model `class:deep`, common under `kanban.strong_model: class:deep`) previously left no concrete name, so a refused `review_override` raised `ReviewModelUnavailable` on every claim: an infrastructure `spawn_failed` with failures at 0, so the card deferred silently forever. It now resolves, and the review runs on a distinct registered model.
- Under a pinned card whose quota rerouted to a peer, the pin is not what ran. The implementer is now taken from the run receipts first, so the reviewer is chosen distinct from the rerouted model, not from the pin.
- A concrete-pin review path uses the same receipt, so a class pin whose run left a concrete model no longer refuses.
- When no concrete receipt exists, the third identical `ReviewModelUnavailable` deferral in a row (`DEFERRAL_LIMIT = 3`, spaced by the infrastructure cooldown) force-trips the card to a sticky `triage` block. The `gave_up` event carries `admission_refusal: review_model_unavailable` and `deferrals: 3`, alongside the breaker's own `reason_code`. The first two deferrals still keep the card in `review` with failures at 0.

## How it works

`hermes_cli/kanban_review_model.py`: `_implementer_model` reads the handoff run's `worker_result.route_model`, then its `run_route` receipt `model`, then the card's concrete pin (via new `_run_model`, which ignores class selectors). `resolve_review_model` uses it for any pinned card. `deferral_exhausted` looks at the last `DEFERRAL_LIMIT - 1` ended runs and checks they are `spawn_failed` with the identical error (truncated to 500 chars); an unreadable history keeps the plain deferral. `admission_refusal` returns `(infrastructure, force_trip, gave_up extra)`; `hermes_cli/kanban_db_dispatch.py` passes `force_trip` and `event_payload_extra` to `_record_task_failure`, with the classification moved out to keep the file at its line cap.

## Code

`hermes_cli/kanban_review_model.py`, `hermes_cli/kanban_db_dispatch.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_managed_pin_policy.py` (three new tests, six parameterized cases). Entry path: `dispatch_once` on a board fixture with a review handoff and receipts written as task events. Covered: class-routed implementer named by `worker_result` (reviewer gets the registered alternative, `model_tier` reason `review_override_refused`); three identical refusals end in `triage` with the extra fields; a rerouted pin (via `run_route` or `worker_result`, with and without a refused override) reviews on a model distinct from the one that ran. The lane-dispatch spawn path under a real worker, the CLI, REST and Desktop UI are **untested**. Source notes the six cases fail before the change and pass after; that was not re-verified here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Receipts are only as good as the booking of `route_model` by the run; a card whose implementer left no concrete receipt and has a class or no pin still defers, then trips to triage after three identical refusals.
- The deferral count compares error text of ended runs, so a changed message resets the streak.

## Plugin

Core change; no plugin required.
