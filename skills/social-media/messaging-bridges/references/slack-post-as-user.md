# Posting / replying in Slack as the user (not the bot)

Use when a message must come from the user's own Slack account (e.g. a thread reply continuing posts the user already made), not from the Hermes `xoxb-` bot.

## 0. Desktop/CLI turns: the Slack integration is the nous-1 `alpha` bot, not the browser

The Mac gateway has `platforms.slack.enabled: false`, and its `SLACK_BOT_TOKEN` is commented out. The Hermes Slack integration is David's bot **Alpha**: profile `alpha` on nous-1. When the user says "use the Slack integration", or the browser is blocked (quiet mode), post as Alpha through the remote shell. Don't defer the send.

1. Build a small Python script. It reads `SLACK_BOT_TOKEN` from `/opt/data/profiles/alpha/.env` and **reads** with GET plus a urlencoded query: `conversations.replies` rejects a JSON body with `invalid_arguments`. It **posts** `chat.postMessage` as JSON, checks for duplicates beforehand, and reads back afterwards. It prints only ok/ts.
2. Base64 it into a `printf %s '<b64>' | base64 -d > /opt/data/cache/x.py && cd /opt/hermes && .venv/bin/python /opt/data/cache/x.py; rm -f …` command file.
3. `scp` `remote_gateway.py` (administering-remote-hermes-gateways/scripts), the command file and a wrapper to the mini (`ssh -i ~/.ssh/id_ed25519 daveey@davids-mac-mini-1`). Run `REMOTE_GW=https://skint-polycentrism-9330.agents.nousresearch.com REMOTE_GW_TOKEN=~/.bb-hermes/cred-relay/nous1_token.json ~/code/hermes-agent/.venv/bin/python /tmp/remote_gateway.py exec "$(cat cmdfile)"` there. Then delete the temp files.
4. The message appears as Alpha, so open it with "_Posting for David (from his Hermes, Alpha)._". Alpha must be a member of the channel.

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
4. **After posting**, read `conversations.replies` again and confirm the new `ts` is present with `thread_ts == PARENT_TS` and the expected text. Give the user the permalink `https://<workspace>.slack.com/archives/<CHANNEL>/p<ts without dot>?thread_ts=<PARENT_TS>`.

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
