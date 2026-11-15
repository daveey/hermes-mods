# Remote notice identity and mixed transcript constituents

Patches: **4254–4255, 4333** (4352 snapshot).

## Behavior

Profiles on one install share the remote notice claim ledger. A notice already durably present does not wake another turn; a failed wake leaves one passive row.

Adjacent duplicates can fold to one notice without discarding ordinary user input, other notices or model-only sidecars. Mixed runs survive persistence, reload and compaction using explicit constituent identities.

## How it works

The claim ledger lives beside the tracked replica rather than independently under each profile. User-row repair recognizes notice identity but refuses to classify opaque premerged carriers as a single notice.

Constituent offsets and identities live in display metadata; model bytes stay in api_content. Persistence avoids reappending an identical current-notice override. Durable repair updates only its owned composite metadata, preserving unrelated reactions and backfills.

## Code

- `agent/agent_runtime_helpers.py`
- `agent/agent_runtime_helpers_user_rows.py`
- `agent/session_persistence.py`
- `agent/transcript_repair.py`
- `tui_gateway/kanban_remote_notices.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/agent/test_mixed_notice_constituents.py`
- `tests/gateway/test_kanban_remote_owner_delivery.py`
- `tests/tui_gateway/test_kanban_remote_index_defer.py`
- `tests/tui_gateway/test_kanban_remote_notice_once.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Text similarity is not a notice boundary. Edited or malformed constituents and premerged carriers must retain input conservatively. Cross-profile wake, alternation and mixed-persistence tests are shipped; no live multi-profile delivery was performed here.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
