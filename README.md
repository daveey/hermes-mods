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
| [compute-rentals](plugins/compute-rentals/) | [yes](desktop-plugins/compute-rentals/) | Read-only inventory of rented compute (Vast, RunPod, Modal, Prime Intellect, SF Compute, EC2, Slurm over ssh) in one panel, plus which session started each box. Private clusters plug in as extra provider files kept outside the repo. |
| [fast-rate-limit-fallback](plugins/fast-rate-limit-fallback/) | — | On a long-reset rate limit, rotate pooled credentials first, then fall back to the next provider without sleeping through retries. |
| [mcp-file-access](plugins/mcp-file-access/) | — | Ask (Once / Session / Always) before the Chrome DevTools MCP server reads or writes files outside its allowed folders. On approval, the file is routed through its temp folder, so no restart is needed. |
| [oauth-keepalive](plugins/oauth-keepalive/) | — | Refreshes pooled OAuth tokens (Claude, ChatGPT/Codex) before they expire, including idle accounts. |
| [prompt-coach](plugins/prompt-coach/) | [yes](desktop-plugins/prompt-coach/) | After you send a task request, offers a more ambitious rewrite (Altitude / Depth / Range, after Felix Rieseberg) in a panel above the composer: *Run in new session* (the original keeps running), *Replace* or *Keep mine*. A cheap model screens; a stronger one rewrites. Logs choices for a weekly digest. |
| [crontab-guard](plugins/crontab-guard/) | — | Blocks sessions from rewriting the user crontab wholesale (`crontab FILE`, `... \| crontab -`, `-r`, `-e`). Edits go through `crontab_line.py` (locked, one line at a time, backs up first). Includes a Claude Code PreToolUse hook (`claude_hook.py`). |
| [quiet-mode](plugins/quiet-mode/) | — | `/quiet` (`/movie`) blocks every Hermes session on the Mac from opening windows, driving the GUI or browser, changing lights or TVs, or making sound. |
| [slack-workspace](plugins/slack-workspace/) | — | For a team Slack bot: records traffic in every channel it's in and adds that channel's recent activity as context when it's @-mentioned. Tools to see recent activity, join channels in bulk and call any Slack API method. Joins, posts outside the current conversation and admin methods go to the bot's admins as one DM with approve-selected / always-allow / deny buttons. |
| [session-mentions](plugins/session-mentions/) | [yes](desktop-plugins/session-mentions/) | `cross_session` tool: list, read and message sessions on any gateway connected to the Desktop app (local, Nous-hosted, remote). |
| [spin-out](plugins/spin-out/) | [yes](desktop-plugins/spin-out/) | Finds the follow-up work at the end of a session and offers it above the composer: click a task to launch it as a new session, [All] for the rest, or [Kanban] to put them on the active board as cards. Shows up on its own when a turn finishes and its last reply mentions follow-up work (a cheap cue filter runs first; palette toggle to turn it off), and on demand with ⇧⌘F. Each child gets a self-contained brief and an @session pointer to the parent. Moves the built-in session-search shortcut to ⌥⌘F. |
| [topic-shift](plugins/topic-shift/) | [yes](desktop-plugins/topic-shift/) | After you send a message (from a session's second message on; mid-turn sends are checked when the turn ends), detects a new task and offers *New session* (with a carried-over brief and an @session link), *Fork* or *Send here*. Set a model with `auxiliary.topic_shift.provider`/`.model` (a Sonnet-class model works well; it defaults to your main model). |

## Desktop-only plugins

| Plugin | What it does |
|---|---|
| [hide-date-dividers](desktop-plugins/hide-date-dividers/) | Removes the "Earlier today / Yesterday / Last week" separators from the session sidebar. |
| [session-transfer](desktop-plugins/session-transfer/) | Copy or move sessions between gateways (this device ⇄ Nous-hosted or remote, or any two connections). |

## Skills

| Skill | Use when |
|---|---|
| [apple/local-chrome-remote-debugging](skills/apple/local-chrome-remote-debugging/) | Driving Chrome with remote debugging on macOS. |
| [apple/macos-disk-space-cleanup](skills/apple/macos-disk-space-cleanup/) | A Mac is low on disk: find, clean, ask first. |
| [apple/macos-dock-management](skills/apple/macos-dock-management/) | The Dock shows dead `?` icons or stale pins. |
| [autonomous-ai-agents/claude-usage-diagnostics](skills/autonomous-ai-agents/claude-usage-diagnostics/) | Claude usage runs out or sessions stick on 429s. |
| [devops/cloud-service-decommission](skills/devops/cloud-service-decommission/) | Shutting down a hosted service to stop billing. |
| [hermes-disk-plugin-backends](skills/hermes-disk-plugin-backends/) | Adding Hermes disk-plugin REST backends. |
| [productivity/hermes-coach](skills/productivity/hermes-coach/) | Mine every host's Hermes sessions into redacted per-session digests in a private git repo, compute usage KPIs, and queue the most wasteful sessions for a coach Bot to label (rubric in `references/labeling.md`). |
| [productivity/ambitious-requests](skills/productivity/ambitious-requests/) | Make a request more ambitious: the Altitude / Depth / Range lenses (goes with the prompt-coach plugin). |
| [productivity/quiet-mode](skills/productivity/quiet-mode/) | You want Hermes off the screen and speakers (goes with the quiet-mode plugin). |
| [social-media/messaging-bridges](skills/social-media/messaging-bridges/) | WhatsApp/Signal bridges: QR-pair once, the session persists. |
| [social-media/slack-workspace-bot](skills/social-media/slack-workspace-bot/) | Acting as a team Slack bot: use observed context, join channels, call the Slack API, handle approvals (goes with the slack-workspace plugin). |
| [software-development/repo-security-audit](skills/software-development/repo-security-audit/) | Auditing a repo for security flaws and fixing them. |

## Contributing / my own workflow

See [AGENTS.md](AGENTS.md). Every commit is scanned for secrets (`.githooks/pre-commit`, plus
gitleaks in CI).
