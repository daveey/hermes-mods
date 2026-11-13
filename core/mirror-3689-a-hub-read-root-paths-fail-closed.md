# Hub-read root paths: keep every path when a traversal is hidden

Patches: **3672**.

## Behavior
- A declared, attested hub-read card no longer clears its Hermes-home root paths when the text could move or compute a path without a literal `..`: quoted or backslash-split dots, `$(`/`${`/`$'`/`$"` expansions, or a cd/pushd/popd other than into a plain absolute or home path outside the Hermes home. Placement fails closed (all paths kept).
- A quote glued to a non-space right before the path (`--file="`) is not treated as a closing quote.
- `cd /tmp` still clears.
- Impact of the old gap was misplacement only; a pool worker reaches the hub through the restricted hub-read, which enforces its own roots.

## How it works
- 3672 changes `_ends_word` and `_strip_read_paths` in `hermes_cli/kanban_placement_coordination.py` to scan the whole text for those constructs.

## Code
- `hermes_cli/kanban_placement_coordination.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_hub_read_roots.py`: `test_controls_still_pin` (11 new rows), `test_declared_read_step_is_portable` (2 new portable rows).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Conservative by design: legitimate text using expansions or cd near these paths keeps its paths and may stay on the hub.
