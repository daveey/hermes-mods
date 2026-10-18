# WhatsApp: groups lost after a number change

Symptom: 1:1 chats work but the user can't post in any group, or groups have gone quiet. Usual cause: a Change Number moved the account (with every group and its admin rights) to another number, and that number is now registered elsewhere, often the bot's WhatsApp Business. Re-registering the original number gives a fresh account with no groups.

## Diagnose (read-only, in this order)

1. **Mac WhatsApp desktop DB.** Copy it first, because the app holds it open: `cp ~/Library/Group\ Containers/group.net.whatsapp.WhatsApp.shared/{ChatStorage,LID}.sqlite* <scratch>/`.
   - Get the newest group vs 1:1 message: `ZWAMESSAGE` with `ZFROMJID`/`ZTOJID LIKE '%g.us'`. Dates are Core Data seconds, so add `978307200`. Hours of silence in busy groups while 1:1 chats still arrive means the account is no longer in the groups.
   - Number-change system rows are `ZMESSAGETYPE 10`. Join to `ZWACHATSESSION.ZPARTNERNAME` for names.
   - Get the user's own LID from `LID.sqlite` `ZWAZACCOUNT` (`ZPHONENUMBER` → `ZIDENTIFIER`).
   - `ZWAGROUPMEMBER.ZISACTIVE` is NOT a membership flag (it's mostly 0), and the user's own row is usually absent. Don't infer membership from that table.
2. **The suspected holder account's bridge log** (e.g. the bot's whatsmeow bridge). `failed to decrypt group message: no sender key ... in <group>@g.us` lines for the user's groups mean that account is in them.
3. **Confirm with a whatsmeow probe** against that bridge's store. `GetJoinedGroups` returns, for each group: name, member count, `IsParent`/`IsDefaultSubGroup`/`LinkedParentJID` (communities), and per participant `JID`/`PhoneNumber`/`LID` with `IsAdmin`/`IsSuperAdmin`. Report how many groups the holder is in, in how many it is admin, and that the user's number/LID is in none.
   - Build a throwaway Go tool with the bridge's own `go.mod`/`go.sum` (rename the module), so the whatsmeow version matches.
   - Run it with the bridge stopped, using one script: `launchctl bootout gui/$(id -u)/<label>`, check `pgrep` shows it gone, run the tool, then `launchctl bootstrap gui/$(id -u) <plist>`. Always restart the bridge, even if the tool fails.
   - macOS has no `timeout`: use `perl -e 'alarm 90; exec @ARGV' <cmd>`.
   - Write its JSON lines to a file and summarise them in Python; there can be hundreds of groups.

## Fix options (pick by coverage)

- **Move the account back (covers every group, keeps admin rights).** In the app that now holds the account, run Change Number from the holder number to the user's number. Then re-verify the user's number in their regular WhatsApp, because groups follow the number on re-registration. Re-register the bot's number fresh in Business and re-link its bridge. Before starting, have the user back up regular WhatsApp's chats. Changing the number also logs out linked devices, so plan to re-link the desktop apps and bridges. WhatsApp may rate-limit repeated number changes; if it refuses, use the partial fix below.
- **Re-add from the holder's admin rights (partial; no phone needed).** `UpdateGroupParticipants(Add)` then `(Promote)` for the user's `<pn>@s.whatsapp.net`, but only in groups where the holder is admin. Where it isn't admin, an admin must invite the user. The holder then has to `LeaveGroup` everywhere, which posts a notice in every group. Don't mix this with the move-back option: do one or the other.
- Adding, promoting and leaving are visible to group members. Present the evidence and the recommended option before running them at scale.

## After the move: chat state that followed the account

Archive, pin, mute and mark-read are account-level app state (collection `regular_low`), not per-app. Anything the user did while the account was in the other app (e.g. archiving everything in Business) follows the account back to regular WhatsApp. The phone has no bulk unarchive, so fix it from a temporary linked device:

1. **Snapshot what was archived before the incident.** The Mac desktop app is logged out by the number change, so its `ChatStorage.sqlite` is frozen at pre-incident state. Run `select ZCONTACTJID from ZWACHATSESSION where ZREMOVED=0 and ZARCHIVED=1` to get the keep-archived set. It's usually spam groups and chats the user archived on purpose, so never unarchive those.
2. **Link a throwaway whatsmeow client to the user's account.** Give it its own new store and use `store.SetOSInfo("<name>", ...)` so the user can recognise it under Linked Devices. Write each QR rotation to `qr.txt` and render it on an auto-refreshing HTML page in the preview pane. Tell the user to scan it from **regular** WhatsApp. The QR channel gives up after ~6 rotations (~2 min) with `timeout`; when the run exits that way, restart it with a fresh store and re-show the page instead of reporting success.
3. **Read archive state.** Register an `*events.Archive` handler, then call `FetchAppState(ctx, appstate.WAPatchRegularLow, true, false)`; on a FRESH store the full sync emits one event per chat. The chats with `Action.GetArchived()==true` are the archive set. Match each JID against the keep set in both forms, using `Store.LIDs.GetPNForLID` / `GetLIDForPN`, because the desktop DB and app state can use different forms.
4. **Do a dry run first:** write jid, name and keep to JSONL and report the counts. Save that JSONL as the target list.
5. **Apply from the saved JSONL, never from a second sync.** A second run on the same store gets no `Archive` events (the app-state versions are already stored, so nothing is re-emitted) and silently finds 0 chats to change. Have the tool take `-targets dryrun.jsonl` and unarchive every non-keep row, or do the dry run and apply in one process. For each chat call `SendAppState(ctx, appstate.BuildArchive(jid, false, time.Time{}, nil))`, with a short sleep between calls (~570 chats took ~4 min). Then call `Logout(ctx)` so the temporary device leaves Linked Devices. Keep the JSONL as the undo list.
6. **Verify** by asking the user to check their phone's chat list. A successful `SendAppState` only means the server accepted the patch.

## After restoring groups: clear the "you were added" unread badges

Re-adding the user to groups leaves each with a small unread count made only of system notices. Clear just those, never real unread messages:

1. **Pick targets from the live Mac desktop DB** (once the desktop app is re-linked it updates within seconds): copy `ChatStorage.sqlite*`, then `select ZCONTACTJID, ZUNREADCOUNT, ZPARTNERNAME from ZWACHATSESSION where ZCONTACTJID like '%g.us' and ZREMOVED=0 and ZUNREADCOUNT>0`. Group by `ZUNREADCOUNT`; the notice-only groups cluster at one value (typically 2). The desktop DB often lacks the recent rows, so don't judge from its `ZWAMESSAGE`.
2. **Exclude any group with real messages since the restore,** checked in a linked bridge's `messages.db` (`chat_jid=? and is_from_me=0 and timestamp > <restore date>`); it stores text messages only, so system notices never count. Save the remaining list as JSON (the undo list).
3. **Apply via an endpoint on the running bridge** for the user's account: `SendAppState(ctx, appstate.BuildMarkChatAsRead(jid, true, time.Time{}, nil))` per chat, ~300 ms apart. A zero time means "read up to now".
4. **Canary one chat, then re-read its `ZUNREADCOUNT`** from the live desktop DB: it drops to 0 after ~20-30 s, not instantly. Only then run the batch (background, notify on completion) and report per-chat errors.
