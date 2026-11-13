# Lifecycle guard scans scripts behind shell reserved words

Patches: **3512**.

## Behavior
- Inside a supervised gateway, a script that restarts the gateway was already refused when run plainly, but not when prefixed by `time`, `!`, `if ...; then`, `{`, `while ...; do` or `coproc`. The guard now scans scripts behind these words and refuses them.
- Interpreter runs (`time python3 x.py`), `sh -c` payloads behind reserved words, `launchctl submit` detection and the over-budget fail-closed path get the same treatment.
- `time(1)` used as a program (`/usr/bin/time`, `command time`) is treated as a transparent wrapper.
- A mere mention of a script behind a reserved word is still not treated as a run.

## How it works
3512 adds `_command_segment_views()`, which yields each command segment as written and, when it starts with reserved words, again without them (`_peel_command_keywords`). The script, interpreter and payload walks and the launchctl-submit check read every view, then peel env assignments and wrappers as before. The change is additive: no reference is ever removed. `coproc` joins the prefix keywords (and skips its NAME), and `time` joins the transparent wrappers, with `-f/--format/-o/--output` taking a value.

## Code
- `cron/lifecycle_guard.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_reserved_words.py`: `test_script_behind_a_reserved_word_is_scanned`, `test_interpreter_run_behind_a_reserved_word_is_scanned`, `test_shell_payload_behind_a_reserved_word_is_scanned`, `test_launchctl_submit_behind_a_reserved_word_is_detected`, `test_unscannable_script_behind_a_reserved_word_fails_closed`, `test_reserved_word_does_not_turn_a_mention_into_a_run`, `test_keyword_peel_is_additive`, `test_hint_survives_reserved_words`.
- `tests/tools/test_lifecycle_guard_hermes_sync_hint.py`: modified (hint test now goes through the full refuse path); no new test names in this patch.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
