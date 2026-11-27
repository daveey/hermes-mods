# Session list reads, status checkpoints and early startup

**Scope:** Packed reads, probe memo, foreign process checkpoints, early-owned promotion, summary marker leaf.

**Source ordinals:** 6477, 6502, 6503, 6551, 6552, 6559, 6560, 6561

## Behavior

- Session list reads hand board/profile rows to Python packed into one JSON row per query (fewer SQLite steps; `read_rows` replaces several per-row reads and a per-column schema probe). Types and order are preserved, but REAL values travel as `printf('%!.26g')` text, exact only as far as the SQLite build's printf, and -0.0 arrives as 0.0. The read-only schema-probe memo is keyed on real path, device/inode, `PRAGMA schema_version`, connection class and probe statements; a hit also requires the file identity to be unchanged across the connect, a failed probe is never remembered, and the memo is capped at 512 keys (cleared when full).
- Canonical session status includes owner-qualified foreign process checkpoints without adoption or starting a watcher. A checkpoint entry that is malformed, has an unavailable scope/birth, or whose host process is not provably running raises and is not reported as running; ids already known to the registry are skipped.
- Early-owned sessions promote before bounded warming. The always-on roller now reads backend HTTP through a deadline-bounded direct loopback reader (no redirects/proxies/threads), health reports `sessions_early_owner`, and an early-owner backend is promoted only after canonical list and sidebar reads are complete and the unit PID and launch nonce stay the same, with a final health recheck; a `pendingPromotion` flag marks an incomplete promotion and a first-boot backend that may have served is not abandoned. Warm rounds follow synchronously, are joined, and run under the same deadline; warm failure after publication is logged, not fatal. Summary marker strings move into a runtime-independent leaf (`agent/context_summary_markers.py`).

## How it works

- `web_server_sessions.py`, `hermes_state_page_pack.py`, `hermes_state_process_checkpoint.py`, `always_on_*.py`, `agent/context_summary_markers.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `agent/context_compressor.py`
- `agent/context_summary_markers.py`
- `hermes_cli/always_on_http.py`
- `hermes_cli/always_on_rollover.py`
- `hermes_cli/always_on_sessions.py`
- `hermes_cli/always_on_warm.py`
- `hermes_cli/kanban_contained_wait.py`
- `hermes_cli/kanban_external_activity.py`
- `hermes_cli/kanban_external_display.py`
- `hermes_cli/kanban_external_watch.py`
- `hermes_cli/kanban_session_wait_rows.py`
- `hermes_cli/sessions_early_boot.py`
- `hermes_cli/web_routers/status.py`
- `hermes_cli/web_server_sessions.py`
- `hermes_state_common.py`
- `hermes_state_page_pack.py`
- `hermes_state_process_checkpoint.py`
- `hermes_state_session_status_snapshot.py`
- `scripts/ci/classify_changes.py`
- `website/docs/developer-guide/context-compression-and-caching.md`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_always_on_http_deadline.py`
- `tests/hermes_cli/test_always_on_sessions_promotion.py`
- `tests/hermes_cli/test_always_on_warm_deadline.py`
- `tests/hermes_cli/test_browse_probe_memo.py`
- `tests/hermes_cli/test_kanban_external_read_rows.py`
- `tests/hermes_state/test_cold_preview_import.py`
- `tests/hermes_state/test_session_status_checkpoint_fences.py`
- `tests/hermes_state/test_session_status_checkpoint_process.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6477-6561, 8 commits: 6477, 6502, 6503, 6551, 6552, 6559, 6560, 6561) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- REAL exactness depends on SQLite printf.
- Foreign process checkpoints are read without adoption or watcher creation, validated for owner, host PID and birth identity; unknown stays unknown and no completion is claimed without an exit receipt.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Server startup.
