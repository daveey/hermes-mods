# Class vision scan ignores tool schemas; non-retryable local errors log a traceback

Patches: **2388**.

## Behavior

The route vision-capability scan no longer walks tool JSON schemas, so a schema property named `type` (a dict, unhashable in a set membership test) neither crashes the request nor falsely demands vision. A non-retryable client error with no HTTP status (a local failure building/sending the request) now logs a traceback instead of one line.

## How it works

`_requirements` passes only the content keys (messages, input, system, instructions) to the recursive `_has_image` helper, which matches only string `type` values; `turn_recovery` logs the traceback when there is no HTTP status.

## Code

`agent/model_routing_request.py`, `agent/turn_recovery.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/agent/test_model_routing_request_context.py` (real tool-schema shape; image block in messages still counts) and `tests/agent/test_nonretryable_local_error_traceback.py` (local error traces; HTTP error stays one line).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Images inside tool-schema defaults/examples are intentionally ignored. Only class-routed agents reach this scan.

## Plugin

Core change; no plugin required.
