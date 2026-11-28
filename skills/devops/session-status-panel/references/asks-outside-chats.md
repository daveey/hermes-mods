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
- **The backend annotation snapshot is unavailable although canonical status still paints red.** Read `needs_you` and `kanban_unknown` from both the SDK row and the exact session API on its connection/profile. If both lack ask refs and report unknown, this is upstream of Desktop, not evidence stripping or a matcher defect. A fresh-process annotation probe is not equivalent to the long-lived server. Sample that server read-only: a bounded annotation helper can finish counts and then stall in a waiting-source plugin's remote inventory gate. Snapshot state is thread-local; establish `one_annotation_snapshot()` on the reading helper itself, not only its caller, so waiting adapters reuse the same strict links/origin read rather than reacquiring the gate. Verify changed cards and origin rows become visible on the next refresh; never mask missing refs with canonical status item matching or extend stale authority. Keep source read age and publication provenance unchanged. Check inspector closure and unchanged app birth separately from recovery.
