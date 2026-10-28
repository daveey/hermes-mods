# Retirement ledger reads BOM JSON; nested binary open is not a text footgun

Patch **0688** (`b3489b119f`). Follow-up to the SSH runtime-marker work in [desktop-always-on-remote-dashboard.md](desktop-always-on-remote-dashboard.md) (0671, `8e6534e7ad`). Both bugs landed in that commit; its push CI was cancelled before the Windows footguns job ran.

## Behavior

- `RuntimeRetireLedger` reads the ledger as `utf-8-sig`. A BOM-prefixed JSON file is the ledger, not an empty dict. An unreadable or non-dict file is still `{}`.
- `scripts/check-windows-footguns.py` does not flag `open(os.path.join(...), "rb")` (or the same shape with another binary mode) as a text-mode open. The mode regex used to stop at the first comma and treat an inner argument as the mode. A real text open with no `encoding=` is still flagged.

## How it works

`path.read_text(encoding="utf-8-sig")` strips a leading BOM before `json.loads`. The footgun checker drops a trailing comment (quotes tracked), then treats a captured "mode" as binary when it is not a mode literal and the call still contains a binary-mode string. A nested call whose mode really is text stays flagged.

## Code

`hermes_cli/web_server_skew_exit.py` (`RuntimeRetireLedger`), `scripts/check-windows-footguns.py`.

## Tests

- `tests/hermes_cli/test_web_server_skew_exit.py::test_runtime_retire_ledger_reads_a_bom_prefixed_file`
- `tests/scripts/test_footgun_nested_binary_open.py::test_nested_binary_open_is_not_a_text_footgun` (parametrized on source and whether it is flagged)

This mirror did not re-run those files. A live restart-loop reading a BOM ledger on disk is untested; the unit test writes the file and reads it back. `scripts/check-windows-footguns.py --all` against the whole tree is untested here.

## Applying

`git am -3` 0688. Needs 0671's skew-exit module.

## Gotchas

- `utf-8-sig` only strips a BOM. Other encodings still fall through to `{}`.
- Do not widen the footgun exemption to every `open()` that mentions `"rb"` in a comment. The checker ignores a `#` comment; a string that contains `rb` still counts.

## plugin?

No.
