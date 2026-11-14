# Explicit handshakes in asynchronous regression fixtures

Patches: 3804, 3817, 3821.

## Behavior

Test-only fixes wait for the actual observable boundary instead of a preceding ledger write or process spawn, and isolate process-dock output from the host's shell startup files.

## How it works

Delegation's mixed-batch test drains matching queued events until its own non-notice completion arrives. The worker persists its completion row before queueing the event, so the ledger is not the synchronization point; early child notices are still collected first and remain forbidden by the assertion.

The dock fixture pins `bg_login_argv` to an rc-free bash invocation so an idle process really has no startup output. The Git lock fixture starts a batch-check reader and requires its `HEAD` response before attempting reclaim; `Popen` alone can precede visible argv in the process scanner. All three changes target tests, not production process behavior.

## Code

Test-only changes; production code is not modified by these mails. The targeted core contracts are described above.

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/tools/test_delegate_auto_resume_silent.py` (patch 3804).
- `tests/hermes_cli/test_process_dock.py` (patch 3817).
- `tests/hermes_cli/test_gitlock.py` (patch 3821).

- **3804:** The mixed-batch test now waits for its own queued completion and still requires exactly one non-notice batch result with no early child-failure notice.
- **3817:** The dock fixture uses rc-free bash and still asserts the idle starting label, process block ordering, failure exit display, viewport width and retirement behavior.
- **3821:** The live-reader test requires a HEAD commit answer before reclaim, then still asserts reclaim refuses and index.lock remains while Git is working.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

Do not replace these handshakes with a fixed delay. RC-free bash is fixture isolation, not a change to users' login-shell behavior. A completion queue event and a ledger row are separate observations.

## Plugin?

No. These are core test fixture repairs.
