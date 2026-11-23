# Association-only receipt deduplication

Patch coverage: **5851**.

## Behavior

Local receipt dedup preserves listed creator/subscriber and status selection without running full execution, wait or history enrichment while holding the links projection lock.

## How it works

A dedicated read-only connection with a bounded busy timeout runs the existing association selector inside its own read transaction and closes on success or failure. Busy fallback uses a separate `links` memo namespace, preventing association-only rows from poisoning enriched sidebar or outcome-reader caches.

## Code

- `hermes_cli/kanban_links_associations.py`: association reader.
- `hermes_cli/kanban_session_activity.py`: dedup reader selection.
- `hermes_cli/kanban_session_activity_busy.py`: isolated links memo.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

`tests/hermes_cli/test_kanban_links_association_reader.py` covers no enrichment under the lock, creator/subscriber/status equivalence, owned connection cleanup and memo isolation. `test_kanban_busy_first_read.py` pins the links namespace; `test_kanban_local_dedup_cost.py` follows the narrower reader boundary. These are local projection tests, not live Desktop performance evidence.

## Applying

Apply the covered patches in the full ordered core series using the [core applying recipe](README.md#applying), including its replay bridges and required repair overlay. These entries depend on earlier core changes; they are not advertised as an independent cherry-pick set. Reimplementation should preserve the invariants above and run the named source suites in a disposable source checkout.

## Gotchas

Association rows intentionally omit enriched fields and must not escape into enriched-reader caches. Owned connection isolation avoids waiting on a shared sidebar borrower but does not remove SQLite contention; existing bounded busy fallback remains necessary.

## plugin?

No: this changes the internal projection reader and memo contract, not an extensible UI surface.
