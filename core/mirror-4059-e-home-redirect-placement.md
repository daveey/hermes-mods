# Home-state placement with shell redirects

Mirror ordinals: 3971, 3987.

## Behavior

Recognizes more valid redirects on home-directory commands and preserves the portable code-tree exemption after a redirected state-directory transition.

## How it works

The redirect grammar accepts single-quoted spaced targets, noclobber >|, whitespace after >& and command-substitution targets with one nesting level. The state-directory negative lookahead skips the command's redirects before checking the next cd/pushd into a code tree. An actual directory operand after redirects still changes the destination; literal ~2 is not home plus an fd redirect.

## Code

- `hermes_cli/kanban_placement.py`

## Tests

- `tests/hermes_cli/test_kanban_state_path_home_forms.py`: Positive home forms and negative repo-operand/~2 controls pin the grammar. Single-quoted targets retain the same placement result with or without hub:read requirements. Redirected state-dir-to-code-tree transitions remain portable; existing linear-time checks remain in the suite.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

This is bounded pattern recognition, not a complete shell parser; the added substitution form supports one nesting level. The mandatory historical hostname escape repair remains required even though this patch edits placement regexes.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
