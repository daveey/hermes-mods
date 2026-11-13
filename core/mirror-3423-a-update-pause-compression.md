# Compression yields during a core-update pause

Patches: **2934, 2935, 2936**.

## Behavior
- A context compression may yield to a core-update pause only where the unchanged transcript reaches a step boundary; yields that did not spend an attempt are refunded.
- A scripted tool tail before any API call cannot yield; the first (reserved) preflight after a scripted prelude does not yield; a forced overflow preflight yields without spending an attempt; a declined post-tool pause refunds the attempt.
- A replayed prelude no longer reuses the previous turn's compression boundary count.

## How it works
2934 passes `api_call_count` into `compression_can_yield` and refunds yields in `turn_preflight`; 2935 excludes the reserved preflight call from pause eligibility; 2936 resets the boundary count at turn entry in `conversation_loop`.


## Code
- `agent/compression_facade.py`
- `agent/conversation_loop.py`
- `agent/turn_preflight.py`
- `agent/update_pause_cancel.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_update_pause_compression.py`: `test_declined_post_tool_pause_refunds_attempt`, `test_first_preflight_after_scripted_prelude_does_not_yield`, `test_forced_overflow_preflight_yields_without_spending_attempt`, `test_replayed_prelude_does_not_reuse_previous_turn_call_count`, `test_scripted_tool_tail_before_any_api_call_cannot_yield`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
