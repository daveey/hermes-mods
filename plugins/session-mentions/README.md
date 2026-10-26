# Cross-task auto-shepherd routing

`cross_task` read and comment opportunistically link a remote task to the calling
session using `hermes-hub request … shepherd`. List does not adopt tasks. Missing
replica/CLI, unknown/local tasks, and dispatch failures do not change tool output.

## Ownership and deduplication

- Replica `board/task` keys are normalized to `default/board/task` at the sender
  boundary. Current `profile/board/task` keys retain their **owner** profile.
  The `--arg profile=…` argument separately identifies the **sender** session.
- An explicit board must match the board component. A missing board match never
  falls back to another board. Multiple distinct gateway/profile/board routes
  are ambiguous and produce no request; equivalent legacy/canonical rows count
  once. Malformed routes are ignored. The receiver's strict three-part validator
  remains unchanged.
- The ledger key is `session|gateway/owner_profile/board/task`; the request ID is
  `shep-` plus the first 32 hex digits of its SHA-256. Repeated canonical requests
  are suppressed. A previously rejected two-part request has a different ledger
  key and request ID, so a corrected read/comment can queue once without deleting
  old ledger entries or reusing a rejected ID.
- The ledger records spawning, not confirmed delivery. This change is not a
  general retry/acknowledgment system. Old rejected requests and their notices
  need separate reconciliation. Do not reset the whole ledger or resend old IDs.

## Failure analysis

The sender previously forwarded legacy replica keys unchanged (`locate` stripped
only the task component; `maybe_shepherd` appended it again). The installed hub
receiver's `decode_task_key` accepts exactly `profile/board/task`, so these
requests were rejected. Normalizing at the sender boundary fixes that contract
mismatch without weakening the receiver.

Why wasn't it caught? The original `test_cross_task` dispatch expectation itself
required `default/task`, and no receiver validator was exercised. The route
selection also treated a board as a preference (`filtered or hits`) and chose
the first result, hiding collisions. Tests now exercise owner profiles, strict
board selection, ambiguity and the actual installed decoder (AST-isolated to
avoid client side effects), or its explicit equivalent contract when the optional
client is absent. The original sender commit had no intervening sender fix.

## Tests

From the repo root with pytest installed:

```sh
python -m pytest -q plugins/session-mentions/tests
```

`test_cross_task.py` covers read/comment dispatch, deduplication, local/unknown
and list no-ops, and failure isolation. `test_shepherd_routes.py` covers legacy
and canonical ownership, a nondefault owner distinct from the sender, board
selection/mismatch, profile/gateway ambiguity, alias collapse, malformed routes,
and correction of rejected legacy identities. Both read and comment entrypoints
run against the receiver decoder contract. Set `HERMES_HUB_CLIENT` to a client
source file to explicitly select that validator. Dispatches are captured in
memory: these tests send no real hub messages.

## Deployment

Update the linked plugin checkout on each sender. Fresh plugin imports use the
new code; already-running Python processes may retain cached modules. Updating
files alone does not prove hot activation. Do not restart active sessions to
force adoption; use a fresh process for corrected replay and let existing
processes reload naturally. No receiver change or replica rewrite is required.
