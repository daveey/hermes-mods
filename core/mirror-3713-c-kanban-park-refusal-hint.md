# Unpublished-workspace park refusal names the publish refusal

Patches: **3709**.

## Behavior
- When parking a worktree card with publish-on-park enabled is refused as `unpublished_workspace`, the message now appends `(workspace_source publish refused: <code>: <detail>[; <fix>])` naming the real cause.
- For `source_clone_unregistered` the hint names the `kanban.workspace_source.clones` setting (`{"<owner>/<name>": "<clone root>"}`) and the worktree's own clone root.
- With publish-on-park off, the message is unchanged. Sibling clones stay fail-closed; receipts and events are unchanged.

## How it works
3709 makes the source preflight keep a per-(task, run) list of refusals in a ContextVar while it runs; the receipt computation records each publish refusal there, and `refusal_hint(task_id, run_id)` renders the last one. The publication guard appends that hint to its fixed `unpublished_workspace` text. `prove_origin`'s `source_clone_unregistered` detail now names the worktree's clone root (parent of the git common dir).

## Code
- `hermes_cli/kanban_source_preflight.py`
- `hermes_cli/kanban_source_publish.py`
- `hermes_cli/kanban_workspace_publication.py`
- `tests/hermes_cli/test_kanban_source_publish.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_source_publish.py`: `test_unpublished_park_refusal_names_the_publish_refusal`, `test_publish_refusal_hint_is_scoped_to_the_preflighted_run`, `test_unpublished_park_refusal_without_publish_refusal_is_unchanged`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The hint is scoped to the preflighted task and run; it is not shown for other runs.
