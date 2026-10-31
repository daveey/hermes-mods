# Canonical served identity in chat replies

Patch 1369; source `970d5e40c12d5d33bf7c90faeb4d414057994e54`.

## Behavior

Non-streaming replies project executed provider/model strings from canonical FIFO results; nested served_runtime remains preferred. Empty or non-string metadata does not overwrite request identity.

## How it works

_apply_served_runtime falls back to top-level result metadata and accepts only nonblank strings.

## Code

gateway/platforms/api_server_openai_routes.py

## Tests

Source regression inventory: tests/gateway/test_api_served_identity.py (six projection cases); tests/gateway/fixtures/api_authority_peer.py (canonical API provider/model assertions).

These runtime suites were not rerun in this mirror pass. Executed mirror inventory/replay/tree checks are recorded in [mirror-1375-verification.md](mirror-1375-verification.md); they do not certify runtime behavior.

## Applying

Apply the complete snapshot using the [README replay recipe](README.md#applying), not this patch independently.

## Gotchas

Streaming identity is outside this patch.

## Plugin?

This patch mirrors the source implementation; no separate public mod installation is required.
