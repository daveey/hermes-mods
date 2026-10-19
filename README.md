# hermes-mods

Plugins, Desktop plugins and skills I've written for
[Hermes Agent](https://github.com/NousResearch/hermes-agent). I run all of them every day on macOS
(Hermes CLI, gateway and Desktop app). MIT-licensed, so take whatever is useful.

Several plugins are workarounds for open upstream issues. Each one says so in its `plugin.yaml` and
can be deleted once upstream ships the fix.

## Install

```bash
git clone https://github.com/daveey/hermes-mods ~/code/hermes-mods
cd ~/code/hermes-mods
./install.sh                # link + enable every plugin and desktop plugin, add the skills
./install.sh --community    # ...plus the community plugins I run (hermes-pack.yaml, pinned SHAs)
./install.sh --extras       # ...plus Home Assistant and Spotify (hermes-pack-extras.yaml)
```

`install.sh` symlinks, so `git pull` updates the mods in place (`--copy` freezes a version instead).
It never overwrites a real file or folder that's already there. Restart the gateway or Desktop backend
afterwards so the backend plugins load; desktop plugins hot-reload.

Doing it by hand, one mod at a time:

```bash
M=~/code/hermes-mods
ln -s $M/plugins/topic-shift ~/.hermes/plugins/topic-shift && hermes plugins enable topic-shift
ln -s $M/desktop-plugins/topic-shift ~/.hermes/desktop-plugins/topic-shift   # its Desktop half
hermes config set skills.external_dirs '["~/code/hermes-mods/skills"]'
```

## What isn't a plugin

- **[core/](core/)**: my changes to Hermes's own source: keyboard shortcuts, multi-gateway projects,
  session takeover, fork-friendly `hermes update`, and more. There's one doc per feature (behavior,
  code, tests, gotchas), plus `git am`-ready patches, so you or your agent can add them to your
  Hermes. Start with [core/README.md](core/README.md).
- **[shortcuts.md](shortcuts.md)**: every keyboard shortcut these mods add, and how to get each one.
- **[hermes-pack.yaml](hermes-pack.yaml)**: the community plugins I use, pinned
  (`hermes plugins pack install hermes-pack.yaml`).

## Plugins

