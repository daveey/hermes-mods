# Notify-subscribe caller resolution before the different-chat warning

Patches: **2458**.

## Behavior

`hermes kanban notify-subscribe --platform tui --chat-id <id>` prints the bound chat's title and, when the calling agent's own chat is a different chat, a `WARNING: binding a different chat` line on stderr suggesting `--chat-id <caller chat>`. Before this patch a Desktop routing key (for example `agent:main:local:dm:<session id>`) in `HERMES_SESSION_KEY` was compared verbatim against the target, so binding the caller's own chat produced a false warning that recommended the routing key itself. Now the warning only appears when the caller resolves to a real stored chat that is not in the same compression lineage as the target, and it only ever recommends a stored chat id. An unresolved caller, a plain shell, a Kanban worker (`HERMES_KANBAN_TASK`), and a target that is not in `state.db` are never warned.

## How it works

`kanban_notify_target._caller_chat()` tries, in order, `HERMES_SESSION_ID`, `HERMES_SESSION_KEY`, and the final `:`-separated segment of `HERMES_SESSION_KEY`. The first candidate that `_title_and_tip` finds in the profile's `state.db` wins and returns `(chat id, compression tip)`; if none is stored it returns `("", None)`. `print_tui_target` returns early when the target is not found, and otherwise compares the target's compression tip with the caller's tip, so two ids of one compacted lineage count as the same chat. A routing-shaped id that really is stored as a session id is used as-is and not replaced by its suffix. The docstring now states that both env spellings only identify the caller: the manual subscribe routes by `--chat-id`.

## Code

`hermes_cli/kanban_notify_target.py` (called from `hermes_cli/kanban.py`).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_notify_subscribe_target.py`. It drives the CLI path (`hermes kanban notify-subscribe --platform tui` through `kc._cmd_notify_subscribe`) for routing-key, bare-id, preferred `HERMES_SESSION_ID`, stale unstored session id, worker, unknown caller, compression-lineage, unknown-target and routing-shaped stored-id cases. No other entry path (tool, REST) is claimed; the `kanban_notify_subscribe` tool is untested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

The warning is advisory: the command still binds the chat named by `--chat-id` and exits 0. If the caller's env names no stored chat (for example a session not yet written to `state.db`), no warning is possible and the wrong-chat check is skipped. The patch notes that live-machine command and wake verification were still required after deployment.

## Plugin

Core change; no plugin required.
