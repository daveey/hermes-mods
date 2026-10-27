# Posting / replying in Slack as the user (not the bot)

Use when a message must come from the user's own Slack account (e.g. a thread reply continuing posts the user already made), not from the Hermes `xoxb-` bot.

## 0. Desktop/CLI turns without a Slack adapter

If this gateway has no Slack platform enabled but another gateway runs the user's Slack bot, post through that bot
from its own host when the user asks to "use the Slack integration" (or the browser is blocked, e.g. quiet mode),
instead of deferring the send. Any private notes on which gateway and how belong in a private skill reference.

## 1. Find how earlier posts in that thread were made

The brief often says "use the tooling used before". Don't grep `~/.hermes` for a script — search the session DB for the channel ID and read the tool names:

```bash
cd ~/.hermes
sqlite3 state.db "select tool_calls from messages where session_id='<parent>' and tool_calls like '%<CHANNEL_ID>%';" \
  | grep -o '\\"name\\\\*": \\\\*"[^\\"]*' | sort | uniq -c
```

Typical answer: `connectors__slack__SEND_MESSAGE` / `FETCH_CONVERSATION_HISTORY` (Composio connector via the Nous portal). Messages it sends carry `bot_profile.name: Composio` but `user:` = the user's ID, i.e. they appear as the user.

## 2. Preferred path: the connector, if this session has it

`tool_call` → `connectors__slack__SEND_MESSAGE` with `channel`, `thread_ts`, `markdown_text`. If it returns "Connectors are not available in this session", the session isn't on a Nous-managed-tools provider or the portal token is missing; the fix is signing in to the Nous portal in Hermes. Go to step 3 rather than hacking the gateway client.

## 3. Working fallback: the user's signed-in Slack tab in Chrome

Use the **chrome-devtools MCP** to reuse a signed-in live Chrome session; if a separate browser launch encounters a profile lock, attach to the existing browser instead of restarting it.

1. Call `mcp__chrome_devtools__list_pages` and reuse an existing Slack page by its `pageId`; otherwise open `mcp__chrome_devtools__new_page(url="https://app.slack.com/client/<TEAM_ID>/<CHANNEL_ID>", background=true)`. Call `auth.test` through the in-page API and confirm the returned user ID before posting; an open workspace tab alone does not establish the sender.
2. `evaluate_script` (pass `waitForStableDom: false`) calling Slack's web API from inside the page with the session token:

```js
async () => {
  const t = JSON.parse(localStorage.localConfig_v2).teams['<TEAM_ID>'];
  const fd = new FormData();
  fd.append('token', t.token);          // xoxc session token; never print it
  fd.append('channel', '<CHANNEL_ID>');
  fd.append('thread_ts', '<PARENT_TS>'); // reply in thread; omit = new top-level message
  fd.append('text', text);               // Slack mrkdwn
  fd.append('unfurl_links', 'false');
  const j = await (await fetch('/api/chat.postMessage', {method:'POST', body:fd, credentials:'include'})).json();
  return {ok: j.ok, err: j.error, ts: j.ts};
}
```

3. **Before posting**, call `/api/conversations.replies` (same pattern, `ts=<PARENT_TS>`) and check (a) `t.user_id` equals the `user` on the earlier posts (right account), (b) the content isn't already posted (avoid duplicates on retries).
4. **After posting**, read `conversations.replies` again and verify the returned timestamp, sender, parent thread and expected text. Account for Slack's documented bare-URL → `<url>` normalization before treating a text mismatch as a failed send; inspect the returned message rather than reposting. Obtain the permalink with `chat.getPermalink(channel=<CHANNEL_ID>, message_ts=<returned_ts>)`—the parameter is `channel`, not `conversation_id`.

## Creating a private channel as the user

Use the same authenticated in-page API pattern above for an explicitly requested channel:

1. Resolve people with paginated `users.list`; distinguish human accounts from similarly named bots. Check `conversations.list(types=private_channel, exclude_archived=true)` for an existing channel before creating one.
2. Call `conversations.create` with `name=<requested name without #>` and `is_private=true`. Keep the returned channel ID; the creator is already a member.
3. Call `conversations.invite` with `channel=<ID>` and `users=<comma-separated resolved IDs>` for existing workspace members.
4. Read `conversations.info` and `conversations.members` back; verify `is_private=true`, the exact name, and every intended internal member. Treat external invitations as a separate operation, not as covered by this membership check.

