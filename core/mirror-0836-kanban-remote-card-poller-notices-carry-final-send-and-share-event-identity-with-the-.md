# kanban: remote-card poller notices carry FINAL SEND and share event identity with the sweeper

Patch 0836, source `80012e85fe8c557ee835125a8b6f4b1655ba2a7a`.

## Behavior

kanban: remote-card poller notices carry FINAL SEND and share event identity with the sweeper. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_legacy_done_cards_are_not_replayed_on_rollout(tmp_path):`
- `def format_notice(gw: str, key: str, task: dict) -> str:`
- `def collect(session_key: str, profile: str, *, hub_db: Optional[Path] = None, le`
- `def sweep_remote_notices(*, replica_path=None, gateway=None, session_db=_session`
- `def _collect_remote_kanban_notifications(session_key: str, claims: list | None)`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The live poller (collect) delivered the generic 'ran on <gw>' notice for a remote done card, which drops the
> FINAL SEND text; the sweeper's FINAL SEND notice then deduped on a different id. collect now formats from the
> owner's completion_notice and both paths record the same kanban_event_keys. [daveey]

## Code

- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/session_notifications.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_kanban_remote_notices.py`

New or changed test definitions present in the source diff:

- `test_poller_collect_delivers_final_send_and_sweeper_dedupes`
- `test_absent_replica_is_read_only_and_not_created`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
