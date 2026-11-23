# Typed courier refusals and pending adoption

Patch coverage: **5837–5838**.

## Behavior

- A typed no-create refusal settles as `rejected` with incomplete handoff, rather than repeatedly replaying an envelope that cannot create a card.
- Successful adoption of an existing card remains pending while origin binding finishes.
- An automatic courier create with empty worker labels can retain the admission decision's pool route instead of accidentally becoming a hub pin.

## How it works

The origin-owner response classifier requires `created: false`, a nonempty string code, and no successful adoption before rejecting. Unknown/malformed replies keep the prior retryable handling. Placement treats nonempty labels, non-auto placement, or a non-scratch workspace as explicit pinning. The tool clears empty labels only for origin-intent auto placement when hub admission denied placement and supplied a replacement route.

## Code

- `hermes_cli/kanban_origin_owner.py`: `_dispatch_result`.
- `hermes_cli/kanban_hub_admission_placement.py`: pin classification.
- `tools/kanban_tools_placement.py`: persist admitted pool policy.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

- `tests/hermes_cli/test_kanban_courier_refusals.py`: typed codes with/without handoff flags; unknown writes stay retryable.
- `tests/hermes_cli/test_kanban_origin_owner.py`: owner-entry adoption replay retains `origin_bind_pending`, the same binding, and one unchanged card.
- `tests/hermes_cli/test_kanban_pool_queue.py`: tool create covers queued and policy-based pool routes, required capabilities and idempotent replay.

## Applying

Apply the covered patches in the full ordered core series using the [core applying recipe](README.md#applying), including its replay bridges and required repair overlay. These entries depend on earlier core changes; they are not advertised as an independent cherry-pick set. Reimplementation should preserve the invariants above and run the named source suites in a disposable source checkout.

## Gotchas

`created: false` alone does not mean refusal: adoption deliberately creates no new row. Empty labels outside the narrowly matched auto/hub-denial path retain their original lease/sync validation.

## plugin?

Not a standalone plugin: response settlement and placement persistence are internal core contracts.
