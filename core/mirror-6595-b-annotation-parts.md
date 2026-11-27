# Semantic annotation parts and fail-fresh caching

Patch coverage: 6191, 6192, 6193, 6194, 6195, 6228, 6280, 6281, 6282, 6298, 6328, 6329, 6330, 6331, 6332, 6333, 6334, 6339.

## Behavior

Session activity reuses annotation work only when the specific inputs of counts, links, attention or waiting sources are certified unchanged. Unread event inserts need not rebuild link projections, while notification revisions still advance. Later per-part semantic revisions narrow invalidation further without concealing unknown schemas.

Waiting-source and done-check declarations are tied to the actual registered closures. Transcript-only changes need not invalidate counts when canonical continuation membership is unchanged. Concurrent external-display captures serialize per board and recheck the signal after waiting.

## How it works

Projection probes answer old/busy files from one open, rather than paying a second busy budget. Schema/trigger coverage is validated and legacy fallback remains conservative. Counts and attention initially retain the full notify revision where their readers need it; semantic part revisions subsequently express their own table/column dependencies. Unknown/lost watch coverage forces fresh reads for every part.

Waiting output is detached and reused only in the serialized holder pass; ordinary requests retain literal reads. register_done_check inputs must include every external dependency. Complete JSON data is copied losslessly, rejects non-string keys/nonfinite values, and uses stable JSON spelling to distinguish bool/int/float and signed zero. Closure replacement invalidates declarations. No forced parts means no outer projection/publication. Inclusive accounting distinguishes forced from cache-eligible work. Dashboard board-row refresh samples its commit clock after refresh custody.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/kanban_activity_revision.py`
- `hermes_cli/kanban_annotation_done_inputs.py`
- `hermes_cli/kanban_annotation_host.py`
- `hermes_cli/kanban_annotation_lineage.py`
- `hermes_cli/kanban_annotation_local.py`
- `hermes_cli/kanban_annotation_parts.py`
- `hermes_cli/kanban_annotation_revision.py`
- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_external_activity.py`
- `hermes_cli/kanban_external_refresh.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_attention.py`
- `hermes_cli/kanban_session_activity_revalidate.py`
- `plugins/kanban/dashboard/board_rows.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_activity_revision.py`
- `tests/hermes_cli/test_kanban_content_key_busy_budget.py`
- `tests/hermes_cli/test_kanban_annotation_waiting_gate.py`
- `tests/hermes_cli/test_kanban_annotation_done_inputs.py`
- `tests/hermes_cli/test_kanban_annotation_revision.py`
- `tests/hermes_cli/test_kanban_external_singleflight.py`
- `tests/plugins/test_kanban_board_rows_cache.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

Cache-eligible does not mean cache hit; inclusive part counters are not additive. Undeclared, unreadable, nonportable or incomplete inputs never certify an unchanged answer. A lost notification is not evidence of no change. Per-board capture locks retain no DB handle and do not serialize unrelated boards.

## Why core, not a plugin?

Transaction-backed change counters, native holder scheduling, callback registries and request-local projections must share core invalidation rules.
