# fix(kanban): keep remote notices in flight until the transcript has them [daveey]

Patch 0837, source `5bab4bb450aa9322183f1653ed6ebe6c77d5bfd6`.

## Behavior

fix(kanban): keep remote notices in flight until the transcript has them [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `in kanban_watchers_common.`
- `class GatewayKanbanWatchersMixin:`
- `class _Collector:`
- `import contextlib`
- `def test_poller_collect_delivers_final_send_and_sweeper_dedupes(tmp_path):`
- `never verified the work (hub kanban.db evidence 2026-10-28, card t_01a182faed899`
- `def _ledger(path: Optional[Path] = None) -> sqlite3.Connection:`
- `def _event_key(owner: str, key: str, notice: dict) -> str:`
- `def _completion_text(gw: str, key: str, task: dict, me: str, profile: str, sid:`
- `def _session_db(profile, sid):`
- `def sweep_remote_notices(*, replica_path=None, gateway=None, session_db=_session`
- `when the notice is diagnostic-only, when the chat's wake budget is spent (HERM`
- `def _wake_job(wake, session_db, slug, ident, tip, text, metadata, old, new, now)`
- `def _deliver_sub(conn, slug, sub, ident, grace_s, now, session_db, _kb, _kbn, ki`
- `def _loop(stop: threading.Event) -> None:`
- `def _forwarded_locally(remote_task_id: str) -> bool:`
- `def _collect_remote_kanban_notifications(session_key: str, claims: list | None)`
- `def _kb_ack_durable(session: dict, durable_ids: set) -> None:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A remote claim stays delivered=0 with holder pid + start until the notice is in
> the chat transcript. A dead holder is re-offered unless that shared key is
> already in the lineage. Poller and sweep record one key, so both yield one row.
> Legacy delivered=1 rows inside FRESH_S with no transcript hit are re-offered
> once; older ones are counted and left. Messaging and unloaded sweeps use the
> same durable claim.

## Code

- `gateway/kanban_watchers.py`
- `gateway/kanban_watchers_notifier.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/session_notifications.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_kanban_remote_notices.py`

New or changed test definitions present in the source diff:

- `test_poller_acks_the_ledger_only_after_the_transcript_write`
- `test_absent_replica_is_read_only_and_not_created`
- `test_done_claim_survives_the_consumer_dying_before_the_turn`
- `test_transcript_write_then_death_before_ack_is_not_delivered_twice`
- `test_poller_and_sweep_share_one_key`
- `test_blocked_claim_survives_the_consumer_dying`
- `test_legacy_fresh_rows_reoffered_once_and_stale_rows_left`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
