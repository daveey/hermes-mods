# Private exact-source receipts (UNACTIVATED)

`dashboard/source_receipts.py` defines `panel-source-receipts/1`: a private
measurement log, not a PanelModel change, execution permission or source
certificate. Normal startup installs neither this log nor the optional source
coordinator. B3 alone owns activation. Actual payloads/logs include full
owner/session/card coordinates and must never enter public Git.

## Explicit measurement seam and lifecycle

An authorized in-process measurement owner loads the current receipt module,
constructs `ReceiptLog(private_path, identity=observed_identity)`, then calls
`feed.install_receipts(log)`. Observe boot ID, PID and process birth, imported
runtime/mods/core SHAs, API generation, serving gateway and profile; checkout HEAD
alone is not imported-code identity. Never take identity from an HTTP body. A
feed refuses a second distinct sink. This does not start readers, change the four
source-service roles, or activate a Desktop classifier. A separate bounded async
writer performs only telemetry file I/O.

Retire/drain producers before `log.close()`, check its return value, retain all
numbered rotations and independently read back/hash the private files. Flush and
close outside Feed/coordinator/SQLite locks. Queue overflow, oversized/invalid
records and writer errors invalidate the window. `window_end` accounts for loss;
a missing terminal record is not a drained window. Abrupt termination remains
incomplete: this module installs no signal handler or lifecycle bypass.

Natural reload needs explicit predecessor/successor measurement custody. Do not
silently reuse a fixed API/runtime identity across new generations; preserve
incomplete windows. This logger does not itself attest constructor metadata.

## Immutable publication custody

With a sink attached, each pre-serialized `Feed._wire` entry gets a private frozen
`StoredModel` sidecar: the exact client-bundle bytes and publication metadata
(feed/publication/chat IDs, model revision, admission, input source vector,
completed/missing scopes, dependencies and canonical/computation/observation
clocks). Dataclass-keyed maps retain exact key/value entries, not stringified
keys. Client JSON and PanelModel v1 are unchanged.

`changes_packet()` takes one lock-free wire snapshot and returns HTTP bytes plus
only the sidecars selected by that same session/cursor/epoch filter.
`changes_bytes()` retains its bytes-only API. Later publication/forget/lineage
changes cannot mutate an earlier response. No hydration, SQLite/network read,
reduction or locks enter the warmed selection path. Private sidecars decode for
logging only after ASGI send.

Storage, acquisition success, `_note_publish`, generic callback return and
invalidation alone are not exact inclusion receipts.

## Server emission versus authenticated catch-up

The explicit `_emit.with_receipt` adapter uses real `push_payload` and the normal
core broadcaster. It retains exact post-transform bytes and callback start/end
clocks; only successful return logs `emit_success` with
`observation=server_emission`. This is server emission, **not authenticated client
delivery**, websocket receipt, React commit or native pixels. A generic callback
without the exact adapter logs a measurement error while normal publication
continues. Telemetry does not alter the production push envelope to fit a stored
sidecar; any race mismatch remains unmatched.

The existing 512 KiB fallback is unchanged. Invalidation emits have
`kind=invalidation_only`, actual transformed bytes and **no included model
publications**. They cannot fulfill models; those same revisions remain eligible
for normal scoped catch-up. No chunking protocol is introduced.

With a sink attached, `/changes` reuses core
`admitted_for_sensitive_route(request)`: native/hosted/cookie/session-token
policy, not a body flag or plugin credential comparison. Refusal returns 401
before constructing the private response. Do not log tokens/cookies/principals.
Disposable tests exercise real core hosted-IPC and loopback-token guards, not
production authentication.

`catchup_success` requires true core admission, a successful final ASGI body send
and byte-count/hash equality between actual successful body messages and the
immutable selected response. Failed/cancelled sends, body replacements and
unauthenticated calls log errors. Construction is not write proof. This observes
the local ASGI send seam, not network arrival, downstream rewriting or pixels.

## Validator

Pass private segments in their original numeric order:

```sh
python dashboard/source_receipts.py /private/window.jsonl /private/window.jsonl.1
```

Closed-window accounting is required by default. `--allow-open` is streaming
inspection only. `--require-fulfilled` also exits nonzero for unmatched stored
publications. Output always says `acceptance=not_evaluated`: schema/inclusion
validation does not prove semantic row/action/absence, latency, CPU, contention,
population or native gates.

The audit joins exact publication keys/vectors, payloads, runtime/process identity
and window. Corrupt payloads, cross-window/process claims, duplicate records and
records after closure fail closed. `server_emitted` and
`authenticated_catchup` are distinct counters; publications deduplicate by key.
Unmatched entries are retained, never dropped to improve latency statistics.

## Remaining integration and rollback

This slice does not yet wire source-received/capture/admission/projection/
no-change/superseded/forgotten events, frozen/current demand manifests, stage
I/O/CPU counters, complete writer request/acquire/release clocks or the C event
analyzer. Reserved event names do not mean those producers execute. Callback time
is not the full serialized-publication E budget. B2b-ii must finish this scope and
its original regression matrix/broad suites/review/CI/canonical landing before
delivery. Existing failed projection/performance and all B/C gates remain intact.

Rollback follows normal reviewed deployment and owner fencing; preserve canonical
decisions, stop/drain telemetry and retain incomplete evidence. Never reuse log
sidecars as runtime authority, force a Desktop restart, or equate cached SHA with
activation. A future cold feed without explicit sink retains the ordinary wire
API and reducer behavior.
