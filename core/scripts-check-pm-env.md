# scripts/check: quiet skip of a pm environment without JSON

Patches: **1806** (`df6afc0420`, `[daveey] scripts/check hook: skip a pm environment that yields no JSON without a traceback`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- The pre-push hook skips a composing checkout whose `pm.environments` output is empty or not a JSON object, without printing a traceback.

## How it works

`scripts/check`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1806: On a checkout with no virtualenv the hook's python can be the CLT python3.9, which cannot run pm.environments; it printed nothing and the inline parser raised JSONDecodeError on every push (the push still succeeded, but agents read the traceback as a failure). Empty or non-object output now exits 1 quietly, so the existing '|| continue' skips that composing checkout.

## Code

`scripts/check`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/scripts/test_code_health_cli.py`: `test_hooks_skip_a_pm_environment_that_yields_no_json_without_a_traceback`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
