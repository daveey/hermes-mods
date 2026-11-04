# Failed-turn boundary explains a provider refusal

Patches: **1749** (`3a9c81260f`, `fix(chat): explain provider refusal in failed-turn boundary [daveey]`); **1750** (`16d4f566e0`, `test(chat): assert refusal boundary through ACP history [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- When the provider refuses (content policy), the failed-turn boundary says so instead of a generic failure (1749); the boundary is asserted through ACP history (1750).

## How it works

`agent/turn_failure_copy.py`, `turn_truncation.py`, `conversation_loop.py`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1749: (subject only)
- 1750: (subject only)

## Code

`agent/conversation_loop.py`, `agent/turn_failure_copy.py`, `agent/turn_truncation.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/acp_adapter/test_failed_turn_closure.py`
- `tests/agent/test_content_policy_refusal_stop_details.py`: `test_missing_or_malformed_category_still_explains_refusal`, `test_other_failure_keeps_generic_visible_boundary`, `test_refusal_category_reaches_visible_boundary`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
