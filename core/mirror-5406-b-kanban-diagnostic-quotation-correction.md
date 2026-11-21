# Kanban: begin audited diagnostic quotation correction (work in progress)

Mirror entries 5223.

## Behavior

- `quote_diagnostic(original, diagnostic)` inserts quotation marks around an exact occurrence of a diagnostic string and refuses unsafe spans; every other byte is preserved.
- `correct_diagnostics` is an operator-only local compare-and-set that keeps the original comment identity and writes an audit; sync convergence must be verified separately.
- The commit is labeled `wip`: nothing in this mail wires it into a CLI or dispatcher (untested and not shown in the mail's paths).

## How it works

- New `hermes_cli/kanban_diagnostic_correction.py` plus its test file only.

## Code

- `hermes_cli/kanban_diagnostic_correction.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_kanban_diagnostic_correction.py` (`test_atomic_correction_keeps_original_comment_identity_and_audit`, `test_quote_plan_refuses_unsafe_spans`, `test_quote_plan_preserves_every_other_byte`). Any CLI exposure or Board convergence is untested.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- WIP: behavior may be completed by later mirror entries outside this batch.

## Why core instead of a plugin

It rewrites core kanban comments with core audit semantics; there is no extension point for that.
