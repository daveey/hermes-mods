# fix(kanban): origin chat of a remote card is subscribed and hears it finish [daveey]

Patch 0781, source `a7dd1e117cba4f5a9dd11b74cb76e5fb36274c99`.

## Behavior

fix(kanban): origin chat of a remote card is subscribed and hears it finish [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _json(value):`
- `def create_with_origin(conn, intent: dict, options: dict) -> str:`
- `def adopt_with_origin(conn, intent: dict, *, board: str) -> str:`
- `+"""The chat that filed a card on another gateway is the one subscribed to it, and it hears the card finish.`
- `def _handle_create(args: dict, **kw) -> str:`
- `def _live_tui_session_key(session_key: str, profile: Optional[str]) -> str:`
- `def _resolve_notify_target() -> Optional[dict[str, Any]]:`
- `+"""Done/blocked notices for cards on OTHER gateways that a chat here is linked to. [daveey]`
- `def _collect_kanban_notifications(session: dict, claims: list | None = None) ->`
- `def _kb_rewind_refused(session: dict, batch: list) -> None:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Hub evidence 2026-10-28 (card t_01a182faed899158): hub cards placed from macbook
> chat 5ab65c had no subscription for it, and 5ab65c never got a done notice.
>
> Why 1: create_with_origin/adopt_with_origin (the courier path every placement-created
>   card takes) stored the origin binding but no platform='hub' subscription; only the
>   legacy kanban_origin.bind path subscribed.
> Why 2: even a hub subscription only feeds replica links (sidebar, task list): nothing on
>   the origin gateway turned a linked remote card's done/blocked into a chat notice.
>   Only deferred forwards notified, through their local placeholder's tui sub.
> Root: default placement moved cards to the hub, replacing a notifying local card with a
>   remote one that had no notice path. Not caught: placement tests asserted card+binding,
>   never that the origin chat is subscribed or notified.
>
> - kanban_origin_creation: _subscribe_origin adds the hub sub from the binding in the
>   creating txn (never the courier's ambient session; the courier path no longer
>   auto-subscribes its process session).
> - tui_gateway/kanban_remote_notices: per-chat poll of the hub replica for terminal cards
>   on other gateways linked (rel=shepherd) to this gateway/profile/session (+ compaction
>   lineage), one notice per state via a host-wide claim ledger, released on a refused
>   turn; states older than 6 h recorded silently; cards a local forward placeholder
>   reports are skipped (no duplicate).
> - kanban_tools._tui_session_key: a process HERMES_SESSION_KEY naming a different chat
>   than this context's bound session id is not subscribed (multi-session serve leak).
>
> Tests: tests/hermes_cli/test_kanban_origin_notify.py (10; 5 fail on the base).

## Code

- `hermes_cli/kanban_origin_creation.py`
- `tools/kanban_tools.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/session_notifications.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_origin_notify.py`

New or changed test definitions present in the source diff:

- `test_courier_create_subscribes_the_bound_origin_not_the_ambient_session`
- `test_courier_adoption_subscribes_the_bound_origin`
- `test_auto_placement_sends_the_filing_chat_as_origin`
- `test_deferred_forward_sends_the_filing_chat_as_origin`
- `test_local_create_never_subscribes_a_stale_process_session`
- `test_tui_key_ignores_a_process_key_naming_another_chat`
- `test_cli_origin_session_flag_subscribes_that_chat`
- `test_remote_done_notice_is_delivered_once_and_released_on_refusal`
- `test_remote_notice_skips_a_card_a_local_forward_placeholder_reports`
- `test_session_poller_delivers_remote_notice_through_collect`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
