# Kanban test pins: sync follow-ups and source clone proof

Patches: **3681–3682**.

## Behavior
Test-only patches; no behavior change.
- A behind sync round whose send fails after pulls still notes the backlog and reruns at once.
- A Board-deferred outbox row never ages the outbox alarm while behind.
- `prove_clone` refuses `origin_rewritten` for a global fetch-only rewrite of origin (fetch differs, push restored).

## How it works
3681 adds two tests pinning mutants that survived (dropping the backlog note, aging every sendable row). 3682 adds a test using a global `insteadOf` plus `pushInsteadOf` pair and a reachable bare fork to pin the fetch-URL compare.

## Code
- `tests/hermes_cli/test_kanban_sync_round_followups.py`
- `tests/hermes_cli/test_kanban_source_resolver.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_sync_round_followups.py`: `test_a_behind_round_whose_send_after_the_pulls_fails_still_reruns_at_once`, `test_a_board_deferred_op_never_ages_the_alarm_while_behind`.
- `tests/hermes_cli/test_kanban_source_resolver.py`: `test_global_fetch_only_rewrite_of_origin_refused`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
