# Fixed lightweight session prefetch projection

Mirror entries: 4847, 4848.

## Behavior

Desktop transcript prefetch requests the fixed lightweight session projection while remaining compatible with older servers that return full rows.

## How it works

The host request opts into projection=prefetch without changing selection, freshness, Inbox injection or polling cadence. A deterministic synthetic fixture compares full and projected envelopes and the fixed fields used for prefetch. Tests load JSON explicitly without expanding the TypeScript composite project.

## Code

- `apps/desktop/electron/transcript-cache-host.ts`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Transcript-cache-host tests assert the projection request and acceptance of legacy full responses. Prefetch-projection tests cover missing/null fields, profile normalization, compression scope, stable ties, Inbox priority and removal of display-only replica payload while retaining the response envelope.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `apps/desktop/electron/transcript-cache-host.test.ts`
- `apps/desktop/electron/transcript-prefetch-projection.test.ts`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

The server endpoint implementation is a separate dependency; this core change requests it rather than implementing it. Full-response compatibility is deliberate. No production latency, traffic or recovery result is claimed.
