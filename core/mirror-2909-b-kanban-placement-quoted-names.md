# Kanban placement: backticked names are not host markers

Patches: 2815.

- **2815** (`fdd2618980`, fix(kanban): exclude quoted names from placement marker scans)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- Producers quote names such as workflow or repo names in backticks; scanning them pinned cards to a machine. `marker_work_text` strips complete matching backtick spans (including fenced code) plus evidence and placement-note blocks, leaving whitespace separation; incomplete quotes keep their markers. Both front doors (routed and direct creation) use it, and diagnostics `portability` too.
- `docs/worker-first-v1.md` documents the quoting convention.

## How it works

- `hermes_cli/kanban_placement.py` (`marker_work_text`, `_BACKTICKS`, used in `_local_marker`), `kanban_placement_diagnostics.py`, `docs/worker-first-v1.md`.

## Code

- `docs/worker-first-v1.md`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_placement_diagnostics.py`

## Tests

- `tests/hermes_cli/test_kanban_quoted_placement.py` (`test_quoted_names_enroll_on_both_frontdoors`, `test_real_work_and_incomplete_quotes_still_pin`), `test_kanban_worker_placement.py` and `test_kanban_published_worktree.py` (`test_backticked_names_are_not_markers_in_routed_creation`).

Test files touched by these patches:

- `tests/hermes_cli/test_kanban_published_worktree.py`
- `tests/hermes_cli/test_kanban_quoted_placement.py`
- `tests/hermes_cli/test_kanban_worker_placement.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- A producer that forgets the backticks still pins; real work text outside quotes still counts.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
