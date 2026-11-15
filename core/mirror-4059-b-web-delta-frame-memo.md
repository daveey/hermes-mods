# Credential-scoped web delta frame memo

Patches: 3836.

## Behavior

Repeated polls for the same credential namespace, base hash and body hash reuse the encoded delta, including the decision that a delta was not smaller than the full body. Changed bodies and different credentials/bases remain separate.

## How it works

`BodyStore` adds an LRU frame map with a byte budget and entry cap. The frame key combines namespace, requested base digest and current body digest; cached `None` represents an unprofitable encoding. `_encoded` still resolves the credential-scoped base from the body store before consulting frame memoization and stores the new body. If the base is absent/evicted, it sends the full body even when an old frame exists. `delta.encode` accounting marks cache hits as skips.

## Code

- `hermes_cli/web_delta_response.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_web_delta_response_memo.py` (patch 3836).

- **3836:** ASGI polling fixtures decode deltas and require one encode for repeated unchanged requests, new encode on changed body, credential/base isolation, whole-body fallback after base eviction, byte/entry bounds including cached None and accounting skips.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

The frame cache does not authorize access to another credential's base. Byte and entry bounds both matter because cached `None` uses no frame bytes. Hashing/response production still happens; this only avoids repeated encoding. These tests establish functional memo behavior, not a new measured network throughput claim.

## Plugin?

No. The core HTTP delta protocol and credential-partitioned response store own this encoding cache.
