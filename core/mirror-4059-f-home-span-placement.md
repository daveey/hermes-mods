# Home-state placement across nested directory changes

Mirror batch 6; ordinals 4055.

## Behavior

Card placement preserves an outer home-relative state span when nested subshell/substitution directory changes do not change the outer working directory. pushd -n also does not terminate that span.

## How it works

Balanced parenthesis groups up to depth three and substitution backticks opened after assignment/quote are atomic span steps. Real top-level cd/pushd still ends the span; inner home transitions keep spans disjoint. A nested-prefix expression still allows relevant state-path matches inside a group. Possessive/atomic alternatives bound backtracking.

## Code

Source paths touched by this feature group (including regression fixtures):

- `hermes_cli/kanban_placement.py`
- `tests/hermes_cli/test_kanban_placement_home_spellings.py`

Mail entries in the ordered series:

- 4055: [`5391a156b8a617e4c28cee583b2d5f5fe071f10d`](patches/4055-daveey-placement-a-cd-nested-in-or-never-ends-the-home-span-pushd-n-neither.patch)

## Tests

`tests/hermes_cli/test_kanban_placement_home_spellings.py`: raw regex and placement decisions retain home-state targeting after chained nested cd, command/backtick substitutions and pushd -n. Top-level directory changes, unclosed groups and markdown backticks remain controls; raw nested backtick cases and large nonmatching bodies pin correctness and bounded scan time.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

This is bounded textual placement classification, not shell execution. Over-depth/unclosed groups retain prior plain-text behavior. Applying still requires the series’ mandatory hostname escape repair; a normalized tree alone does not establish regex validity.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
