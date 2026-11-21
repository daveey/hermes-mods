# Value-keyed chat-links reconciliation and refresh accounting

Mirror entries: 5306. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Unchanged Kanban chat-link inputs can skip reconciliation beyond the old 300-second age threshold when the state-store answers used by the previous pass remain equal. An unrelated chat commit does not force a links rebuild. Explicit `hermes kanban sync links` still reconciles; degraded/error passes keep conservative age retries.

## How it works

A thread-owned `ChatReads` context records effective session creation sources, compression-chain steps, store existence and discovered homes per board. Within a pass each answer is freshly read once, bypassing positive and negative provenance TTL entries; memoized answers are shared across boards only within that pass. The next pass replays the recorded reads by value, not state DB `data_version`. Changed board/replica/ledger inputs still invalidate as before.

Swallowed required-read failures mark the pass degraded so no permanent skip key is recorded; missing optional historical tables are distinguished from read errors. Context copied to a background thread does not join the original pass. Refresh accounting separates cold/skip/age/input reasons and raw changed-store categories from actual builds; narrowed cache hits are not builds.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_links_chat_key.py`
- `hermes_cli/kanban_links_refresh.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_provenance.py`
- `hermes_cli/kanban_sync_client.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests cover unchanged skips past max age, no-store boards, warm positive/negative TTL bypass, new session/store/profile arrival, unrelated commits, changes during a pass, unreadable-store age retry, once-per-pass reads with outside-reader cache preservation, copied-thread isolation, failed reconcile and compression/store/homes key replay. Error-degradation tests cover owner/replica/hub/subscription/lineage reads. Accounting tests check decision reasons, store categories, overlapping expiry/input changes, narrowed hits, projection errors and scope changes. Sync-local/steady-links fixtures pin conservative fallback behavior.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_links_refresh_accounting.py`
- `tests/hermes_cli/test_kanban_sync_links_chat_key.py`
- `tests/hermes_cli/test_kanban_sync_local_integration.py`
- `tests/hermes_cli/test_kanban_sync_steady_links.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

The key is semantic answers actually consumed, not absence of every host mutation. A failed required read must not look like a stable empty answer. These fixture tests do not measure live CPU savings or a production sync loop; direct REST/Desktop invocation paths are untested separately.

## Why core instead of a plugin

Only core links reconciliation knows which transactional and state-store inputs it consumed. A plugin timer/cache cannot safely skip that work without recording/degrading the exact dependency reads.
