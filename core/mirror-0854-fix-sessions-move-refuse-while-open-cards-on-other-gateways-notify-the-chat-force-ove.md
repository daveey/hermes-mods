# fix(sessions move): refuse while open cards on other gateways notify the chat (--force overrides) [daveey]

Patch 0854, source `ea91fc2b15fad0aa490004495c3498f65ce61e48`.

## Behavior

fix(sessions move): refuse while open cards on other gateways notify the chat (--force overrides) [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `A chat session moves to another gateway while this one (its current owner) is al`
- `def _segments_and_counts(db, session_id: str, home: str) -> tuple[dict, dict[str`
- `def move_session(session_id: str, to: str, reason: str, *, after_turn: bool = Fa`
- `def _move(db, session_id: str, to: str, reason: str, *, me: str, target: str, af`
- `def cmd_discard_hidden(args) -> int:`
- `def build_sessions_parser(subparsers, *, cmd_sessions: Callable) -> None:`
- `def test_transport_argv_templates():`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> repoint_links re-points only this gateway's cards; a card another gateway owns would keep notifying the archived
> copy. The move now lists such open cards (kanban_chat_links.replica_cards) and refuses with remote_cards unless
> --force.
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `hermes_cli/session_handover.py`
- `hermes_cli/subcommands/sessions.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_session_handover.py`

New or changed test definitions present in the source diff:

- `test_open_cards_on_other_gateways_refuse_the_move_unless_forced`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
