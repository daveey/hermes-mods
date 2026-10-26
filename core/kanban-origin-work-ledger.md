# Owner-qualified origin work, durable receipts and completion evidence

## Behavior

An originating chat stays unfinished while its work runs on another gateway. Task lists, sidebar counts and archive guards must not treat a failed replica read, missing owner, review state or unverified terminal status as an empty workload. Identity includes gateway, profile, board and task ID, not just a task ID.

`hermes chat --origin-session` / `HERMES_ORIGIN_SESSION` opt the orchestrator into the Kanban toolset and validate origin routing before work starts. The create tool and CLI retain the actual worker session and add an origin association instead of rewriting `session_id`. Binding acceptance is a durable receipt, **not completion or proof of delivery**.

Desktop preserves qualified owners through task open requests, drawers, attachment routing and bulk writes. Unknown task status requires archive confirmation. Completion notices wait for fresh origin-release evidence; matching a stored session ID on a different owner is insufficient. Profile-only event ownership and proven same-owner compression lineage are handled without borrowing another owner's completion.

## How it works

The origin ledger persists immutable owner-qualified bindings and idempotent courier request receipts. The binding marker is independent of the publication-version lower bound. Creation intents are committed atomically with task creation, including the registered tool path; the strict courier adapter validates routing and published owner records before acknowledging readback.

Owner observations advance only from newer, exactly bound records. The collector supplies evidence inside the owner's read transaction; payload hashes bind that evidence to the observed snapshot. A terminal status alone cannot release an obligation. PR completion requires the exact acceptance event/run and contract; goal completion also needs a successful, completion-bound handoff-judge receipt. Archive release must retain evidence that the task was completed before archive. A later nonterminal publication can reopen an obligation.

Projection requires a readable qualified owner scope and fresh matching evidence (the source uses a 180-second freshness window). Replica failure retains the durable obligation as unknown/stale rather than zero. Activity caches are partitioned by origin profile. Legacy associations remain unfinished until verified; reconciliation across aliases requires proven session lineage.

Desktop consumers apply freshness consistently to rows, tabs, navigation and archive checks. Deferred completion refresh is bounded and late results cannot publish after its deadline. This is not a live notification-delivery guarantee.

## Patch-by-patch scope

| Patch | Change |
|---|---|
| 0221 | One remote-work association for task lists and counts; review/unreadable replica remains unfinished. |
| 0223 | Origin-session CLI/environment/tool binding and typed tool-availability preflight. |
| 0242 | Durable validated binding receipts. |
| 0243 | Separate immutable marker from publication version. |
| 0244 | Strict courier receipt CLI adapter. |
| 0245 | Atomic creation intents. |
| 0246 | Retain intent through the registered create tool. |
| 0247 | Validate courier owner creation through registered tools and entry-parity tests. |
| 0268 | Advance durable bound owner observations. |
| 0270 | Project ledger receipts into activity readers. |
| 0273 | Qualified remote identity and unknown legacy ownership. |
| 0274 | Origin-profile-scoped activity caches. |
| 0275 | Require readable owner scope for projection. |
| 0276 | Keep unknown work unfinished; expose profile-pinned plugin REST. |
| 0277 | Carry `kanban_active` in automated-row typing. |
| 0278 | Confirm archive when task status is unknown. |
| 0279 | Preserve remembered board and explicit profile in task APIs. |
| 0281 | Retain qualified owner in open requests. |
| 0282 | Partition bulk writes by owner profile and board. |
| 0293 | Carry qualified owner through task drawers and attachments. |
| 0295 | Owner evidence projection and notification foundation. |
| 0296 | Bind evidence to owner snapshot and archive lifecycle. |
| 0297 | Bind release to exact PR acceptance run. |
| 0299 | Bind goal release to successful handoff judge. |
| 0311 | Defer completion notices until fresh release. |
| 0313 | Retain unverified legacy work and expire consumer evidence. |
| 0314 | Automated-row active-count type follow-up (retained source identity). |
| 0316 | Apply freshness to tree rows, tabs and navigation. |
| 0324 | Bind deferred completion to owner and proven lineage. |
| 0325 | Qualify profile-only completion-event owners. |
| 0326 | Bound projection reads and suppress late publication. |
| 0328 | Reconcile legacy origin work across proven session aliases. |
| 0342 | Update merged done-state test fixture to require fresh evidence. |
| 0361 | Integration fixes for origin ownership: shared creation/origin helpers, drawer/SDK wiring, archive copy, profile-dot fixtures and test-runner integration. |

## Code

