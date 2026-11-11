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

**Look first:** check supported credentials and authenticated sessions before handing over a step. If live state
contradicts an approved plan, use commit history and `cross_task`/`cross_session` to recover the newer setup's
owner and actual user decision before offering choices: another session may already have resolved the premise.
Do not infer personal execution or authorization from an SSO identity alone. Record the superseding decision on
the existing owners and resume replanning; do not mechanically refresh/apply an obsolete plan or undo newer work.
Before filing an `action_request` for a card's ask, read the card's newest comments for a request id another chat
already published, and the card's named sole owner chat: if either exists, don't file a second one. Duplicates
split the user's answer, and the copy not consumed sits stale in For-you. Withdraw yours as soon as the card shows
the ask was answered elsewhere or the card left `blocked`.
When told “do it yourself,” remove discretionary blockers, not identity or explicit-deny boundaries. For an
ambiguous acceptance tolerance, test a stricter sufficient bound without redefining the requirement to fit results.

## Commands that need the user

- Never ask the user to run a shell command themselves: no "run this in your terminal" text and no copy-paste
  instructions. When a command needs them (sudo/password, an interactive login or OAuth, a TTY prompt, their
  terminal's macOS permissions, or anything you won't run unattended), call `run_in_user_terminal`. It opens a
  terminal window and runs the command; do not assume a separate confirmation gate. Read the returned status, exit code and output.
- For an approved owner-chat-only handoff, read the recipe, pins and latest receipts, then establish one operator
  before `run_in_user_terminal`. If coordination is queued or unanswered, read the target transcript AND the actual
  unit/process, invocation receipt and log: another chat may have launched before replying. An active invocation
  takes precedence over missing acknowledgment; do not duplicate it. For an apply, also inspect the lock holder
  (`fuser <lock>`); preserve worker markers and guards.
  Return verified results to the existing card rather than asking the user to relay them. When a card blocks on
  "from your own Hermes chat open the owner-terminal flow" and this chat IS the user's Desktop chat, the launch is
  yours: run the reviewed recipe through `run_in_user_terminal`, comment the job id and log path, and unblock the
  card; don't relay the ask. Before extending a bounded service hold, exercise the current updater's read-only
  readiness predicate against the proposed stopped state: a longer hold cannot fix a guard that rejects deliberately
  stopped services. Re-review changed timing and guard compatibility rather than treating duration as the only defect.
  Report the script deadline separately from the service timeout and cleanup allowance; none alone proves restoration.
- Serialize owner-context tests too, and give each attempt a unique log directory: isolated HOME/board fixtures
  still overwrite evidence when two chats share output paths. If that happened, hash the retained coherent logs
  and count them as one proof, not two independent runs; do not rerun merely to recreate overwritten evidence.
- `run_in_user_terminal` returning `ok:false` with "`<device>` claimed the request but sent no answer in time" is still
  pending, not failed: call it again with only that `job_id` (and a wait); never resend the command, which would run it twice.
- A hub `systemctl --user start|stop` of a gateway unit from the chat's own terminal is refused by the gateway
  self-stop guard; put it in the same `ssh -t ubuntu@hermes-hub '...'` user-terminal command as the step that needs it.
- The tool is eager on most hosts. Where it isn't in the tool list, call it through `tool_call` (name
  `run_in_user_terminal`); never fall back to pasting the command.
- The window and `cwd` belong to the user's Mac, even from a hub chat. For a reviewed script already on the hub,
  omit the hub `cwd` and run `ssh ubuntu@hermes-hub 'cd <checkout> && <clean-tree/exact-SHA guards> && <script>'`
  through `run_in_user_terminal` (name the user: the Mac's default login for the bare alias is refused with
  `Permission denied (publickey)`); do not copy it to the Mac merely to execute it back on the hub. Stage files with
  `tailnet_copy` only when the command actually needs local Mac files. For a hub-held script that must run
  as root on a THIRD host the Mac can reach (e.g. the mini), `tailnet_copy put` it to the Mac's `/tmp`, have the
  window print its sha256 next to the expected one, `ssh <host> 'cat > /tmp/x' < /tmp/x`, then
  `ssh -tt <host> 'sudo ... > out; rm -f /tmp/x'`, copy the output back to the Mac, and `tailnet_copy get` it;
  passing a hub path to the window fails because the path does not exist on the Mac. In zsh, write SSH options inline rather
  than in an unquoted variable, which does not word-split.
