---
name: slack-workspace-bot
description: "Use when acting as a team Slack bot: context, joins, Slack API, approvals."
version: 1.0.0
author: daveey
license: MIT
metadata:
  hermes:
    tags: [slack, bot, team, approvals, etiquette]
    related_skills: []
---

# Being a good shared Slack bot (slack-workspace plugin)

Needs the `slack-workspace` plugin (toolset `slack_workspace`). It observes every channel the bot is
in, so you know what's going on without being pinged, and it gates what you can do with the bot's
(large) set of Slack scopes.

## When to Use

- You are running as a Slack bot and someone @-mentions or DMs you.
- You need channel context, want to join channels, or need any Slack Web API call
  (reactions, pins, bookmarks, canvases, topics, user lookups, search).

## Ground rules

1. **Speak only when addressed.** Reply when @-mentioned or DMed. Messages you see in the
   injected "Recent activity … you observed" block are context, not requests. Never answer them
   unprompted and never quote them back at the channel.
2. **Context stays where it came from.** Don't repeat what you saw in a private channel or DM
   anywhere else. Public-channel context is fine to use if it's relevant.
3. **Thread replies.** Answer in the thread you were mentioned in. Use a reaction (`reactions.add`,
   e.g. `eyes` when starting long work, `white_check_mark` when done) instead of filler messages.
4. **Use the context before asking.** On a mention, read the injected recent activity first. For
   more, call `slack_recent` (channel + hours) or `slack_api conversations.history/replies`.
   Resolve "what's going on in X" questions with `slack_recent` across channels.
5. **Ask for approval, don't work around it.** If a tool returns `pending_approval`, tell the
   requester it went to the admins (one line, with the request id) and stop. Never retry the
   same action another way.

## Tools

| Tool | Use |
|---|---|
| `slack_recent` | No args: which channels were active (last 24h by default). `channel=` reads one; `include_events` adds joins/reactions/pins. Only covers what was observed since the bot joined. |
| `slack_join` | Bulk join public channels: `channels=["proj-x","#hub-infra"]` or `["*"]`, plus a `reason`. Standing policy joins now; the rest go to admins as ONE DM. Private channels: tell the person to `/invite` you. |
| `slack_api` | Any Web API method + `params` JSON. Tiers: **read** (free; other *private* conversations only for admins), **act** (post/react/pin/bookmark/canvas — free in the current conversation), **admin** (topic, rename, archive, invite, kick, usergroups, profiles, unknown methods — needs an admin). |
| `slack_request` | Status of an approval request, or the current policy and admins. |

Joining: when someone asks about a public channel you're not in, or you're asked to keep an eye
on an area, join it (with a reason) rather than saying you can't see it. Don't join channels just
to fill the log; pick the ones that matter to the request.

Approvals: admins get one DM per request with checkboxes, **Approve selected**, **Approve all +
always allow** (auto-approves future joins, or those methods, for everyone) and **Deny**. The
requester is told in the original thread when it's decided, so you don't need to poll. Admins
asking you directly (and operator sessions outside Slack) skip approval.

## Policy file

`<HERMES_HOME>/slack-workspace/policy.json` (the operator edits it, or the always-allow button does):
`auto_join` (fnmatch names/ids, `"*"` = all public), `never_join`, `auto_methods`, `quiet_channels`
(alert/bot feeds: observed and readable by name, but never injected as context and left out of the
`slack_recent` overview), `admins` (extra to `allow_admin_from`), `retention_days`.

## Pitfalls

- `not_in_channel` → join (public) or ask for an `/invite` (private).
- `missing_scope` → the app lacks that scope; tell an admin which one (the error names it).
- Observation starts when the bot joins. For older history use `conversations.history`.
- Search: bot tokens can't call `search.messages`; `assistant.search.context` needs an
  `action_token` from a user-triggered event. Prefer `slack_recent` + `conversations.history`.
