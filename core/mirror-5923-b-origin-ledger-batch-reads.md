# Origin ledger: batched exact owner task reads

Mirror snapshot 5923; ordinals 5876, 5887.

Entries:

- **5876**: perf(kanban): batch exact origin observation task reads
- **5887**: perf(kanban): keep exact batch keys outermost in replica query

## Behavior

- Origin-ledger projection used to cross SQLite once per obligation (one `SELECT ... FROM replica` per binding) in each of preflight, locked refresh and presentation. Each phase now reads the exact owner-qualified `(gateway, task_record_key)` set in one statement.
- Nothing is retained between calls: each phase reads its own current snapshot, and the locked refresh takes a fresh batch after the ledger write lock is acquired rather than reusing preflight evidence. Unknown evidence and completion validation are preserved, and read errors never become "absent".
- `HERMES_ORIGIN_BATCH_READS` defaults on; `0`, `false`, `off` or `no` restores the original per-binding keyed reads.
- 5887: the batch query uses `CROSS JOIN` so the requested keys stay the outer loop. With an ordinary `JOIN` SQLite could pick the collection index, scan every replica task and re-scan `json_each` per row; matching semantics are unchanged.

## How it works

- `hermes_cli/kanban_origin_batch.py` (new): `task_rows(source, bindings)` returns `{(gw, key): (version, data)}` from one `json_each(?) ... CROSS JOIN replica` query, `None` when disabled (legacy path) and `{}` for no bindings; `lookup(source, binding, rows)` answers from the batch or falls back to the keyed query.
- `hermes_cli/kanban_origin_ledger.py`: `_advanced_observation` takes an optional `rows` batch; the pending-check, refresh and presentation loops fetch their obligation lists once and build one batch each (the presentation batch only for the viewing origin's bindings, and only when `fresh`).

## Code

Shipped non-test paths:

- `hermes_cli/kanban_origin_batch.py` (new)
- `hermes_cli/kanban_origin_ledger.py`

## Tests

Source-side tests shipped by these entries; none were executed by this documentation pass, so no pass result is claimed here:

- `tests/hermes_cli/test_kanban_origin_batch.py` (exact keys, de-duplication, absence, failure and rollback; refresh and projection stay strict after committed changes with the switch on and off; locked refresh takes a new batch after preflight; batch work bounded by requested keys, not replica inventory)

## Applying

These entries are part of the ordered snapshot 5923. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). They are not independent patches.

## Gotchas

- Do not cache the batch across calls or phases; staleness after the write lock is the failure this design avoids.
- Savings in production were not measured when the source change landed (the motivation was a profile showing repeated per-obligation reads).
- Keep `CROSS JOIN`; replacing it with a plain `JOIN` can reintroduce a full replica scan.

## Plugin

Core change; no plugin or hook is involved.
