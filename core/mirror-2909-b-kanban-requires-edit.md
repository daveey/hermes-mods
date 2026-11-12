# Kanban `edit --requires` (orchestrator-only capability correction)

Patches: 2816, 2831.

- **2816** (`fd2d1bfa81`, feat(kanban): audit orchestrator-only requirement corrections)
- **2831** (`e3eaa13496`, kanban edit: --requires/--completion-contract/--reason preflight in kanban_requires_edit; kanban.py back to the 2000-line cap)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- `--requires` replaces declared requirements with a comma-separated list (empty clears) on idle, open cards with no claim or lease; workspace-implied and skill-implied needs are retained. It refuses when a worker env var is set, on done/archived/running/review cards, and cannot combine with other edit flags. No-op edits report unchanged; changes append an `edited` event with old/new and actor and notify.
- 2831 moves the `--requires`/`--completion-contract`/`--reason` preflight into `kanban_requires_edit` so `kanban.py` returns to the 2000-line cap (behavior unchanged).

## How it works

- `hermes_cli/kanban_requires_edit.py` (new: `set_requires`, `cmd_edit_requires`), `hermes_cli/kanban.py`, `hermes_cli/kanban_parser.py`.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_requires_edit.py`

## Tests

- `tests/hermes_cli/test_kanban_edit_requires.py` (CLI readback and audit, worker refusal, invalid values leave card unchanged, explicit clear, non-idle/closed refused, workspace needs retained, combined flags refused); follow-ups in the recovery-guard tests: unknown card, `--force` is not an escape, skill requirements retained, ready card with custodian refused, legacy null requirements accepted.

Test files touched by these patches:

- `tests/hermes_cli/test_kanban_edit_requires.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Capability labels are validated by `_required_capabilities`; invalid values leave the card untouched.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
