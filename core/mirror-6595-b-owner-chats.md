# Standing producer owner chats and local origin custody

Patch coverage: 6178, 6179, 6180, 6181, 6182, 6183, 6184, 6185, 6206, 6207, 6220, 6221, 6222, 6287, 6348.

## Behavior

Producer-created cards can have a durable standing owner chat, reused per producer and per repository for self-work intake. Existing real owning chats retain precedence, including through creator ancestry and replica resolution. Agent-created work still requires its ordinary scope authority unless its trusted runner supplies a producer.

The owner chat is pinned and seeded. Only a seed-only chat with no outcome may receive an idle done outcome; actual linked open work overrides that presentation. Creating-chat membership is displayed independently of wake subscriptions.

## How it works

Creation settles ownership within the board transaction. System creation can survive unavailable session storage with a recorded unavailable-owner event; bounded maintenance backfill repairs ownerless cards later. Backfill rechecks dead owners, restores archived sessions, rotates deleted identities, preserves real releases, isolates producer failures and applies an explicit retry cadence. Observer subscriptions and reasonless legacy subscriptions do not count as owners.

Self-work producer keys use repository, not requester-chosen area. The trusted ensure-and-attach path can issue immutable local native origin custody for a newly ensured producer session on its primary board. Plain link projection, foreign task rows and nonprimary attachment cannot issue that receipt. The receipt commits or rolls back with attachment.

## Code

Affected implementation and integration paths in the source series:

- `cron/monitor.py`
- `cron/scheduler_prompt.py`
- `cron/scheduler_script.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_graph.py`
- `hermes_cli/kanban_dispatch_maintenance.py`
- `hermes_cli/kanban_local_chat_ownership.py`
- `hermes_cli/kanban_origin_creation.py`
- `hermes_cli/kanban_owner_chat.py`
- `hermes_cli/kanban_owner_origin.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_selfwork_intake.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_outcome_tasks.py`
- `hermes_cli/kanban_swarm.py`
- `tools/kanban_tools.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_owner_chat.py`
- `tests/hermes_cli/test_kanban_selfwork_owner_chat.py`
- `tests/cron/test_cron_workdir.py`
- `tests/hermes_cli/test_owner_chat_origin_forward.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

kanban.owner_chats can disable the feature. Producer environment declarations inside an ordinary agent turn do not mint authority. An owner link is not automatically a notification subscription or a native origin issuer. Idle-outcome failure must not fail card creation.

## Why core, not a plugin?

Ownership is a core card-creation invariant involving board transactions, session provenance, cron runners and origin receipts; a post-create plugin would leave authority gaps.
