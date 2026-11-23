# Links inventory capture envelope and typed refusals

Patches: **5910–5911**. Follow-up to the existing in-process links-facts startup and serialized capture implementation.

## Behavior

Links capture gets its own inventory-sized envelope instead of borrowing a task-page-sized generic capture budget. Captures remain complete generations: excess rows/bytes/time make the projection unavailable, never a truncated success. Consumers can distinguish pending, expired, scope-changed, row-budget, byte-budget, time-budget and generic capture failures through typed error codes.

## How it works

`LinksBudget`, a `CaptureBudget` subclass, defaults to and caps at 16384 combined card/origin rows, 16 MiB encoded bytes and 15 seconds. Its constructor rejects out-of-range ceilings. Generic `CaptureBudget` defaults are unchanged. `LinksRuntime` supplies this links-specific budget to `ContentOwner`.

The serialized source spends its time envelope while acquiring the projection lock as well as while reading/encoding. It checks origin scope, row count, byte count and elapsed time before publishing. A hung capture receives a typed time-budget refusal without starting a concurrent replacement. Failure retains existing bytes as stale, or publishes unknown if no prior value exists. `LinksUnavailable.error_code` preserves that reason through consumer refusal; absent facts report pending, and expired facts without a more specific error report expiry.

## Code

- `hermes_cli/fact_remote_links.py`: `LinksBudget`, source capture/refusal typing and `LinksFacts` consumption.
- `hermes_cli/fact_links_startup.py`: startup owner budget selection.

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/hermes_cli/test_fact_links_inventory_budget.py`: synthetic native-sized inventories (including larger platform-shaped inventory cases), independent generic/links bounds, envelope validation, accepted capture within the expanded time budget, rejected excess envelopes without partial publication, and error-code propagation through capture/store/consumer.
- `tests/hermes_cli/test_fact_remote_links_serialization.py`: serialized projection-lock timeout carries `links_capture_time_budget` rather than losing its typed cause.

The platform-shaped inventory tests are fixtures, not evidence of this mirror pass running on that platform or capturing a live board.

## Applying

Use the full `core/README.md` replay recipe with the preceding links-facts startup and serialization changes. Port budget selection and typed failure propagation together so the owner and consumer agree on refusal semantics.

## Gotchas

Links include retained legacy associations, not just open cards. Raising only the row ceiling does not guarantee a capture fits the byte/time ceilings. A larger bounded envelope is not an unbounded retry or permission for synchronous HTTP fallback. A stale retained generation is not current evidence.

## plugin?

This is core fact-source budgeting and failure propagation, not a new plugin. No Desktop timeout change or lanes stale-while-revalidate plugin change is included.
