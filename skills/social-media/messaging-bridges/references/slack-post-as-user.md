# Posting / replying in Slack as the user (not the bot)

Use when a message must come from the user's own Slack account (e.g. a thread reply continuing posts the user already made), not from the Hermes `xoxb-` bot.

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

`browser_exec(local=True)` cannot attach while Chrome is running (profile lock). Use the **chrome-devtools MCP** (`--autoConnect` attaches to the live Chrome):

1. `mcp__chrome_devtools__new_page(url="https://app.slack.com/client/<TEAM_ID>/<CHANNEL_ID>", background=true)` — note the returned page id.
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

## Formatting (`text` field is Slack mrkdwn, not Markdown)

- Bold is `*x*` (single asterisks); `**x**` renders literally.
- Bullets: literal `•` lines; Slack has no list syntax in plain `text`.
- Bare URLs auto-link (Slack rewrites them to `<url>`); inline code with backticks — escape them inside a JS template literal.
- Thread URL `p1791620915007619` → `ts` `1791620915.007619` (insert dot before last 6 digits).
