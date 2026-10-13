# hermes-mods

Plugins, Desktop plugins and skills I've written for
[Hermes Agent](https://github.com/NousResearch/hermes-agent). I run all of them every day on macOS
(Hermes CLI, gateway and Desktop app). MIT-licensed, so take whatever is useful.

Several plugins are workarounds for open upstream issues. Each one says so in its `plugin.yaml` and
can be deleted once upstream ships the fix.

## Install

```bash
git clone https://github.com/daveey/hermes-mods ~/code/hermes-mods
M=~/code/hermes-mods

# agent plugin (backend + optional dashboard API)
ln -s $M/plugins/topic-shift ~/.hermes/plugins/topic-shift && hermes plugins enable topic-shift
# its Desktop half, if it has one (hot-reloaded by the running app)
ln -s $M/desktop-plugins/topic-shift ~/.hermes/desktop-plugins/topic-shift

# all skills, read in place (git pull updates them)
hermes config set skills.external_dirs '["~/code/hermes-mods/skills"]'
```

Copy instead of symlink (`cp -R`) if you'd rather freeze a version. Restart the gateway or Desktop
backend after enabling a backend plugin.

## Plugins

| Plugin | Desktop half | What it does |
|---|---|---|
| [account-usage](plugins/account-usage/) | [yes](desktop-plugins/account-usage/) | Status-bar chips for pooled Claude / ChatGPT accounts. Records which pooled credential served the latest call and highlights it. |
| [anthropic-pool-status](plugins/anthropic-pool-status/) | — | Dashboard workaround: the Desktop Accounts card counts Anthropic OAuth logins in the credential pool. |
| [fast-rate-limit-fallback](plugins/fast-rate-limit-fallback/) | — | On a long-reset rate limit, rotate pooled credentials first, then fall back to the next provider without sleeping through retries. |
| [mcp-file-access](plugins/mcp-file-access/) | — | Ask (Once / Session / Always) before the Chrome DevTools MCP server reads or writes files outside its allowed folders. On approval, the file is routed through its temp folder, so no restart is needed. |
| [oauth-keepalive](plugins/oauth-keepalive/) | — | Refreshes pooled OAuth tokens (Claude, ChatGPT/Codex) before they expire, including idle accounts. |
| [quiet-mode](plugins/quiet-mode/) | — | `/quiet` (`/movie`) blocks every Hermes session on the Mac from opening windows, driving the GUI or browser, changing lights or TVs, or making sound. |
| [session-mentions](plugins/session-mentions/) | [yes](desktop-plugins/session-mentions/) | `cross_session` tool: list, read and message sessions on any gateway connected to the Desktop app (local, Nous-hosted, remote). |
| [spin-out](plugins/spin-out/) | [yes](desktop-plugins/spin-out/) | ⇧⌘F on a session: finds the follow-up work at its end and offers it as choices: click a task to launch it as a new session, or [All] for the rest. Each child gets with a self-contained brief and an @session pointer to the parent. Moves the built-in session-search shortcut to ⌥⌘F. |
| [topic-shift](plugins/topic-shift/) | [yes](desktop-plugins/topic-shift/) | After you send a message, detects a topic change and offers *New session* (with a carried-over brief and an @session link), *Fork* or *Send here*. |

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
| [productivity/quiet-mode](skills/productivity/quiet-mode/) | You want Hermes off the screen and speakers (goes with the quiet-mode plugin). |
| [social-media/messaging-bridges](skills/social-media/messaging-bridges/) | WhatsApp/Signal bridges: QR-pair once, the session persists. |
| [software-development/repo-security-audit](skills/software-development/repo-security-audit/) | Auditing a repo for security flaws and fixing them. |

## Contributing / my own workflow

See [AGENTS.md](AGENTS.md). Every commit is scanned for secrets (`.githooks/pre-commit`, plus
gitleaks in CI).
