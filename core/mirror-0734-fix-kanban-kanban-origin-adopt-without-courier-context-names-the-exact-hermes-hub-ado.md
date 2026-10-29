# fix(kanban): kanban_origin_adopt without courier context names the exact hermes-hub adopt-task next step [daveey] (#145)

Patch 0734, source `2248ff852705eab0969c2ea7edbf810e8daa8666`.

## Behavior

fix(kanban): kanban_origin_adopt without courier context names the exact hermes-hub adopt-task next step [daveey] (#145). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from hermes_cli import kanban_lockguard as _kanban_lockguard`
- `def resolve(explicit: Optional[str] = None) -> Optional[Origin]:`
- `def test_registered_adoption_tool_requires_trusted_context(board):`
- `def _handle_origin_adopt(args: dict, **kw) -> str:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> * fix(kanban): [daveey] kanban_origin_adopt without courier context names the exact hermes-hub adopt-task next step
>
> kanban_origin_adopt only runs inside a courier-delivered adoption. Called from a chat (or a worker) it
> returned origin_context_required with no way forward: a hub worker told its origin chat to call the
> tool, the chat was refused, and the card sat blocked on a human (t_a5cc12d9).
>
> The refusal now carries next_step, the exact command, and run_on, the gateway to run it on:
> - chat: hermes-hub adopt-task <owner-gw> <profile>/<board>/<task> --origin-session $HERMES_SESSION_ID
>   --id adopt:<task>:<session>, owner gateway/profile read from this gateway's hermes-hub replica
>   (courier ids, never SSH aliases); placeholders when the card is unknown or ambiguous.
> - card on this same gateway: hermes kanban --board <b> notify-subscribe <task> --platform tui.
> - worker: the command for the origin gateway (from HERMES_ORIGIN_SESSION) plus delegate, a
>   hermes-hub create-task that gets it run there.
> Tracks t_01a181e3f7620552.
>
> * fix(kanban): [daveey] adopt next_step keeps owner+origin profiles, validates every part, no self-addressed delegate
>
> Review on #145: the worker delegate's links --expect dropped the owner profile (3-part ref = default
> profile, so a non-default card never verified); the command omitted --origin-profile, so it was filled
> from the running CLI not the chat; task/board/session went into a shell command unvalidated. Now: 4-part
> expect ref, --origin-profile from the chat (worker: its HERMES_ORIGIN_SESSION profile), each part must be
> a route name else stays a <placeholder> with complete=false, and a worker whose origin chat is on this
> same gateway gets the notify-subscribe path instead of a create-task addressed to itself.
>
> * fix(kanban): [daveey] adopt_next_step catches only the profile lookup's own errors (health BLE001)

## Code

- `hermes_cli/kanban_origin.py`
- `tools/kanban_tools.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_origin_adoption.py`

New or changed test definitions present in the source diff:

- `test_chat_refusal_names_exact_adopt_task_command`
- `test_chat_refusal_uses_owner_profile_and_board_from_replica`
- `test_chat_refusal_with_unknown_owner_keeps_placeholders`
- `test_unsafe_values_become_placeholders_never_shell_text`
- `test_chat_refusal_for_card_on_this_gateway_subscribes_instead`
- `test_chat_refusal_for_card_on_another_local_profile_names_it`
- `test_worker_refusal_points_at_origin_gateway_cli`
- `test_worker_refusal_carries_non_default_profiles`
- `test_worker_refusal_without_known_origin_names_placeholders`
- `test_worker_with_origin_on_same_gateway_gets_no_self_addressed_delegate`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
