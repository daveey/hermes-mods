---
name: machine-investigate
description: Use when a machine in the fleet is saturated (high CPU load, RAM, low disk) and you must find out what is doing it, fix what is safe, and ask about the rest. Started by the Runners panel's Investigate button; also usable by hand.
version: 1.0.0
author: daveey
license: MIT
metadata:
  hermes:
    tags: [devops, load, disk, macos, linux, runners]
    related_skills: [five-whys, mac-disk-cleanup]
    requires_toolsets: [terminal, file, kanban]
---

# Machine investigate

You run ON the machine being investigated (the card was placed there). The card body has the panel's numbers
at click time. Script: `scripts/collect.py` in this skill (stdlib only).

## Procedure

1. `python3 <skill>/scripts/collect.py install-recorder --apply` (macOS: makes sure the load recorder runs here,
   so the next spike is attributed; no-op elsewhere or when already loaded).
2. `python3 <skill>/scripts/collect.py report > /tmp/investigate-$(date +%s).json` and read it. The home `du`
   can take a few minutes; use `--no-du` only for a CPU-only question. It has:
   - `load`, `cpus`, `cpu_pct` (= load1 / cores, what the panel shows), `memory`
   - `processes.top`: top processes with parent `chain`, top-level `root` app and `owner`
     (`card:t_…`, `claude:…`, `hermes-session:…`, `codex:…`, `browser-use:…`); `by_app` CPU per top-level app;
     `young_by_name`: processes under 10 s old (a cron/sync/poll loop shows up here as many short-lived
     python/gh/git processes, not in the top list)
   - `disk`: volume free, APFS local snapshots, `cleanable` caches with sizes, `largest_in_home`, other users' homes
   - `recorder`: load percentiles and per-agent CPU during recorded spikes (last 24 h)
3. Sample twice more 30 s apart (`report --no-du`) before blaming a process: one ps snapshot is noise.
4. For each real consumer, follow the chain to whoever started it: a card (`hermes kanban show t_…`), a launchd
   job (`launchctl list | grep`, `launchctl print gui/$UID/<label>` for its interval), a systemd unit, a cron
   (`crontab -l`, `~/.hermes/cron/jobs.json`). A loop that spawns `gh`/`git`/`python` every few seconds is a
   polling interval problem: find the job and its interval.
5. Disk: for a macOS disk question also follow `mac-disk-cleanup` (its repo-deletion rules and known big
   hitters); its deletions still go through the ask below.
6. Write the diagnosis with the `five-whys` skill: every why cites evidence from the report; stop at a
   process/guard/design gap.

## What you may fix without asking

- `collect.py clean --apply` (can take several minutes: run it in the background and wait for it, not in a
  foreground call with a short timeout): package/build caches only (uv, pip, npm/_npx, go build, cargo registry cache,
  Homebrew, Xcode DerivedData, pnpm store prune, unavailable simulators). Report before/after free space.
- `collect.py kill PID --apply`: refuses unless the process belongs to a Kanban card that is done, archived or
  blocked, and it never kills a GUI app or a Hermes gateway/serve. Use it for those leftovers only.

## Ask first (blocked card, exact command, size or owner, what yes/no mean)

Everything else: worktrees, repos, app data, logs, model/browser downloads, APFS snapshots, other users'
files, launchd/systemd/cron changes (including a polling interval), untagged or still-owned processes, app
restarts. Never delete the only copy of anything. Group the asks into ONE blocked card on this card's board
(`kanban_block` on yourself with the list) rather than many.

## Finish

`kanban_complete` with:
- summary: first line `<machine>: <verdict in ≤12 words>` (the panel shows this line), then before → after
  numbers (load, RAM, free disk), what you fixed, what needs the user.
- metadata: `{"machine": "...", "before": {...}, "after": {...}, "fixed": [...], "asks": [...], "five_whys": [...]}`.

If something needs the user, block instead of completing, with the asks; the panel shows "Needs you".