- `hermes_cli/kanban_origin{,_ledger,_adapter,_creation,_owner}.py`, `main_chat_origin.py`, `kanban_db_creation.py`: binding, creation, receipt and projection boundaries.
- `hermes_cli/kanban_session_activity.py`, `kanban_pr_acceptance_store.py`, `kanban_goal_receipt.py`, `kanban_db.py`: activity and lifecycle evidence.
- `tools/kanban_tools.py`, `tools/kanban_tools_schemas.py`, `model_tools.py`, CLI parser and `kanban.py`: supported creation/preflight surfaces.
- `apps/desktop/src/plugins/kanban/{api,open-task,board,drawer,board-task-drawer,attach-drop}.*`, `sdk/index.ts`, `api/plugins.ts`: owner-preserving UI and plugin access.
- `apps/desktop/src/store/{kanban-observation,session-dot-state,archive-guards,native-notifications,sidebar-visible-order,gateway}.ts`, session hooks and sidebar/tab components: evidence consumers.

## Tests

These are **source regression suites**, not tests executed by this documentation pass. Executed full-series and selected test results are recorded in [mirror verification](mirror-377-verification.md). No live gateway, courier, Desktop or notification deployment is claimed.

Python suites under `tests/hermes_cli/`:

- `test_kanban_origin.py`: origin validation and preflight.
- `test_kanban_origin_ledger.py`, `test_kanban_origin_adapter.py`: immutable receipts, idempotency and courier validation.
- `test_kanban_origin_creation.py`, `test_kanban_origin_create_tool.py`, `test_kanban_origin_owner.py`, `test_kanban_create_entry_parity.py`: atomic intents and registered creation/CLI entry parity. Do not infer an arbitrary shell script's private-handler call is supported.
- `test_kanban_origin_observation.py`, `test_kanban_origin_projection.py`, `test_kanban_origin_evidence.py`: exact owner/version/snapshot evidence and conservative projection.
- `test_kanban_session_activity.py`, `test_kanban_remote_identity.py`, `test_kanban_activity_scope.py`, `test_kanban_origin_alias_release.py`: shared readers, profile isolation and legacy aliases.
- `test_kanban_pr_acceptance.py`, `test_kanban_goal_receipt.py`: release receipts tied to completion gates.
- `tests/tui_gateway/test_projects_rpc.py`: project-row integration fixtures in 0361.

Desktop suites under `apps/desktop/src/`:

- `api/plugins.test.ts`: profile-pinned plugin REST.
- `plugins/kanban/api.gateways.test.ts`, `open-task.test.ts`, `board-owner.test.tsx`: qualified requests, partitioned writes and drawers.
- `store/session-dot-state.test.ts`, `archive-guards.test.ts`, `sidebar-visible-order.test.ts`, `profile-dot-state.test.ts`: unknown/freshness/visibility handling.
- `store/native-notifications.test.ts`: cross-owner collision refusal, same-owner lineage, profile-only events, hung projection refresh and late-publication suppression.
- `app/session/hooks/use-message-stream/completion-boundaries.test.tsx`: completion stream boundary.
- `app/session/hooks/use-session-actions/archive-linked-confirm.test.tsx`, `use-session-actions.test.tsx`, `use-session-actions/unarchive-session.test.tsx`: archive/action integration.
- `app/chat/sidebar/session-row.test.tsx`, `app/chat/session-tab-accent.test.tsx`: row/tab freshness.

Run the relevant Python files with `bash scripts/run_tests.sh <files>` in an isolated applied checkout and Desktop files with `npx vitest run <files>` from `apps/desktop`. The type-only patches need typechecking too; they do not add a runtime test.

## Applying

Apply the complete ordered series, not this noncontiguous subset alone. It builds on existing session links, hub replica reads, PR acceptance and goal handoff. Patch 0361 integrates overlapping changes after the earlier feature commits. Read [hub-sync-replica.md](hub-sync-replica.md) for cached sidebar behavior and [kanban-unfinished-visibility.md](kanban-unfinished-visibility.md) for later unfinished-state presentation.

## Gotchas

- Missing publication, unreadable scopes and stale evidence are unknown, not delivered. An accepted receipt acknowledges persistence only.
- Courier sender authentication and collector publication are separate integration responsibilities; the ledger is not a transport.
- Bare IDs and ambient selected profiles cannot authorize owner-routed operations. Retain the complete owner even when IDs collide.
- Existing historical test totals in other docs do not establish verification of this expanded series.

## Plugin relationship

Hub/courier and Desktop plugins consume these seams, including profile-pinned REST, but transactional creation, durable release evidence, shared activity projection and Desktop owner routing require core changes. Updating a disk plugin alone does not activate them.
