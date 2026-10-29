# Session list previews are computed for the page rows only

Patch **0533** (source `4c32c4aad3`).

## Behavior

`list_sessions_rich(order_by_last_active=True)` used to put the correlated "first user message" preview subquery in the
paged SELECT. SQLite computes every result column for each candidate row before `ORDER BY ... LIMIT` keeps the page, so
each sidebar slice read one message per session in the store (7.6k sessions / 600k messages), three slices per refresh.
The page is now chosen in an inner SELECT and the preview is projected onto those rows only. Rows, order and previews are
unchanged. Measured on a 12 GB `state.db`: the sidebar's three slices dropped from 1.39 s to 0.35 s.

## How it works

`hermes_state_sessions.py`: the inner SELECT picks the page (an `OFFSET` keeps SQLite from flattening it back into the outer
query), the outer SELECT adds the preview column for those rows.

## Code

`hermes_state_sessions.py`.

## Tests

`tests/hermes_state/test_session_list_preview_page_only.py`: `test_preview_is_computed_for_page_rows_only` (both
`order_by_last_active` values; counts preview evaluations) and `test_full_rows_keep_preview_and_order`.

## Applying

`git am -3`; independent of other patches.

## Gotchas

Do not remove the `OFFSET` from the inner query: SQLite then flattens it and the preview is computed per candidate row again.
