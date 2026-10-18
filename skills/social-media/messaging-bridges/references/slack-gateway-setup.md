# Slack DMs via the Hermes gateway

The Slack adapter ships with Hermes; setup is a Socket Mode Slack app + two tokens + a running gateway.
The user must click through api.slack.com (only a workspace member can create/install apps). Do every
other step yourself and hand them one short numbered list.

## Diagnose first

- One Slack app per Socket Mode consumer. A new bot persona or a non-Hermes service (e.g. a home-assistant
  bridge) gets its OWN app and tokens, never another gateway's: Slack spreads events across every open
  connection of an app, so a second consumer silently steals half of the first one's DMs.
- Never write a second app's tokens into `~/.hermes/.env` under `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN` (the
  stock clipboard helper does): that replaces the gateway's own Slack identity. Use service-specific names
  (e.g. `SLACK_<SERVICE>_BOT_TOKEN`) in that service's env file. When the service runs on another host, the
  same clipboard pattern works: a background watcher (`pbpaste` poll, match `xoxb-`/`xapp-`, clear the
  clipboard) pipes the tokens over ssh stdin into the remote env file (chmod 600) and runs the installer,
  printing no token; start it with `terminal(background=true, notify=[...])` before handing the user the steps.
- `grep -iE "\[slack\]|slack.*(token|connect)" ~/.hermes/logs/gateway.log | tail` — `SLACK_BOT_TOKEN not set` /
  `SLACK_APP_TOKEN not set` means `platforms.slack.enabled: true` but no credentials; the platform is parked.
- Check secret stores the user already uses before asking them to create an app.

## Procedure

1. Generate the manifest: `hermes slack manifest --agent-view [--name <BotName>] --write` →
   `~/.hermes/slack-manifest.json` (Socket Mode, DM scopes, `message.im`, every gateway slash command).
2. `pbcopy < ~/.hermes/slack-manifest.json`, open `https://api.slack.com/apps?new_app=1` in the user's
   browser (`open -a "Google Chrome" URL`). User: **From an app manifest → workspace → JSON tab → paste →
   Create**. Do not pass the manifest as a `manifest_json=` URL parameter: the full manifest is ~15k chars and
   Slack drops it. The "Socket Mode requires additional setup" warning is expected — Create anyway.
3. App token: **Basic Information → App-Level Tokens → Generate**, scope `connections:write` (`xapp-…`).
4. Bot token: **Install App → Install to Workspace → Allow** (`xoxb-…`).
5. Store tokens without chat exposure: have the user copy each token and run a clipboard helper
   (`scripts/slack-token-from-clipboard`, installed at `~/.hermes/bin/`) that writes
   `SLACK_APP_TOKEN`/`SLACK_BOT_TOKEN` to `~/.hermes/.env` by prefix, chmod 600, and clears the clipboard.
   If the user pastes a token into chat anyway, save it (don't refuse), then tell them it is now in history and
   how to rotate it.
6. Verify the bot token: `auth.test` (prints team + bot user). Find the user's member id with `users.list`
   (bot has `users:read`; `users.lookupByEmail` needs `users:read.email`, which the manifest lacks). Set
   `SLACK_ALLOWED_USERS=<U…>` in `.env` so only they can drive the agent.
7. Run the gateway supervised: `hermes gateway install` (launchd). Then confirm `[Slack] Socket Mode connected`
   and `✓ slack connected` in `gateway.log`, and send a test DM with `chat.postMessage` to the member id
   (a user id as `channel` opens the DM). Tell the user to reply and optionally `/sethome` in that DM.

## App settings URLs

The app id is the second field of the app token (`xapp-1-<APP_ID>-…`). Reliable pages:
`https://api.slack.com/apps/<APP_ID>` (settings root; use the sidebar from there) and
`https://api.slack.com/apps/<APP_ID>/install-on-team`. Don't guess other sub-paths (e.g. `/app-manifest` 404s);
open the root and name the sidebar item.

## Renaming / avatar

- Rename: regenerate with `--name <New>` (sets app name and bot display name), copy to clipboard, user pastes into
  **Features → App Manifest → JSON → Save**; reinstall if prompted (bot token normally unchanged; if it changes,
  rerun the helper + `hermes gateway restart`).
- App icon: **Basic Information → Display Information → App icon** (square PNG, 512–2000px). When editing a
  generated avatar on feedback, change only what was asked (image edit with "keep everything else identical")
  rather than restyling the whole image.
