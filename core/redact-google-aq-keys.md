# Redaction masks Google AI Studio AQ. keys

Patches: **1917** (`d5e6c396d4`, `fix(redact): mask Google AI Studio AQ. auth keys [daveey]`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- `agent/redact.py` masks Google AI Studio API keys of the `AQ.` + ~50 `[A-Za-z0-9_-]` form, as it already did for `AIza...` keys.

## How it works

A new pattern in `agent/redact.py`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1917: AI Studio now issues Gemini API keys as `AQ.` + ~50 chars of [A-Za-z0-9_-]; they work as x-goog-api-key / ?key= like legacy AIza keys. redact.py only knew AIza..., so an AQ key printed by a tool reached chat, logs and state.db in cleartext.

## Code

`agent/redact.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/agent/test_redact.py`: `test_key_shape_is_53_chars`, `test_plain_text_masked`, `test_env_assignment_masked`, `test_url_key_query_masked`, `test_json_value_masked`, `test_faq_prose_and_short_aq_untouched`
- `tests/plugins/test_a2a_plugin.py`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
