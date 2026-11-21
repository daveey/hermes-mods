# Doctor: mask degenerate long hex argv values

Mirror snapshot 5406; ordinals 5325.

Entries:

- **5325**: fix(doctor): mask degenerate long hex argv values

## Behavior

- The documented >12-character hex mask is now applied *before* the numeric and letter identifier exceptions. Random hex tokens can be entirely decimal digits or entirely A-F, and the old rule required both character classes, letting such synthetic secrets through.
- Abbreviated hex, ordinary short numbers and typed identifiers stay readable; random-coverage and leak ceilings are unchanged.

## How it works

- `hermes_state_argv_secrets.py` reorders the classifier so the length-plus-hex rule wins over the identifier exceptions.

## Code

Shipped non-test paths (union over these entries):

- `hermes_state_argv_secrets.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_state/test_argv_secrets.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- A long all-digit value that is a genuine identifier is now masked; the trade-off favours not leaking secrets.
- Producer-reported: 42 deterministic regressions failed before and passed after, with readability controls (producer evidence only).

## Why core instead of a plugin

The redaction of process arguments in diagnostics is done inside core before output is produced; a plugin sees only already-rendered text.
