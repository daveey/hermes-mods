# Doctor: whole command-line holders with an argv secret mask

Mirror entries 5027–5036.

## Behavior

`hermes doctor` lists `state.db` holders with their **whole command line**, then masks secrets in those descriptions.

- Credential/cookie/bearer values are masked whole, as is a bare token that is long, mixed alphanumeric and high-entropy.
- Paths (they name where a secret lives), URLs, hex (commits, UUIDs), Hermes ids and tokens with a plain word segment are kept.
- Secret-named flags: the secret word must **end** the flag name; the rule fails closed if any secret word appears unless the name ends in a setting. Numeric values of secret-named flags are masked. Plural secret flags and `NAME=value` assignments are masked. Base64 padding is not a `NAME=value` assignment. `UPPER_SNAKE` env names stay readable in `NAME=value` tokens.
- Paths are judged by their start; numbers and known public flags stay.

## How it works

`hermes_state_holders.py` renders the full command line. `hermes_state_argv_secrets.py` classifies each token. Doctor advice still names who to stop without pointing at the gateway process alone.

## Code

- `hermes_state_holders.py`
- `hermes_state_argv_secrets.py`
- `hermes_cli/doctor_state.py`, `hermes_cli/doctor_platform.py`

## Tests

Source tests (not executed here):

- `tests/hermes_cli/test_doctor_fts_deferral_holders.py` (`test_doctor_lists_holders_with_their_whole_command_line`, `test_holder_descriptions_never_show_secrets_from_argv`)
- `tests/hermes_state/test_argv_secrets.py` (`test_secrets_are_masked`, `test_identifiers_are_kept`, `test_a_flag_value_that_is_itself_a_flag_is_not_swallowed`)

## Applying

Follow the full segmented recipe in the core mods [README](README.md) ("Applying"); never apply these mails on their own.

## Gotchas

- A flag is secret-named only when the secret word **ends** its name (`--token` yes, `--tokenizer` no unless fail-closed wording applies).
- Do not treat base64 padding `=` as an env assignment.
