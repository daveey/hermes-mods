# Worker origin inheritance and durable forwarding refusals

Patch coverage: 6239, 6262, 6263, 6277, 6369, 6370, 6371, 6374.

## Behavior

Worker-created follow-up work can discover its existing origin through the board shadow when pickup did not carry foreign chat links. A native same-home creator can survive forwarding only with exact local native attestation. Missing-binding refusals are delivered durably to the actual owning chat before the courier finalizes rejection.

Legacy NULL chat profiles normalize to default for proven foreign ownership. Execution placement alone does not identify a card home or adoption destination.

## How it works

Placement resolution falls back from live created-chat links to replica owner discovery, but the destination still authenticates creator/run custody. An optional creator envelope field must equal the exact destination scope and its owner-local receipt; the registry keyword carrying it is not a caller-supplied authority shortcut.

Late rejection publication revalidates the server-stamped request, resolves canonical session lineage and records an idempotent diagnostic without creating a chat or binding. Busy, compressing or unreadable destinations retain a pending retry. A unique replica owner is needed for an adoption hint. The scheduling test fixes its published park cap to the same scheduling clock.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_create_authority.py`
- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_origin.py`
- `hermes_cli/kanban_origin_creation.py`
- `hermes_cli/kanban_origin_forward_adapter.py`
- `hermes_cli/kanban_origin_forward_decision.py`
- `hermes_cli/kanban_origin_forward_notice.py`
- `hermes_cli/kanban_origin_owner.py`
- `hermes_cli/kanban_session_provenance.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_placement.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/tools/test_kanban_worker_review_origin.py`
- `tests/hermes_cli/test_origin_forward_creator.py`
- `tests/origin_transport/test_creator.py`
- `tests/hermes_cli/test_origin_forward_rejection_notice.py`
- `tests/hermes_cli/test_kanban_foreign_desktop_owner.py`
- `tests/hermes_cli/test_kanban_origin_ledger.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

A replica route is discovery, not permission. Empty profile strings remain invalid; only the legacy NULL spelling receives default normalization. Successful forwarding must not publish a rejection notice, and an unavailable notice store must not produce a silent terminal rejection.

## Why core, not a plugin?

Courier envelopes, creator attestation, session lineage and durable rejection delivery are cross-process authority boundaries maintained by core.
