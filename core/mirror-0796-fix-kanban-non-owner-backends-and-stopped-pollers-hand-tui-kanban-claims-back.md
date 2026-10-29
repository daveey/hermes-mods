# fix(kanban): non-owner backends and stopped pollers hand tui kanban claims back [daveey]

Patch 0796, source `407b0a76435a08dcea84646e5a07e7897f38edb2`.

## Behavior

fix(kanban): non-owner backends and stopped pollers hand tui kanban claims back [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_unloaded_sweep_recovers_an_orphaned_claim(tmp_path, monkeypatch):`
- `def _kb_persist_batch(session: dict, batch: list, diagnostic: bool) -> tuple:`
- `def _kb_release_claims(session: dict) -> None:`
- `def _notif_poll_kanban_scoped(sid: str, session: dict) -> None:`
- `def _notification_poller_scoped_loop(stop_event: threading.Event, sid: str, sess`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Review round 1 of t_01a1835174955959 (at-least-once tui kanban notices):
>
> - A session that stops polling while a notice is buffered (chat closed, evicted, or taken over while its
>   process lives on) now rewinds its in-flight claims, at the poller's exit and on any poll of a
>   finalized/closing/taken-over session. Before, the claim stayed held by a live pid and blocked every other
>   backend's durable claim on that subscription until the process exited.
> - Ownership is probed read-only (foreign durable turn lease, or another pid's live registry entry for the
>   chat) BEFORE the notice row is written and acked. A non-owner hands the claim back for the owner's poller
>   instead of writing a row no turn would ever react to.
>
> Tests: 6 new cases in test_kanban_notice_at_least_once.py (close/closing/takeover, poller stop, durable-lease
> owner, registry owner), all failing on the previous commit.

## Code

- `tui_gateway/session_notifications.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_kanban_notice_at_least_once.py`

New or changed test definitions present in the source diff:

- `test_closed_session_hands_its_buffered_claim_back_while_the_process_lives`
- `test_poller_stop_hands_buffered_claims_back`
- `test_non_owner_backend_writes_no_row_and_leaves_the_notice_for_the_owner`
- `test_chat_held_in_another_process_registry_writes_no_row`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
