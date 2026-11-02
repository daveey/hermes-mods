# Named-profile bot DMs use the install-wide relay mailbox

Patches: 1691 1692

- `patches/1691-fix-bot-share-named-profile-relay-mailbox-with-conne.patch`: the fix.
- `patches/1692-test-bot-exercise-handle-collisions-in-connection-ma.patch`: test-only; moves one handle-collision test to
  the install-wide mailbox.

Related: [desktop-bot-relay-connection-retention.md](desktop-bot-relay-connection-retention.md) (1661). Desktop drains
one representative route per connection (preferably `default`), and the gateway relay methods resolve to one
install root. That doc notes its tests did not prove where a named profile's `message_agent` producer writes. 1691
fixes and tests the producer side.

What you see: in Bot Mode, a named profile's Bot Chat that messages a bot on another connected gateway (`remote` or
`remote@connection`) now gets delivered and receives the reply. Before, the envelope was queued in the named profile's
own relay folder, which the connection backend never drained.

## Behavior

- `tools/bot_mode_dm.message_agent_tool` queues relay envelopes in the install root's mailbox
  (`<install root>/bot_relay/...`, from `_hermes_root(home)`), not in `<root>/profiles/<sender>/bot_relay`. This applies
  to both relay attempts: a qualified `name@connection` target, and an unknown or same-name target that falls back to
  the relay roster.
- Sender identity is unchanged: the envelope still carries `from_profile` (for example `sender`), and the message text
  still reads `Message from 🤖 <sender> (@<sender>): ...`.
- The reply waiter (`--wait-reply`) watches `<install root>/bot_relay/replies/<envelope id>.json`, which is where the
  gateway's `bot_relay.reply` method writes.
- `tools/bot_mode_probe._build_section` builds the remote-teammates paragraph from the install root's roster (the one
  `bot_relay.roster.sync` writes), so a stale roster left in a named profile's folder is no longer shown in its prompt.

## How it works

Three call sites switch from the profile home to `root` (`_hermes_root(home)`): the two `_try_relay_delivery(...)`
calls in `bot_mode_dm.py` and `_remote_paragraph(...)` in `bot_mode_probe.py`. The local-teammate path and the peer
paragraph (already `root`) are unchanged.

## Code

`tools/bot_mode_dm.py`, `tools/bot_mode_probe.py`; tests `tests/tools/test_bot_profile_relay.py`,
`tests/tools/test_bot_mode_dm.py`, `tests/tools/test_bot_mode_hermes_handle.py`.

## Tests

- `tests/tools/test_bot_profile_relay.py::test_named_sender_round_trip_through_default_backend` (target `remote` and
  `remote@cloud`): `HERMES_HOME` is the install root (the representative default backend). The roster is synced
  through the real `bot_relay.roster.sync` gateway method. The named sender's `message_agent` call is drained by
  `bot_relay.outbox.drain` with `target_connection == "cloud"` and `from_profile == "sender"`, and no `bot_relay` folder
  is created under the sender's profile. The real `_delivery_main` waiter is started before the reply exists, then
  `bot_relay.reply` writes it; the waiter exits 0 and prints the reply, and a second drain is empty.
- `test_named_sender_prompt_uses_connection_roster_not_stale_profile_roster`: the Bot Mode prompt section lists the
  install-root roster entry and not the stale per-profile one.
- `tests/tools/test_bot_mode_dm.py::test_named_bot_relays_a_qualified_target_through_install_mailbox` (renamed from
  `..._through_its_own_folder`): `researcher@box` from the `researcher` profile is claimed from the install root, not
  the sender folder.
- 1692: `tests/tools/test_bot_mode_hermes_handle.py::test_remote_default_is_never_offered_as_a_bare_form_a_local_profile_answers_to`
  writes the roster to and claims envelopes from the install root.

The mirror pass did not rerun these tests.

Untested: Desktop actually draining the named sender's envelope over a real connection (the round-trip test calls the
gateway methods in-process); envelopes already queued in a profile folder by the old code are not migrated, and no test
covers them.

## Applying

Needs the Bot Mode relay (`tools/bot_relay.py`, `tui_gateway` `bot_relay.*` methods) and the install-root resolution
`tools.bot_mode_probe._hermes_root`. Apply 1691 then 1692.

## Gotchas

- Envelopes and rosters left in `profiles/<name>/bot_relay` by older code are ignored after this patch. They are not
  delivered and not cleaned up.
- All profiles of one install share one outbox. Code that tells senders apart must use `from_profile`, not the mailbox
  path.

## Plugin?

No core-side plugin surface. The Desktop half of Bot Mode (`hermes-bots`, inside core's Desktop app) drains the
mailbox; 1691 makes the producer write where that drain already reads.
