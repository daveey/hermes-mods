# Kanban scope renewal: dependents, canonical owner links, foreign evidence and foreign comments

Mirror snapshot 5923; ordinals 5869, 5870, 5871, 5873, 5875.

Entries:

- **5869**: fix(kanban): a renewed, completed scope stop releases its dependents
- **5870**: fix(kanban): defer dependency projection to main's parent_blocks gate
- **5871**: fix(kanban): read missing scope owner links from canonical Board
- **5873**: fix(kanban): read foreign renewal evidence on primary boards
- **5875**: fix(kanban): preserve gateway writer on foreign mandate comments

Builds on the earlier scope-renewal and foreign-card features; see those docs for the renewal model itself.

## Behavior

- **Dependents (5869, 5870)**: a card whose scope stop was renewed, promoted and completed no longer strands its children. 5869 taught the dependency projection to skip a scope stop that a valid renewal covers (exact event uid, unedited mandate digest), failing closed on any read error; safety refusals, unrenewed stops, later stops and administrative containment archives still restrict. 5870 then removed that parallel projection and returned to the mainline `parent_status_sql`/`parent_blocks` gate (which labels restricted terminal parents `<status>:restricted` and releases renewed scope evidence), keeping the new dependency tests, now asserting the mainline gate and labels. Net production change of the pair: `kanban_containment_terminal.py` returns to the mainline projection; the regression tests remain.
- **Canonical owner links (5871)**: `renew-scope` from a foreign-home shadow where only a shepherd row is replicated no longer refuses the real owning chat. After process-ancestry, stopped-card own-run and creator-task checks, a missing owning link is looked up as exact canonical `created` link uids on the Board (explicit profile, plus the legacy NULL-profile uid for the default profile only), requiring full identity, not released, not deleted. Nothing is inserted or projected; the canonical `renew_scope` operation still decides and writes. An unreadable Board refuses with `board_unavailable` and renews nothing.
- **Primary boards (5873)**: renewal evidence for a card that exists only in the full replica is now read through the existing read-only foreign replica opener when the live card is absent, in both primary and shadow sync modes (the previous shadow-only view ignored the replica on primary boards). The foreign card is never imported into the live board.
- **Foreign comments (5875)**: `kanban comment` on a replica-only card appends the comment and its classified writer event through the authenticated Board (portable comment and event uids, exact readback) and preserves the gateway writer identity; an origin-gateway override is refused, the target must be live and canonical, and an uncertain or partial reply is not retried (both portable ids are reported for read-only recovery). Ordinary local comment behavior is unchanged.

## How it works

- `hermes_cli/kanban_scope_renewal_cli.py`: `_canonical_owner(...)` queries `hub.row_evidence(board, 'task_chat_links', uid)` for each candidate uid; `_evidence_connection(...)` picks the live connection or the read-only replica. A `canonical_owner` callback is threaded into the actor-authorization helper.
- `hermes_cli/kanban_foreign_comment.py` (new): `append(...)` returns the canonical comment uid, or `None` for the ordinary local path; it opens the replica read-only, verifies the live Board row, submits `task_comments` and `task_events` upserts in one op batch, closes the preflight socket before POST so a stale-socket retry cannot replay the non-journaled append, then reads both rows back. `hermes_cli/kanban.py` calls it from the comment command.
- `hermes_cli/kanban_containment_terminal.py`: 5869 added a SQL function wrapper over `kanban_scope_renewal.covered`; 5870 deleted it again in favor of the mainline gate.

## Code

Shipped non-test paths:

- `hermes_cli/kanban_containment_terminal.py`
- `hermes_cli/kanban_scope_renewal_cli.py`
- `hermes_cli/kanban_foreign_comment.py` (new)
- `hermes_cli/kanban.py`

## Tests

Source-side tests shipped by these entries; none were executed by this documentation pass, so no pass result is claimed here:

- `tests/hermes_cli/test_kanban_actionability_dependencies.py` (renewed completed scope releases its child; edited mandate, new stop and safety refusal keep it waiting; full and incremental foreign mirrors)
- `tests/hermes_cli/test_kanban_scope_renewal_shadow.py` (canonical created-link lookup, process and transport gates preserved, foreign cases parameterized over shadow and primary modes)
- `tests/hermes_cli/test_kanban_scope_renewal_synced.py`
- `tests/hermes_cli/test_kanban_foreign_comment.py` (hub writer recorded without import, canonical and live target, exact ack and readback, non-owner identity preserved, origin override rejected, uncertain delivery never claims success)

## Applying

These entries are part of the ordered snapshot 5923. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). They are not independent patches.

## Gotchas

- 5869 and 5870 partly cancel in production code: do not re-introduce a second dependency-projection expression next to the mainline `parent_blocks` gate.
- The canonical owner lookup treats an unreachable Board as a refusal, not as "not the owner".
- A foreign comment is not idempotent: on an unconfirmed result, reconcile by the reported uids instead of resending.
- Only the comment command path for replica-only cards is covered; other comment entry points (tool, REST) are untested here.

## Plugin

Core change; no plugin or hook is involved.
