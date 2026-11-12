# Browse reconcile budget armed before macOS durability pragmas

Patches: 2788.

- **2788** (`feabfcbbfd`, Arm the browse reconcile budget before the macOS durability pragmas)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- On macOS the browse catch-up DDL connection ran `PRAGMA checkpoint_fullfsync` and `synchronous=FULL` before the budget progress handler was armed. `synchronous=FULL` loads the schema, so a slow store held `gateway.lock` unbounded and gateway boot gave up with an ownership conflict.
- The late interrupt then hit `BEGIN IMMEDIATE` outside the rollback try and surfaced as a "corrupt store" 503 instead of the retryable "nothing was changed" one. Linux never runs these pragmas.
- Now the budget is armed first and the barriers plus BEGIN run inside the try.

## How it works

- `hermes_state_reconcile.py` reorders arming of the progress-handler budget ahead of the darwin pragmas and widens the try/rollback region to include `BEGIN IMMEDIATE`.

## Code

- `hermes_state_reconcile.py`

## Tests

- `tests/hermes_cli/test_browse_schema_drift.py`: `test_browse_budget_is_armed_before_the_macos_durability_pragmas` runs the darwin pragma path on every platform (the macOS CI lane covers only `tests/hermes_state`). The existing slow-reconcile test is the behavioral guard.

Test files touched by these patches:

- `tests/hermes_cli/test_browse_schema_drift.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- The fix is only observable on slow stores; the added test pins ordering, not wall-clock behavior.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
