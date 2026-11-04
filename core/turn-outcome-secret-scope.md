# Post-turn outcome refresh in the session's profile scope

Patches: **1714** (`cff30b6592`, `[daveey] bind session profile scope for post-turn outcome refresh`); **1715** (`f9e26960a9`, `[daveey] distinguish foreign and launch credentials in outcome regression`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- The post-turn outcome refresh binds the session's profile secret scope, so it reads that profile's credentials, not the launching process's (1714); the regression distinguishes foreign and launch credentials (1715).

## How it works

`tui_gateway/turn_outcome.py` wraps the refresh in the session's profile scope.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1714: (subject only)
- 1715: (subject only)

## Code

`tui_gateway/turn_outcome.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/tui_gateway/test_turn_outcome_secret_scope.py`: `test_post_turn_refresh_scopes_judge_and_restores_caller`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
