# fix(kanban): children inherit only owning notify subs, never shepherd/touch observers [daveey]

Patch 0638, source `684b28d63b68ff66b566c9bbfc10c86c6cf65e66`.

## Behavior

fix(kanban): children inherit only owning notify subs, never shepherd/touch observers [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _cmd_notify_subscribe(args: argparse.Namespace) -> int:`
- `def _inherit_notify_subs(`
- `if TYPE_CHECKING:`
- `def add_notify_sub(`
- `def bind(conn: Any, task_id: str, origin: Origin, *, board: Optional[str] = None`
- `def test_attention_cache_does_not_leak_between_profiles(scoped, monkeypatch, tmp`
- `+"""Children inherit owning notify subscriptions only, never observer ones. [daveey]`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> _inherit_notify_subs (create_task parents, link_tasks, decompose, inherit_creator_origin)
> copied EVERY parent/creator subscription to each child. A shepherd card collects one sub
> per chat that watched it (hermes-hub shepherd -> platform 'hub', notify-subscribe, touch),
> so each observer chat came to own the shepherd's whole program: t_82f80db4's 44 subs were
> copied to t_54aa263b/t_f15d182d and runners-panel chat d48ade showed 5 cards it never made.
>
> - Provenance in delivery_metadata.reason: owning = create (kanban_create) / origin
>   (kanban_origin.bind, now stamped); observer = shepherd (notify-subscribe, now stamped
>   for 'hub' too) / touch.
> - Inheritance skips observers. Unstamped legacy 'hub' rows inherit only when written
>   within 120 s of the card's creation (the origin binding); other unstamped rows (legacy
>   tui, gateway DM subs) keep the old behaviour, so #73030 DM routing is unchanged.
> - add_notify_sub never downgrades an owning reason to an observer one.
> - Child session_id still comes from the creator task.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_notify.py`
- `hermes_cli/kanban_origin.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_activity_scope.py`
- `tests/hermes_cli/test_kanban_notify_inheritance.py`

New or changed test definitions present in the source diff:

- `test_shepherd_chat_counts_its_card_not_the_cards_children`
- `test_shepherd_observer_subs_not_inherited_by_child`
- `test_decompose_child_does_not_inherit_observer_subs`
- `test_grandchild_keeps_owning_chain`
- `test_legacy_hub_subs_inherit_only_when_bound_at_creation`
- `test_legacy_unstamped_tui_and_messaging_subs_still_inherit`
- `test_resubscribe_never_downgrades_an_owning_sub`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
