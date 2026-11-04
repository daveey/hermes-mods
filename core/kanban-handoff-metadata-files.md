# Kanban: scratch files named in handoff metadata survive cleanup

Patches: **1794** (`41ca81cd93`, `fix(kanban): keep scratch files named in handoff metadata past workspace cleanup [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Workspace files cited in `kanban_complete`/`request_review` metadata (for example `replay_report`, `plan`) are preserved when the managed scratch workspace is reaped.

## How it works

`hermes_cli/kanban_metadata_files.preserve_metadata_files`, called from `_stage_completion_artifacts`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1794: Workers cite workspace files in kanban_complete / request_review metadata (replay_report=<ws>/REPORT.md, plan=<ws>/PLAN.md). Completion reaps the managed scratch workspace right after, and only metadata["artifacts"] was copied out, so reviewers found those citations dangling (t_01a19d7fc2cfe36a, t_01a19d0bcab45558 on 2026-11-02).

## Code

`hermes_cli/kanban_db.py`, `hermes_cli/kanban_metadata_files.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_metadata_files.py`: `test_file_both_declared_and_named_is_copied_once`, `test_files_over_total_cap_are_skipped_with_logged_reason`, `test_metadata_named_workspace_file_survives_reaper`, `test_non_workspace_paths_are_untouched`, `test_review_handoff_keeps_metadata_named_file_past_reviewer_completion`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
