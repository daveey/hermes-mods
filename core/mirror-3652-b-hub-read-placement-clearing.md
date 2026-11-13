# Declared hub:read clears read-only hub paths in kanban placement

Patches: **3634–3640**.

## Behavior
- A card that declares `requires: [hub:read]`, where every destination attests `capability:hub-read`, no longer gets pinned to the hub by read-only hub access in its text. Two kinds of token are dropped from the placement marker scan:
  - an ssh call to the hub using one of the hub-read subcommands (cat, tail, head, ls, grep, sqlite, status, journal, units, git, help). A bare ssh, or ssh to any other host, still pins.
  - `~/.hermes/<p>` and `/home/<user>/.hermes/<p>` paths whose `<p>` lies under the `kanban.placement.hub_read_roots` setting. Unset or malformed roots clear nothing.
- Only the path token is removed, so a write verb elsewhere in the card (restart the gateway, systemctl, `hermes config set`) still pins. Undeclared cards and claim refusal behave as before.
- Path clearing is deliberately strict. A root path is cleared only if it ends its shell word. These shapes keep pinning: an operator glued to the path (`&&`, `:`), dot-only segments such as `logs/..`, quoted/brace/`$`/backslash/glob remainders, a quote that was not opened right before the path, more than one trailing dot, a quote left open after the path, a backtick glued to the path, and any text holding a standalone `..` segment.
- Backtick spans are still treated as names, as on main, with or without `hub:read`.

## How it works
- 3634 adds the declared-`hub:read` stripping in `kanban_placement_coordination.py` and calls it from the diagnostics `portability()`.
- 3635 restricts the path to the character class `[\w.@+%~/-]*` and refuses dot-only segments before a trailing sentence dot is trimmed.
- 3636 requires a word-end lookahead after the path.
- 3637 replaces the lookahead with `_ends_word`: a trailing quote counts only if the same quote opened the word just before the prefix, then at most one optional `)`, then at most one sentence mark before whitespace, end or a shell operator. It also tightens the `/home/<user>` segment.
- 3638 makes `portability()` strip root paths from the work text before backtick spans are dropped, so `_ends_word` can see a glued backtick.
- 3639 preserves backtick boundaries when stripping (follow-up to 3638).
- 3640 requires the same quote right after the path when the word opened with a quote, and keeps every path when a standalone `..` segment is present.

## Code
- `hermes_cli/kanban_placement_coordination.py`
- `hermes_cli/kanban_placement_diagnostics.py`
- `tests/hermes_cli/test_kanban_hub_read_roots.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_hub_read_roots.py`: `test_declared_read_step_is_portable`, `test_other_hub_read_shapes_are_portable`, `test_undeclared_card_still_pins`, `test_pool_without_label_still_pins`, `test_unset_roots_fail_closed`, `test_malformed_roots_clear_nothing`, `test_controls_still_pin`, `test_claim_refusal_unchanged`, `test_backtick_spans_follow_main_with_or_without_hub_read`.
- Patches 3635–3637 and 3639–3640 extend this file with additional pin-control cases whose names are not newly declared in the patches; they are untested here beyond what the listed tests cover.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The quote/word-end rules were tightened over several review rounds; each patch closes a bypass found in the previous one, so apply the whole range together.
- Failing closed is intended: ambiguous shell shapes keep the card pinned to the hub.
