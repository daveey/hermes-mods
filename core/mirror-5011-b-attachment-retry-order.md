# Attachment custody retry priority

Mirror entries: 4477.

## Behavior

- **4477** — A failed upload with a nonempty local custody path gets retry priority even before it has a blob key.

## How it works

The bounded transfer query already prioritizes metadata and failed blob operations. Its failure-priority arm now includes either an existing blob key or a nonempty stored local path. A keyless upload failure therefore competes ahead of overdue verification maintenance rather than being starved by it.

Eligibility, metadata priority, backoff and batch limits remain unchanged. Empty paths still represent metadata-only rows and do not gain upload priority merely by existing.

## Code

- `hermes_cli/kanban_sync_attachments.py`

## Tests

The source attachment suite adds real upload failures for OSError and ValueError, verifies the retry is deferred until due, checks zero and bounded batch limits, and confirms successful blob publication plus retained local bytes. Existing metadata-only controls remain. No upload or suite execution was performed in this documentation pass.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_kanban_sync_attachments.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

Ordering does not waive retry deadlines or make an empty custody path uploadable. A failed upload has no blob key by definition, so using blob-key existence alone cannot identify all failed custody work.
