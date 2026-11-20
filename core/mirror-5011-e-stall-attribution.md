# Callback wall-time and passive HTTP body attribution

Mirror entries: 4796, 4824.

## Behavior

Existing diagnostic snapshots include slow callback wall/inclusive CPU records and passive ASGI body/lifecycle counters, with fixed safe route labels rather than raw request identifiers.

## How it works

Callbacks at or above 500 ms produce bounded records on the existing snapshot cadence. Inclusive callback CPU is not added to residual CPU totals. An outer ASGI wrapper observes only traffic the application consumes or successfully sends; it does not drain bodies, buffer streams or open another CPU accounting scope. Global counters remain cumulative. Successful matched response headers select an allowlisted route; early exits, unsuccessful statuses and unmatched paths use other. Five literal sync/courier method/path pairs extend the bounded allowlist.

## Code

- `hermes_cli/http_body_accounting.py`
- `hermes_cli/loop_accounting.py`
- `hermes_cli/loop_callback_latency.py`
- `hermes_cli/web_server_transport.py`
- `website/docs/developer-guide/stall-attribution.md`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Callback tests cover controlled sleep versus CPU, nested accounting, exceptions, cancellation, threshold/fork reset and bounded safe identities. HTTP tests cover streaming, unread bodies, successful-send accounting, disconnect/failure outcomes, inflight streams, concurrency, cumulative conservation, privacy and route overflow. Static-route regressions cover exact method/path pairs, query/trailing slash handling and negative statuses/suffixes.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_http_body_accounting.py`
- `tests/hermes_cli/test_loop_callback_latency.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

ASGI body bytes are not wire bytes. Diagnostics attribute observed stalls; they do not establish their historical root cause or prove a production latency fix. WebSocket and lifespan traffic pass through, and delayed route classification must not subtract cumulative global observations.
