# Byte-verified gzip transcript deltas and bounded recovery

Patch coverage: **5839–5840**.

## Behavior

- Remote transcript GETs combine slim/gzip reads with `zstd-gzip-v1` negotiation; ordinary list reads retain the original delta protocol.
- Hashes and dictionary bases identify exact compressed bytes. Corrupt or missing bases are dropped before a bounded retry.
- Oversized gzip expansion spends the existing GET retry on identity encoding, with a 64 MiB received-byte recovery limit.

## How it works

The server accepts gzip entities only under the new capability. Whole responses retain gzip; delta responses remove outer `Content-Encoding` and declare `X-Hermes-Delta-Content-Encoding: gzip`. The client reconstructs and verifies entity bytes before gunzip and caches only successful verified 200 entities. Old servers without a body hash remain readable but do not seed compressed bases. Entity reconstruction is bounded to 8 MiB and gunzip to 16 MiB. `GzipExpansionError` clears bases and selects identity headers, stripping delta negotiation; a second oversized or noncompliant recovery fails instead of looping. The middleware stops buffering a response that exceeds its declared length.

## Code

- `hermes_cli/web_delta_response.py`: capability, entity eligibility and delta framing.
- `apps/desktop/electron/{delta-reads,display-transcript,json-fetch}.ts`: negotiation, verification, decode and transport recovery.
- `scripts/bench_gzip_transcript_delta.py`: synthetic body-byte/CPU benchmark; not a measured result here.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

- `tests/hermes_cli/test_web_delta_response_gzip.py`: exact whole/unchanged/changed bytes, credential isolation, old capability passthrough, corrupt disk bases, encoding exceptions and store/length bounds.
- `apps/desktop/electron/delta-reads-gzip.test.ts`: Python-produced zstd prefix frame, real HTTP fetch negotiation/retry, invalid frame/hash/coding/base, old-server behavior, and local/remote whole/delta identity recovery with bounded failures.
- `apps/desktop/electron/{delta-reads,display-transcript}.test.ts`: transcript header selection, exact gzip coding and gunzip bounds.
- `tests/hermes_cli/test_session_messages_display_transcript.py`: a valid large page retains exact identity recovery.

## Applying

Apply the covered patches in the full ordered core series using the [core applying recipe](README.md#applying), including its replay bridges and required repair overlay. These entries depend on earlier core changes; they are not advertised as an independent cherry-pick set. Reimplementation should preserve the invariants above and run the named source suites in a disposable source checkout.

## Gotchas

Plain `zstd` is not consent to gzip-entity deltas. A transport hash is not authorization. The identity cap counts actual chunks, not a trusted `Content-Length`; no benchmark savings are asserted by this documentation.

## plugin?

No: both gateway middleware and Electron transport must agree on wire representation and retry semantics.
