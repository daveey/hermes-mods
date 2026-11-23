# Canonical session status envelope (backend reducer) (mirror 5736–5737)

## Behavior
- Outcome refresh publishes a revisioned status envelope under `outcome_json["status"]` (status_version 1, identity, generation, revision, source_revisions, coverage, failures, kind, presentation stripe and reason codes).
- Precedence: mute; genuine human ask; own active turn; open linked work; unknown/failed/lost evidence (never green); settled turn with all work terminal = done; never-started draft = no stripe. A failure persists until a later generation's own successful completion.
- A reader that raises is unknown coverage (`<source>_read_failed:<Error>`), not an empty list. No prose heuristic selects the envelope; declarations are explanation only.
- Each refresh publication attempt shares a card read (including a reader exception) between explanation and status; a fenced retry may perform a new read.

## How it works
`hermes_state_session_status.py` is the pure reducer; `hermes_state_session_status_snapshot.py` collects a qualified snapshot over existing readers and the strict process inventory; `hermes_state_outcome.py` publishes with fencing; `tools/process_registry*.py` provide strict results.

## Code
- `hermes_state_session_status.py`
- `hermes_state_session_status_snapshot.py`
- `hermes_state_outcome.py`
- `tools/process_registry.py`
- `tools/process_registry_results.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_state/test_session_status.py` checks precedence, revisions, source qualification and retained failure semantics. `test_session_status_refresh.py` checks persisted readback, declarations, reader failures, token ownership, stale collector fencing, and `test_one_refresh_reads_the_board_once_for_explanation_and_status`. `tests/tools/test_process_results_strict.py` checks strict qualified process inventory and missing-exit evidence. Desktop painting and live REST/TUI publication entry paths are untested by these reducer/refresh cases.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files. The Desktop painting half is a separate later change.

## Gotchas
- No new table, column, poller or trigger.
- A second warm card read could hang inline-thread tests; keep one read per publication attempt.

## plugin?
No. Core change.
