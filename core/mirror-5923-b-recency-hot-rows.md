# Recency ranking: step Python through the page, not the store

Mirror snapshot 5923; ordinals 5890, 5891.

Entries:

- **5890**: perf(recency): step Python through the page, not the store, under the store lock
- **5891**: fix(recency): re-rank page rows whose chain a write can shrink, not only dirty's relatives

Builds on the earlier recency page-probe and rank-memo features.

## Behavior

- Python's `sqlite3` releases and retakes the GIL around every row it steps, so in a many-thread backend each row stepped under the per-store lock can wait a full switch interval. The ranked session-list path now hands Python far fewer rows while returning the same results:
  - the message-maximum rebuild comes back as a single `json_group_array` row; because JSON renders a REAL with 15 digits, a REAL travels as two exact integers (`trunc(m)` and `(m - trunc(m)) * 2**52`) and is reassembled bit for bit, with a REAL below 1 travelling as `1.0`, still a valid upper bound;
  - the candidate id list is one JSON row handed back verbatim as the `json_each` bind;
  - the re-probe of high cached maxima filters in SQL and steps only through sessions whose maximum fell below the threshold;
  - the memo re-rank asks SQLite only for the dirty rows' relatives and merges untouched page rows back by their memo sort keys.
- 5891 fixes a correctness gap in that re-rank: session-change triggers stamp only a row's own id, so when a chain member is re-parented, detached or deleted, the page row whose chain it left is not a relative of it any more (and deleting a messageless member never bumps the lowered counter). Each memo entry now records, when it is stored, the page ids that have a child; those are always re-ranked along with the dirty rows' relatives, so a drop is caught as `fallback`/`rank_lowered` as before. No schema change.

## How it works

- `hermes_state_recency.py`: `_rebuilt_maxima(conn)`, `_candidates(...)` and the re-probe (`drop_below`) use JSON-aggregate statements; `restricted(...)` handles restricted ids.
- `hermes_state_rank_memo.py`: `_rerank(entry, relatives, restricted_ids)` re-ranks only the relatives plus the recorded has-child ids and merges the rest by memo keys; entries store the has-child ids at put time from the same snapshot.

## Code

Shipped non-test paths:

- `hermes_state_rank_memo.py`
- `hermes_state_recency.py`

## Tests

Source-side tests shipped by these entries; none were executed by this documentation pass, so no pass result is claimed here:

- `tests/hermes_state/test_recency_hot_rows.py` (exact round trip of the rebuilt maxima in one row; cold and lowered reads step through the page, not the store; re-probe of legitimately high sessions steps through none; re-rank steps only through rows a write touched; a chain member leaving lowers its old root, parameterized over compression-tip projection on/off and re-parent, detach and delete)
- The source states that the existing randomized equivalence tests in `test_recency_page_probes.py` and `test_rank_memo.py` were left unchanged; they are not part of these entries and were not run here.

## Applying

These entries are part of the ordered snapshot 5923. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). They are not independent patches.

## Gotchas

- The REAL encoding must stay exact; do not replace the two-integer encoding with plain JSON numbers.
- 5890 alone has the stale-root bug 5891 fixes; keep them together.
- Row-count assertions in the tests pin the optimization: a change that steps more rows through Python fails them even if results are right.
- Reported timings in the source commit are single-machine measurements, not acceptance results.

## Plugin

Core change; no plugin or hook is involved.
