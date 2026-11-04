# Parked PR owners resume after verdict-free reviews

Patches: **1729** (`bf470a8fb4`, `[daveey] Resume parked PR owners after verdict-free exact-head reviews`); **1730** (`ff0ff207fc`, `[daveey] Bind verdict-free review aliases and deduplicate diagnostics`); **1731** (`f3e6cc37a1`, `[daveey] Cover reviewed PR binding on explicit approval paths`); **1819** (`f3dea5638b`, `[daveey] Keep metadata-less PR reviews out of the ordering-dependency handoff`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- A PR owner parked for review resumes after an exact-head review that carries no verdict (1729); review aliases are bound and diagnostics deduplicated (1730); explicit approval paths are covered (1731).
- A review without metadata stays out of the ordering-dependency handoff, fixing a flaky `active_pr` hold release (1819).

## How it works

`kanban_dependency_review`, `kanban_db_dispatch_pr_state`, `kanban_db_dispatch_guard_notice`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1729: (subject only)
- 1730: (subject only)
- 1731: (subject only)
- 1819: Problem: tests/hermes_cli/test_kanban_dependency_review_no_verdict.py:: test_other_refusals_survive[missing] flaked on main (run 41531323842, job 125572639298) and wt/api-context-usage (run 41542995049): check_respawn_guard returned None instead of "active_pr".

## Code

`hermes_cli/kanban_db_dispatch_guard_notice.py`, `hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_dependency_review.py`, `hermes_cli/kanban_dependency_review_notice.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_dependency_review.py`
- `tests/hermes_cli/test_kanban_dependency_review_no_verdict.py`: `test_finished_unreadable_review_notices_immediately`, `test_incident_resumes_exactly_once`, `test_ordering_handoff_does_not_absorb_unreadable_review`, `test_other_refusals_survive`, `test_reviewed_pr_binds_without_summary_number`, `test_unrecognised_verdict_is_repair_not_approval`
- `tests/hermes_cli/test_kanban_dependency_review_sync.py`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
