# Desktop write invalidation across preload and IPC

Mirror ordinals: 3953.

## Behavior

Replaces comment-sensitive source substring coverage with an executable test of renderer write notices reaching the main read coalescer. Production behavior is unchanged.

## How it works

The test transpiles the complete preload and executes the actual top-level main IPC registration selected through TypeScript AST statements. HermesGateway talks to a held real WebSocket mutation; Electron transport and unrelated app bootstrap are isolated. Reads on two backend origins are checked before, during and after success or rejection.

## Code

Test-only patch set; production implementation is unchanged. The regression files below are the changed code.

## Tests

- `apps/desktop/src/api/backend-write-ipc.test.ts`: Asserts read RPCs do not invalidate; mutation start and settlement create distinct read generations for both origins, including rejection, while duplicate reads within each generation share answers and transport calls.
- `apps/desktop/electron/read-coalescing.test.ts`: Removes the old string-only wiring assertion; retained unit coverage is not the new bridge proof.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

This executes production bridge wiring in a harness, not a full Electron window. A commented registration cannot satisfy the AST-based test.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
