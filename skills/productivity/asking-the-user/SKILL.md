---
name: asking-the-user
description: "Use when user must act: sudo, sign-in, Inbox, a Mac screen."
triggers:
  asks:
    - "I need you to sign in to this site"
    - "can you click Allow on the mini"
    - "this command needs my sudo password"
  not_for: "a decision filed as a Kanban card (use kanban-orchestrator); driving a page yourself (use the browser tools)"
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [user, handoff, terminal, chrome, screen-sharing, inbox, approval]
    related_skills: [kanban-orchestrator, computer-use]
---

# Handing a step to the user: terminal, Chrome, another Mac, the Inbox

The one-line rules stay in the user's `~/AGENTS.md` ("Commands That Need Me", "Proposing Work (Inbox)"). This
skill is how to carry them out. A decision or approval that should wait on the board is a blocked Kanban card
(`kanban-orchestrator`, "Card mechanics for this user"), not anything here.

**Look first:** can an agent do this step itself (a broker token, `remote_approve_dialog`, the browser tools)?
Hand it over only when it can't.

## Commands that need the user

- Never ask the user to run a shell command themselves: no "run this in your terminal" text and no copy-paste
  instructions. When a command needs them (sudo/password, an interactive login or OAuth, a TTY prompt, their
  terminal's macOS permissions, or anything you won't run unattended), call `run_in_user_terminal`. It opens a
  terminal window, shows the command, the user confirms there, it runs, and you get the exit code and output.
- The tool is eager on most hosts. Where it isn't in the tool list, call it through `tool_call` (name
  `run_in_user_terminal`); never fall back to pasting the command.
- Remote hosts too: `ssh -t <host> 'sudo ...'`. When the SSH key is refused, run
  `ssh-copy-id -i ~/.ssh/id_ed25519.pub <user>@<host>` there.
- Get a go-ahead in chat first for anything destructive: the window runs the command at once, with no y/N step.
- ask-gate refuses a final reply that hands such a step back, and user-steps drops it from the reply.

## Chrome steps

- Before asking the user to do anything in the Chrome that `browser_exec` drives (sign in, 2FA, captcha, click,
  approve), bring that window and tab to the front with `browser_focus` (deferred: `tool_call` name
  `browser_focus`, optional arg `match` = a URL/title substring).
- Say the tab is already in front only when `browser_focus` returned the tab you are asking them to use. A `match`
  that hits no tab returns `focused: false` plus the open tabs: open the page first, then focus it.
- If the user says they can't see the window or page, look at the browser (list tabs / screenshot) before you
  answer. Never repeat the ask unchecked.
- The browser-focus plugin also focuses automatically for sign-in/sign-up pages and for replies that name the page
  they ask the user to use. Don't skip the call on that assumption.
- When the user is done there, send the window back with `browser_focus` `{hide: true}`. A sign-in tab hides itself
  once the login completes, and shown windows auto-hide after 20 min.

## Another Mac's GUI

- Before asking the user to click a permission prompt on another Mac ("X would like to access...", Allow/OK), try
  it yourself: `tool_call` name `remote_approve_dialog`, args `{host}` (hosts: `remote_screen_hosts`). It only
  approves allowlisted requesters and refuses password prompts. If it returns `blocked` or `click_ignored`, ask the
  user, with the Screen Share button it gives you.
- To look at or click anything else on another Mac, use `remote_screenshot` / `remote_input` (all deferred: call
  via `tool_call`, no search needed). Never type a password or code with `remote_input`.
- Whenever you ask the user to do something in another Mac's GUI, put a clickable Screen Share button next to the
  instructions: a standalone `::screenshare{host="verified-host-or-ip" label="Computer name"}` directive, not
  inside a code fence.
- Discover and verify an address that is reachable from the user's local Desktop. Never invent one, and never
  assume a gateway-only SSH alias resolves there. Use a DNS name or IPv4 address, optionally with a port; the
  button does not support IPv6.
- The button opens Screen Sharing on the user's local Desktop through its client OS, even for a remote-gateway
  chat. Never launch it on the remote gateway, and never auto-open it without a click. If the target is not a Mac,
  or the client lacks a VNC handler, say so and explain the right remote-desktop fallback.
- Commands still go through `run_in_user_terminal`. Screen Share is for GUI interaction, not a substitute for the
  command approval flow.

## Inbox posts

- The Inbox is only for a decision only the user can make. Anything an agent can fix (red CI, a broken job, a stuck
  card) is a Kanban card, never an Inbox card, and never both for the same thing. Monitors and crons follow this too.
- Post when you find something worth doing that needs the user's go-ahead while they are not in the conversation
  (cron jobs, Kanban workers, audits). Use `inbox_post` (deferred: `tool_call` name `inbox_post`, no search
  needed) with:
  - `title`, and `body` (what you found, with evidence);
  - `accept_prompt`: a self-contained instruction that runs on "Do it";
  - a stable `key` for recurring findings;
  - `ask={interaction, why_human, action}`. `interaction` is one of approval | credential | publish_as_david |
    physical | preference; `why_human` says why only the user can do it; `action` is the exact thing they do.
- A post without `ask` is refused (`ask_missing`), and the error shows the shape. Repost with `ask`, or use a Kanban
  card if an agent can do the work.
- A Kanban worker whose card has a `human_request` gets `ask` filled in from it automatically.
- Don't post status or finished work.
- The noise breaker: the inbox refuses a poster whose last 3 cards the user archived untouched, and files a card to
  fix that poster.

## Done means

- [ ] No reply asks the user to paste or run a command; `run_in_user_terminal` was used instead.
- [ ] Every Chrome ask came after a `browser_focus` that returned that tab.
- [ ] Every other-Mac GUI ask tried `remote_approve_dialog` first (for prompts) and carries a verified `::screenshare` button.
- [ ] Every Inbox post is a user-only decision with `ask` set; agent-fixable work went to a Kanban card.
