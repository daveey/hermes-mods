# Dormant zero-tool business-review adapter

Patch coverage: 6223, 6224, 6225, 6238.

## Behavior

The source adds a zero-tool review adapter with one durable review operation per review-task identity. It is inert with respect to normal dispatch in this range: no production caller invokes it; entry is its module CLI and source tests.

A separate reviewer process receives a bound diff and returns a narrow structured verdict through a class selector, without a tool inventory. Canonical Board admission, settlement and read-only receipt remain authoritative.

## How it works

A private sidecar records envelope/grant/settlement state so restart or a lost reply resumes the same identity rather than starting a second review. Envelope fields bind author/reviewer identities, repository, base/head, full diff and prompt/capability digests. File locks, bounded payloads and explicit lease margins constrain operations.

Diff opens use no-follow and nonblocking flags to reject symlinks/FIFOs without hanging. Verdict text must be nonempty, bounded UTF-8 scalar text. Spawn inventory classifies the subprocess, and the sidecar is explicitly distinguished from a live board connection for lock scanning.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/kanban_business_review.py`
- `hermes_cli/worker_drain_spawn_inventory.json`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_business_review.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

Do not describe this as a live automated reviewer. A local sidecar is recovery custody, not a review approval or permission to resend after uncertain admission. Installer or service activation was not attempted. Platform flock support is required by preparation.

## Why core, not a plugin?

The adapter depends on core review admission/settlement identities and drain-managed subprocess custody, beyond an isolated plugin review prompt.
