# Asks outside chats (lanes sidebar section)

The lanes plugin's **Asks outside chats** section (formerly "Unlinked asks") lists a human ask when no visible sidebar row is red with a `needs_you` ref of `<board>/<id>`. It does NOT mean the card has no chat link: check that before changing anything.

## Procedure

1. Dump the producer rows on the hub. Load `plugins/lanes/dashboard/plugin_api.py` with the core venv python and call `_lane_view(6.0, '6h', True)['attention']`. Each row has `links` (chat_gateway, chat_profile, chat_session) and `target.board`.
2. Read the renderer's input read-only on the Mac. Use rend-eval (skill inspecting-hermes-desktop-dom) on the `UnlinkedAsks` fiber's hook state: the sidebar session list plus the attention view. For each linked session, read its `connection_id`, `needs_you` refs and outcome stripe.
3. The section's count changes between reads. Take several samples before you call a row explained.
4. Close the inspector you opened, and confirm the app still has the same PID.

## Known cause classes

- **Two gateways share a headless owner-chat id** (`kanban_owner_*`). A list keyed by session id alone shows the wrong gateway's copy, so sidebar identity must be owner-qualified (gateway + id).
- **The attention row has a null board.** It matches against `default/<id>`, so a `bb/` ask never covers.
- **The linked chat is muted or offline-cached.** It is absent from the list. It must still render red, with a reason.
- **A link shows up twice in `links`.** This is cosmetic, but dedupe by (gateway, profile, session).
