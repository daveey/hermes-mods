# Provider retry deadline survives update pause and continuation

Mirror snapshot 5406; ordinals 5345, 5346, 5347, 5348.

Entries:

- **5345**: WIP retry-wait update pause: pending durable deadline custody
- **5346**: Preserve provider retry deadline across update pause and continuation
- **5347**: Keep restored retry waits pausable and fence legacy forwarding
- **5348**: Cover retry custody review branches and preserve interrupt issuer

## Behavior

- A turn in API-error backoff is a resumable update boundary (**5345**, WIP tests/plumbing).
- **5346**: `provider_retry_deadline.py` is the agent side of a provider-retry field on the turn marker; the absolute deadline is stamped on wait entry and restored across pause/continuation so the provider call is not made early. Retry-wait work registers before turn-start compression and provider-capable hooks.
- **5347**: restored waits stay pausable (tracking uses the prospective request count; the empty-history continuation boundary checks the eligibility placeholder); a legacy bare-text owner forward keeps its marker until the absolute deadline.
- **5348**: review coverage of custody branches; a backend-exit interrupt keeps its issuer instead of being labelled a user stop.

## How it works

- `agent/provider_retry_deadline.py`, `turn_api_call.py`, `turn_api_error.py`, `turn_iteration_prep.py`, `turn_recovery.py`, `update_pause_cancel.py`; `tui_gateway/turn_marker.py`, `prompt_turn.py`, `session_auto_continue.py`, `update_pause.py`.

## Code

Shipped non-test paths (union over these entries):

- `agent/turn_api_error.py`
- `agent/turn_recovery.py`
- `agent/provider_retry_deadline.py`
- `agent/turn_api_call.py`
- `agent/turn_iteration_prep.py`
- `agent/update_pause_cancel.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/turn_marker.py`
- `tui_gateway/update_pause.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/tui_gateway/test_update_pause_retry_wait.py`
- `tests/tui_gateway/test_retry_deadline_custody.py`
- `tests/tui_gateway/test_retry_deadline_declined_pause.py`
- `tests/tui_gateway/test_retry_deadline_integration.py`
- `tests/tui_gateway/test_retry_deadline_prework.py`
- `tests/tui_gateway/test_retry_deadline_gateway.py`
- `tests/tui_gateway/test_retry_deadline_owner.py`
- `tests/tui_gateway/test_retry_deadline_process.py`
- `tests/tui_gateway/test_retry_deadline_repeat.py`
- `tests/tui_gateway/test_retry_deadline_startup.py`
- `tests/tui_gateway/test_retry_deadline_review_controls.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- The wait is a per-turn retry, never a scheduler sleep.
- Producer notes say live latency acceptance was not established; do not treat this as measured.

## Why core instead of a plugin

The retry loop and turn marker are core agent/gateway code.
