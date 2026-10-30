# CLI exit cancels the in-flight background review; one-shots never fork one

Patches: 1123.

## Behavior

- Classic CLI exit cancels an in-flight post-turn background review before resource teardown, so the review cannot dispatch a tool after interpreter shutdown ("cannot schedule new futures after interpreter shutdown").
- Single-query CLI agents (`-q`, kanban workers) and `hermes -z` are built with `skip_background_review=True`; interactive CLI keeps reviews.

## How it works

`cli_shutdown._cancel_background_review_on_exit` runs the same bounded cancel the TUI session finalize uses, first in `_run_cleanup` and in `_finalize_single_query` (before memory shutdown). One-shots skip the review because the process exits right after the turn, so it could never finish.

## Code

- `cli.py`, `hermes_cli/cli_shutdown.py`, `hermes_cli/cli_agent_setup_mixin.py`, `hermes_cli/oneshot.py`

## Tests

Tests below were identified from the patch source in this documentation pass; they were **not executed** here. Where a patch lists no test name, it edited existing tests or touched untested code and nothing is claimed for it. `tests/hermes_cli/test_cli_exit_cancels_background_review.py`: `test_cli_exit_cancels_in_flight_review_before_it_dispatches_a_tool` (subprocess running the real `_run_cleanup` plus interpreter shutdown), `test_cli_exit_without_a_review_is_a_no_op`, `test_single_query_cli_agent_skips_background_review`, `test_interactive_cli_agent_keeps_background_review`, `test_oneshot_z_agent_skips_background_review`.

## Applying

These patches belong to the ordered fork series that follows an upstream merge. Apply the whole series with `git am -3 --empty=keep` from its recorded base; each patch depends on the ones before it, and no independent application of this subset was tested. The ported tests only make sense against the post-merge upstream names they reference.

## Gotchas

The cancel is bounded; a review stuck past the bound is not waited for. Measured failure counts quoted in the commit message are the author's observations on one host, not reproduced here.

## Could this be a plugin instead?

No: it changes core CLI shutdown and agent construction.