If the workspace sign-in page offers Google SSO, follow its **Sign in with Google** link before asking the user to log in: an existing Google session can authenticate Slack without another prompt. Confirm the resulting identity with `auth.test` before writing.

## Inviting external collaborators with Slack Connect

After creating and verifying the private channel:

1. Send `conversations.inviteShared` using the in-page form-encoded API pattern: `channel=<ID>`, `emails=<recipient email>`, **`external_limited=false`** when the recipient should invite coworkers. The default is limited posting only; the UI equivalent is **Post and invite**, not **Only post**. Keep the returned `invite_id` and distinguish invited from joined.
2. On `inviter_email_not_verified`, complete the **inviter's** email verification before retrying; switching from API to UI does not bypass it. The channel's Add people flow → external organization → permissions → Send Invitation exposes **Verify Email**. Have the user complete the code through the supported secure/browser flow, then retry the original invitation once. Google SSO authentication alone does not establish this email-verification state.
3. Read back `conversations.info`: `properties.has_slack_connect_invite_created` confirms an invite was created, but does not prove a particular recipient accepted. For a link to include in an email reply, open channel details → **Settings → Slack Connect → Share Link**. Check that the dialog explicitly says recipients can **post and invite their coworkers**, and note its displayed expiry. Use **Copy Link** to obtain the actual invitation URL; a normal channel URL does not grant access.
4. If Copy Link reports success but the local clipboard is empty, capture the browser's copy operation instead. Slack may use `document.execCommand('copy')`, so intercepting only `navigator.clipboard.writeText` misses it. Temporarily wrap `document.execCommand`, capture `window.getSelection().toString()` immediately before forwarding the `copy` call to the original function, click Copy Link, read the captured URL, then restore the original function. Keep this capture limited to the invite-link action.
5. Send the requested email reply with that link and the permission wording, then read back the sent message in the original thread. Call the Slack permission **Post and invite**, not channel admin; GitHub Admin is a separate grant. For a later “why aren't they here?” check, resolve `conversations.members` through `users.info` and inspect connected organizations in `conversations.info`. An old successful invite plus absent membership establishes **invitation sent; not currently joined**, not that the invitation is still pending. Distinguish pending, withdrawn and expired only when current invitation evidence supports it.

## Finding "our channel" with named people or bots

The user may have several workspaces in `localConfig_v2.teams`. Confirm the workspace and sender with `auth.test`, then resolve the destination from current membership and discussion:

1. Paginate `users.conversations` with `types=public_channel,private_channel,mpim`. When a channel name contains the named collaborator, shortlist it first; names are discovery hints, not identity proof.
2. Read `conversations.members` for candidates and resolve member IDs with `users.info`; bots count as collaborators. If no candidate name is useful, paginate `users.list` to resolve the requested people and filter channels by their member IDs.
3. Read `conversations.history` to confirm the channel's purpose and check duplicates. Fetch relevant threads with `conversations.replies`: channel history contains roots, not the advice or follow-up reports inside replies. Paginate when `has_more` or a cursor indicates omitted messages.
4. Continue an existing thread for the same discussion; use a new top-level message for a distinct experiment/report. Tag verified collaborators as `<@UID>` and explicitly identify assistant-authored messages as posted on the user's behalf.
5. Read back the returned message timestamp, sender, destination and expected text. For a root message, `conversations.replies(ts=<returned_ts>)` also checks whether answers have arrived; for a reply, read its parent thread. Return the permalink and distinguish “posted; no reply yet” from an actual collaborator recommendation. Do not infer that they have no fix merely because no answer has arrived.

## Formatting (`text` field is Slack mrkdwn, not Markdown)

- Bold is `*x*` (single asterisks); `**x**` renders literally.
- Bullets: literal `•` lines; Slack has no list syntax in plain `text`.
- Bare URLs auto-link (Slack rewrites them to `<url>`); inline code with backticks — escape them inside a JS template literal.
- Thread URL `p1791620915007619` → `ts` `1791620915.007619` (insert dot before last 6 digits).
