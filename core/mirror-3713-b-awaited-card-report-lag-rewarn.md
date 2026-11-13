# Awaited-card report_lag re-warns on a new park episode

Patches: **3705**.

## Behavior
- If a warned park (a card waiting on an awaited card that has finished) leaves `scheduled` and re-parks within one throttled scan window, still naming a finished card (same or another), the owner is warned again. A single park episode still warns only once.

## How it works
- 3705 keys the `_WARNED` mark by (awaited card, park episode), the episode being the id of the card's latest `scheduled`/`continued` event. The replica scan and `expedite` re-arm a warned park whose episode or awaited card changed; an unchanged mark is skipped. Cost is one indexed event read per warned park, only on the existing 60 s per-board scan. Comments and docstring no longer say the mark lasts "until it leaves scheduled".

## Code
- `hermes_cli/kanban_awaited_card_wake.py`
- `tests/hermes_cli/test_kanban_awaited_card_wake.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_awaited_card_wake.py`: `test_a_warned_park_that_re_parks_within_one_scan_window_on_a_finished_card_is_warned_again` (parametrized: same card, other card).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
