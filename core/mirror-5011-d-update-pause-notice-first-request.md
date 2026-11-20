# Update pause: durable own-marker notices may yield their first request

Patches: **4729, 4730, 4731**

- 4729 (`5f8571d393`): [daveey] Allow durable own-marker notices to yield their first request
- 4730 (`fbcf32c3f3`): [daveey] Exercise notice pause through middleware and cold-session scheduling
- 4731 (`2aea17c037`): [daveey] Isolate notice marker write custody from prompt equality

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- A notice with its own durable, full resume prompt (successful marker write, exact prompt, no inherited pause) is eligible to yield its first request; truncated, previous-pause or unwritten markers are not.
- Tests drive the real throughput middleware and cold-session scheduling; auto-continue opt-out and root prompt/attempt budget are preserved.

## How it works

`tui_gateway/prompt_turn.py` eligibility change; tests add a middleware pause/resume case and isolate marker-write custody from prompt equality.

## Code

- `tui_gateway/prompt_turn.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/tui_gateway/test_update_pause_first_request.py`: `test_a_notice_with_its_own_durable_prompt_may_yield`, `test_a_notice_without_a_successful_marker_write_cannot_yield`, `test_notice_without_its_exact_resume_prompt_is_not_first_request_eligible`, `test_notice_carrying_a_previous_pause_cannot_yield_before_persisting_itself`, `test_notice_with_truncated_marker_cannot_yield_before_persisting_full_prompt`
- `tests/tui_gateway/test_update_pause_notice_middleware.py`: `test_notice_middleware_pause_and_resume`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Marker-write success is a distinct predicate from prompt equality; tests keep them separable.
