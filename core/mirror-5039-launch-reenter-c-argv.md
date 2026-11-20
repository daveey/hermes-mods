# Launch: re-entered `-c` keeps argv on its command line

Mirror entry 5016.

## Behavior

When a process is re-launched through a `-c` stub, the attested command line still carries the original argv (not only the rewritten stub). A mismatch, extra statement, non-canonical exec literal, other interpreter option, or trailing argv other than `sys.argv` is refused.

## How it works

`hermes_cli/code_attestation.py` (with `venv_sync.py`) treats a re-entry of the current stub as attested through a spaced interpreter while still requiring the orig argv to appear on the process command line. Rewritten orig-argv is not evidence.

## Code

- `hermes_cli/code_attestation.py`
- `hermes_cli/venv_sync.py`

## Tests

Source tests (not executed here): `tests/hermes_cli/test_code_attestation_relaunch.py`

- `test_relaunch_of_the_current_stub_is_attested_through_a_spaced_interpreter`
- `test_argv_mismatch_refuses`, `test_extra_statements_refuse`, `test_module_body_refuses`, `test_non_canonical_exec_literal_refuses`, `test_other_interpreter_option_refuses`, `test_prefix_differing_in_one_byte_refuses`, `test_rewritten_orig_argv_is_not_evidence`, `test_run_path_body_refuses`, `test_tampered_embedded_script_refuses`, `test_trailing_argv_other_than_sys_argv_refuses`

## Applying

Follow the full segmented recipe in the core mods [README](README.md) ("Applying"); never apply this mail on its own.

## Gotchas

- Attestation is command-line evidence, not the rewritten in-process argv array.
