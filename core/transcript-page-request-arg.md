# Direct transcript-page tests pass Request

Patch **0693** (`cb8da3bf9a`). Test harness only.

## Behavior

No product change. Direct calls to the history-page helpers pass a Starlette `Request` and an explicit `order`. FastAPI does not apply Query defaults outside the framework, so a positional call bound the old arguments one slot off (`False` landed on `order`) or TypeError'd on a missing `session_id` after a leading `Request` parameter was added.

## How it works

`tests/hermes_cli/test_history_commentary_display.py` builds `Request({"type": "http", "method": "GET", "path": "/", "headers": []})` and passes it into the two direct page callers.

## Code

`tests/hermes_cli/test_history_commentary_display.py` only.

## Tests

Existing tests, callers fixed rather than new cases:

- `test_rest_pages_bind_the_history_owner_for_messages_and_around`
- `test_unscoped_rest_history_uses_custom_home_of_its_database`

This mirror did not re-run the file. The sibling messages-endpoint caller (`test_messages_endpoint_reports_corruption`) is a different commit, covered as the route-argument bind in [spotlight-noindex-scratch.md](spotlight-noindex-scratch.md) (0692). The REST path through FastAPI, where Query defaults apply, is untested by this patch.

## Applying

`git am -3` 0693. Test file only.

## Gotchas

- A new direct call to these handlers must pass `Request`. Copying a positional argument list from before the leading parameter will bind the wrong field and not fail at import time.

## plugin?

No.