- `ssh host 'cmd'` runs a non-login shell whose PATH lacks `~/bin` (where the hub's `gh` lives). A script that
  resolves tools with `which` / `shutil.which` passes under the agent's own terminal, then refuses mid-apply on the
  owner route. Before running a reviewed hub apply this way, check that the package pins absolute tool paths, or
  that its read-only `check` exercises the same lookup. On a hub, also wait for the core-update drain to end before an apply: a drain restarts owner gateways, so the
  owner check fails or the apply rolls back. Test `[ -e ~/.hermes/core-update/drain-hold ]` (it follows the link):
  the path is a symlink to an episode file and is deliberately left dangling when the drain ends, so waiting for
  the path itself to disappear never finishes.
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
- `browser_focus` can return `focused: true` with `shown_on_main_display: false` / `app_activated: false`: the
  agent Chrome is on a gateway's virtual display (hub: Xvfb `:99`) and the user sees nothing. Never say "it's in
  front" then; open the private noVNC view on the user's Mac instead (`references/hub-chrome-viewer.md`).
- If the user says they can't see the window or page, or reports a sign-in error without naming the site, look at
  the browser (`browser_cdp` `Target.getTargets`, then read the tab's text) before you answer. Never ask "which
  sign-in?" or repeat the ask unchecked. An OAuth callback showing "invalid state" usually means the login page was
  opened too long ago: decode the callback's `state` (base64 JSON with `exp`), navigate the tab to the app's own URL
  for a fresh login, and ask them to finish within its validity window.
- Judge a sign-in as done from the browser (URL past the login host, Access/session cookies present), not from
  the user's "Done" or "don't see it" on an earlier prompt.
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
- A macOS Automation prompt ("... wants access to control Terminal") in front of a `run_in_user_terminal` window is
  ONE human step: "click OK, then type the password in the window that opens". The job waits `awaiting_consent` and
  opens the window by itself after the click (retries every 15 s until max wait). Keep waiting with its `job_id`;
  never add "unblock the card" or "reply done" so an agent retries (coach `consent_relay` holds that reply).
- When the user answers "done" on a password/prompt step but the result is still absent, don't just re-ask. First check
  that their input actually reached the prompt: look for auth attempts (`log show --last 60m --predicate 'process ==
  "sudo"'`), and `sample` the app hosting the prompt. Terminal.app whose main thread sits in
  `CA::Context::commit_transaction` at 0% CPU (and `open -a Terminal` fails with -1712) is hung and swallows every
  keystroke; the user sees a beachball. If `ps -t <tty>` shows the stuck prompt is its only session, kill Terminal,
  reopen the same `.command` file with `open`, confirm a fresh `sample` is idle in `_BlockUntilNextEventMatchingListInMode`,
  then ask once more for the password in the new window.

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

## Reading the answer

- A bare "go" / "yes" after a reply that recommends several decisions approves all of them. Act on each one. Don't
  take it as approval of only the first and re-ask the rest: that costs the user another round trip.

## Done means

- [ ] An optional offer is worded "Optional: I'll keep X; say if you want Y." in a plain sentence, never under a
  "Decision for you:" header, and is not restated next turn. user-steps drops it, so no For-you request opens and the
  chat is not painted red. A choice that really blocks you gets a direct question instead ("Should I add it? yes/no").
- [ ] No reply asks the user to paste or run a command; `run_in_user_terminal` was used instead.
- [ ] Every Chrome ask came after a `browser_focus` that returned that tab shown on the user's display (else the noVNC view was opened).
- [ ] Every other-Mac GUI ask tried `remote_approve_dialog` first (for prompts) and carries a verified `::screenshare` button.
- [ ] Every Inbox post is a user-only decision with `ask` set; agent-fixable work went to a Kanban card.
