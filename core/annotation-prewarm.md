# Prewarm session-list Kanban annotation at serve boot

Patches: 1141.

## Behavior

- Serve starts a daemon prewarm of the session-list Kanban annotation during application lifespan startup.
- The read is skipped under `HERMES_TEST_ISOLATION`.

## How it works

Boot starts the existing annotation snapshot read before reconnect polling. A first request can find the completed snapshot or join the read already in flight; this does not create a second annotation implementation.

## Code

- `hermes_cli/kanban_annotation_prewarm.py`
- `hermes_cli/web_server_app.py`

## Tests

Source regression coverage:

- `tests/hermes_cli/test_kanban_annotation_prewarm.py`
- `tests/hermes_cli/test_serve_unloaded_sweeper.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

The source describes consumers including session lists and session detail. No fresh REST latency benchmark or live Desktop reconnect burst is claimed here.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.