| Plugin | Desktop half | What it does |
|---|---|---|
| [account-usage](plugins/account-usage/) | [yes](desktop-plugins/account-usage/) | Status-bar chips for pooled Claude / ChatGPT accounts. Records which pooled credential served the latest call and highlights it. |
| [anthropic-pool-status](plugins/anthropic-pool-status/) | — | Dashboard workaround: the Desktop Accounts card counts Anthropic OAuth logins in the credential pool. |
| [coach](plugins/coach/) | [yes](desktop-plugins/coach/) | One nudge bus. It hints the agent (inside the tool result, which you never see) when the same command fails 3×, when it polls with sleep loops, after 40 tool calls without an update, and when it edits the live Hermes checkout. It also shows you one-button panels above the composer: *Restart clean* (on the 3rd "still not working"), *Spin out* (past 250 msgs), *Add a line* (a bare screenshot sent mid-turn), *Move to board* (10th subagent or a job over 10 min) and *File it once* (the same Hermes bug in 2 sessions in a week). At most one per 15 turns, a dismissal mutes the rule, and 3 dismissals in a week turn it into digest-only. prompt-coach, topic-shift and spin-out share its rate limiter. It also carries a feature curriculum (`curriculum.yaml`, plus a private overlay): a one-line tip with *Try it* when its trigger fires (a recurring ask → cron, a checklist → todo, a long job → a Kanban card, the Kanban L1–L4 drills…), spaced out after each dismissal and retired once the hermes-coach miner sees you use the feature. Everything is logged to `coach/log.jsonl`. |
| [compute-rentals](plugins/compute-rentals/) | [yes](desktop-plugins/compute-rentals/) | Read-only inventory of rented compute (Vast, RunPod, Modal, Prime Intellect, SF Compute, EC2, Slurm over ssh) in one panel, plus which session started each box. Private clusters plug in as extra provider files kept outside the repo. |
| [fast-rate-limit-fallback](plugins/fast-rate-limit-fallback/) | — | On a long-reset rate limit, rotate pooled credentials first, then fall back to the next provider without sleeping through retries. |
| [inbox](plugins/inbox/) | [yes](desktop-plugins/inbox/) | Hermes Inbox: an **Inbox** section in the left pane (above Blocked) of proposals of work Hermes posts with the `inbox_post` tool, `hermes inbox post` or REST. Each message is a hidden session whose first message is the proposal; open it to read, **Do it** to run the proposed prompt (the chat moves to Recents), reply to adjust, or archive. Dedupe by `key`, unread state, every gateway. |
| [mcp-file-access](plugins/mcp-file-access/) | — | Ask (Once / Session / Always) before the Chrome DevTools MCP server reads or writes files outside its allowed folders. On approval, the file is routed through its temp folder, so no restart is needed. |
| [oauth-keepalive](plugins/oauth-keepalive/) | — | Refreshes pooled OAuth tokens (Claude, ChatGPT/Codex) before they expire, including idle accounts. |
| [prompt-coach](plugins/prompt-coach/) | [yes](desktop-plugins/prompt-coach/) | After you send a task request, offers a more ambitious rewrite (Altitude / Depth / Range, after Felix Rieseberg) in a panel above the composer: *Run in new session* (the original keeps running), *Replace* or *Keep mine*. A cheap model screens; a stronger one rewrites. Logs choices for a weekly digest. Shares [coach](plugins/coach/)'s rate limiter. |
| [core-update](plugins/core-update/) | [yes](desktop-plugins/core-update/) | Status-bar chip for a Hermes core update prepared in the background: *Building update…* while the next version is packed, then *Update ready — restart to apply*. Clicking it starts the apply-on-quit waiter and quits Desktop, so the update lands between quit and relaunch. It reads `$HERMES_HOME/cache/core-update/state.json`, which your updater writes (format in `core.py`); without one it stays hidden. |
| [crontab-guard](plugins/crontab-guard/) | — | Blocks sessions from rewriting the user crontab wholesale (`crontab FILE`, `... \| crontab -`, `-r`, `-e`). Edits go through `crontab_line.py` (locked, one line at a time, backs up first). Includes a Claude Code PreToolUse hook (`claude_hook.py`). |
| [quiet-mode](plugins/quiet-mode/) | — | `/quiet` (`/movie`) blocks every Hermes session on the Mac from opening windows, driving the GUI or browser, changing lights or TVs, or making sound. |
| [slack-workspace](plugins/slack-workspace/) | — | For a team Slack bot: records traffic in every channel it's in and adds that channel's recent activity as context when it's @-mentioned. Tools to see recent activity, join channels in bulk and call any Slack API method. Joins, posts outside the current conversation and admin methods go to the bot's admins as one DM with approve-selected / always-allow / deny buttons. |
| [session-mentions](plugins/session-mentions/) | [yes](desktop-plugins/session-mentions/) | `cross_session` tool: list, read and message sessions on any gateway connected to the Desktop app (local, Nous-hosted, remote). Each machine's own Desktop is its "home courier": sends into a chat open there go through the backend that owns it. Kanban tasks too: `@task:` / `@<title words>` / `@t_…` in the composer complete tasks from every board on every gateway (`@task:<board>[@<gateway>]/<id>`), and the `cross_task` tool lists, reads and comments on them. |
| [browser-focus](plugins/browser-focus/) | [yes](desktop-plugins/browser-focus/) | When Hermes needs you to act in the Chrome it drives (sign in, 2FA, captcha, click), it brings that window and the right tab to the front: automatically when a `browser_exec` result shows a sign-in/2FA/captcha page or the final reply asks you to do something in the browser, plus a `browser_focus` tool. Uses CDP `/json/activate` + `NSRunningApplication` (no Accessibility grant). Once the sign-in you were shown completes, it sends the window back off-screen; `browser_focus {hide:true}` does it on demand. Desktop status-bar toggle (`desktop-plugins/browser-focus`): **Browser hidden / Browser shown**, click to bring the agent's Chrome on/off screen yourself. Respects quiet mode. macOS. |
| [user-terminal](plugins/user-terminal/) | — | `run_in_user_terminal` tool: instead of asking you to run a command, Hermes opens a centred 110-column terminal window (iTerm, else Terminal.app, else cmux), shows why and the exact command, you type `y` there, it runs in that real TTY (sudo, logins work) and the exit code and output go back to the agent. Also rewrites headless `sudo` failures in terminal output to point the agent at the tool instead of core's SUDO_PASSWORD tip. macOS. |
| [spin-out](plugins/spin-out/) | [yes](desktop-plugins/spin-out/) | Finds the follow-up work at the end of a session and offers it above the composer: click a task to launch it as a new session, [All] for the rest, or [Kanban] to put them on the active board as cards. Shows up on its own when a turn finishes and its last reply mentions follow-up work (a cheap cue filter runs first; palette toggle to turn it off), and on demand with ⇧⌘F. Each child gets a self-contained brief and an @session pointer to the parent, and the parent is told on its next turn what was spun out and where, so it stops offering it. Moves the built-in session-search shortcut to ⌥⌘F. The automatic panel shares [coach](plugins/coach/)'s rate limiter. |
| [topic-shift](plugins/topic-shift/) | [yes](desktop-plugins/topic-shift/) | After you send a message (from a session's second message on; mid-turn sends are checked when the turn ends), detects a new task and offers *New session* (with a carried-over brief and an @session link), *Fork* or *Send here*. Set a model with `auxiliary.topic_shift.provider`/`.model` (a Sonnet-class model works well; it defaults to your main model). Shares [coach](plugins/coach/)'s rate limiter. |

## Desktop-only plugins

| Plugin | What it does |
|---|---|
| [screen-share](desktop-plugins/screen-share/) | Inline `::screenshare` button opens a validated VNC address on the local Desktop computer, never the remote gateway; requires the strict VNC core capability. |
| [hide-date-dividers](desktop-plugins/hide-date-dividers/) | Removes the "Earlier today / Yesterday / Last week" separators from the session sidebar. |
| [screenshot-mention](desktop-plugins/screenshot-mention/) | Type `@ss` in the composer to attach your latest screenshot (`@ss3` = latest 3) as an image chip, no dragging. When you send, the originals go to the Trash; Hermes keeps its own copy. |
| [session-transfer](desktop-plugins/session-transfer/) | Copy or move sessions between gateways (this device ⇄ Nous-hosted or remote, or any two connections). |
| [task-mentions](desktop-plugins/task-mentions/) | Kanban task ids (`t_1a2b3c4d`) in any chat become live: hover for a preview card (title, status, assignee, age, latest summary), click for a popup with description, summary, parents/children, runs, comments and events, plus buttons to open the card on the board (its drawer, to edit it or comment; needs fork core's kanban open-task hook, else it opens the board), the worker session or the board. Looks up every board on every connected gateway (a `@task:<board>@<gateway>/<id>` ref goes straight to that gateway); leaves the chat DOM untouched (CSS Highlight API). |
| [session-tasks](desktop-plugins/session-tasks/) | A right-aligned strip of chips just above the composer input, one per thing the focused session is running: background processes, delegate_task subagents, and the Kanban tasks it created or shepherds, on every board (todo/ready/running/scheduled/blocked/review, matched across the session's compression lineage) — the same cards the sidebar row's kanban counts cover, via the fork's `/api/session-kanban-tasks` (falls back to scanning the current board on older backends). Also the rented machines bound to the session (or an ancestor in its lineage) in the compute-rentals registry, with status and hourly rate; clicking one opens it in the Compute Rentals popover. On the fork's Desktop it also registers an archive guard (`ctx.registerArchiveGuard`), so archiving a chat that still holds rented machines asks first. Pulsing dot + elapsed time; hover for the full command/goal; click a Kanban chip for the Task Mentions popup. Hidden when idle; polls every 5 s while visible. Tests: `node --test desktop-plugins/session-tasks/test/projections.test.mjs`. |
| [blocked-panel](desktop-plugins/blocked-panel/) | When the focused chat is blocked, an amber panel above the composer says what you must do to unblock it. For each of its Kanban cards (created or shepherded, any board, across its compression lineage) sitting in `blocked` and waiting on a human, it shows the instructions the worker left: the latest block reason, split into numbered steps, or the card description when none was recorded. It also gives Open card, Done → unblock and Reply & unblock buttons (the reply is posted as a card comment, then the card is unblocked). It skips `transient` blocks and "no human action needed" time waits. A chat parked with ⌘B (fork core) shows a one-line "parked by you" notice. Pairs with core patch 0130, which makes workers write reasons as steps. Tests: `node --test desktop-plugins/blocked-panel/test/helpers.test.mjs`. |

## Skills

| Skill | Use when |
|---|---|
| [creative/animated-explainer](skills/creative/animated-explainer/) | Illustrated, animated, narrated explainers ("make an explainer about X for a 6-year-old, in Russian"): one self-contained responsive HTML with Gemini TTS, karaoke captions and chapter sync. Template + `tts.py` + `build.py` + a full example. |
| [apple/invisible-chrome-agent](skills/apple/invisible-chrome-agent/) | A real Chrome on an invisible virtual display, kept there by a launchd CDP supervisor; Touch ID notifications for passkeys. |
| [apple/local-chrome-remote-debugging](skills/apple/local-chrome-remote-debugging/) | Driving Chrome with remote debugging on macOS. |
| [apple/macos-disk-space-cleanup](skills/apple/macos-disk-space-cleanup/) | A Mac is low on disk: find, clean, ask first. |
| [apple/macos-dock-management](skills/apple/macos-dock-management/) | The Dock shows dead `?` icons or stale pins. |
| [autonomous-ai-agents/claude-usage-diagnostics](skills/autonomous-ai-agents/claude-usage-diagnostics/) | Claude usage runs out or sessions stick on 429s. |
| [devops/cloud-service-decommission](skills/devops/cloud-service-decommission/) | Shutting down a hosted service to stop billing. |
| [devops/github-ci-wait](skills/devops/github-ci-wait/) | Waiting for a pushed commit's GitHub CI or release: `wait_for_ci.py` waits on runs and tags that *contain* the commit, never the newest run (which a busy branch mostly cancels). |
| [devops/kanban-hands-digest](skills/devops/kanban-hands-digest/) | "What do you need from me?": every Kanban card blocked on a human, grouped by device, shared steps deduped, deadlines first, as one checklist; config-driven auto-checks unblock cards whose completion a machine can verify (`hands_digest.py`, cron recipe inside). |
| [hermes-disk-plugin-backends](skills/hermes-disk-plugin-backends/) | Adding Hermes disk-plugin REST backends. |
| [productivity/hermes-coach](skills/productivity/hermes-coach/) | Mine every host's Hermes sessions into redacted per-session digests in a private git repo, compute usage KPIs, and queue the most wasteful sessions for a coach Bot to label (rubric in `references/labeling.md`). `coach_mine.py --write-adoption` checks the coach plugin's curriculum against the digests (`reports/adoption.json`). `coach_eval.py` gates a change before rollout: detector variants against the labeled reply-check/fix-loop sets, and instruction-change grading over a frozen benchmark (paid CLI replay currently disabled pending enforceable cost bounds; incomplete/smoke reports cannot pass); `coach_trial.py` turns the week's top outside idea into a gated trial card. `coach_weekly.py` adds six-week source/handle/feed yield, mature-source adjustment proposals, and daily lane spend against budgets; unknown coverage stays explicit. |
| [productivity/ambitious-requests](skills/productivity/ambitious-requests/) | Make a request more ambitious: the Altitude / Depth / Range lenses (goes with the prompt-coach plugin). |
| [productivity/quiet-mode](skills/productivity/quiet-mode/) | You want Hermes off the screen and speakers (goes with the quiet-mode plugin). |
| [social-media/messaging-bridges](skills/social-media/messaging-bridges/) | WhatsApp/Signal bridges: QR-pair once, the session persists. |
| [social-media/slack-workspace-bot](skills/social-media/slack-workspace-bot/) | Acting as a team Slack bot: use observed context, join channels, call the Slack API, handle approvals (goes with the slack-workspace plugin). |
| [software-development/repo-security-audit](skills/software-development/repo-security-audit/) | Auditing a repo for security flaws and fixing them. |
| [software-development/skill-audit](skills/software-development/skill-audit/) | Auditing skills: size cap, notes lost to failed `skill_manage` writes, orphan references, overlaps, rules without evidence behind them; daily guard + weekly refactor cron recipe. |

## Contributing / my own workflow

See [AGENTS.md](AGENTS.md). Every commit is scanned for secrets (`.githooks/pre-commit`, plus
gitleaks in CI).
