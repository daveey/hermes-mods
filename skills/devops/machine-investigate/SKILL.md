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
     `young_by_name`: processes under 10 s old, grouped as objects with `name`, `count`, `owner`,
     `owner_source` and `service` (a cron/sync/poll loop shows up here as many short-lived processes).
     `top` also includes `service` and `owner_source`; `owned_services` includes quiet owned service processes
     below the top-CPU threshold. Ownership comes from `HERMES_OWNER_CARD=t_<hex>[@host]` or the first line
     `card: t_<hex>[@host]` of `OWNER.md` in a process's cwd or an ancestor process's cwd
   - `runnable` (macOS): R-state threads by priority and top priority-4 PIDs with owner/chain; load average includes the E-core background queue even when P-cores are idle. Collected via libproc (own processes) + setuid `ps -M -p` (others) in ~0.1 s; never run the probe at Background QoS (launchd ProcessType=Background / `taskpolicy -b`), or it waits in the E-core queue it counts.
   - `disk`: volume free, APFS local snapshots, `cleanable` caches with sizes, `largest_in_home`, other users' homes
   - `recorder`: load percentiles and per-agent CPU during recorded spikes (last 24 h)
3. Sample twice more 30 s apart (`report --no-du`), and collect cumulative CPU time alongside each report
   (`ps -Ao pid=,time=,args=`). Attribute CPU with CPU-time deltas over at least 60 s: CPU seconds gained /
   measured elapsed wall seconds × 100 is percent of one core. Never infer cost or improvement from two
   `ps %cpu` snapshots. Re-measure the same PIDs (check identity/start time) the same way after a fix.
4. A long-lived process is not a leftover just because it is old. Find its owner first: `OWNER.md` in its cwd
   or an ancestor process's cwd, `HERMES_OWNER_CARD` in its env, and open cards on every board and gateway
   (MacBook, hub, mini in a three-host fleet) naming its label, script or directory. Read the owner card with
   `kanban_show`, not a shell board command. Follow the chain to the launchd job (`launchctl list`,
   `launchctl print gui/$UID/<label>` for its interval), systemd unit, or cron (`crontab -l`,
   `~/.hermes/cron/jobs.json`). Any ask to stop an owned service goes on its owner card, with measured cost.
   A loop spawning `gh`/`git`/`python` every few seconds may be a polling interval problem: find its job first.
5. Disk: for a macOS disk question also follow `mac-disk-cleanup` (its repo-deletion rules and known big
   hitters); its deletions still go through the ask below.
6. Write the diagnosis with the `five-whys` skill: every why cites evidence from the report; stop at a
   process/guard/design gap.

## What you may fix without asking

- `collect.py clean --apply` (can take several minutes: run it in the background and wait for it, not in a
  foreground call with a short timeout): package/build caches only (uv, pip, npm/_npx, go build, cargo registry cache,
  Homebrew, Xcode DerivedData, pnpm store prune, unavailable simulators). Report before/after free space.
- `collect.py kill PID --apply`: refuses unless the process belongs to a Kanban card that is done, archived or
  blocked, and it never kills a GUI app, Hermes gateway/serve, or launchd/systemd-managed service (even if
  the card is done). Unknown card status, including a card on another host, counts as owned, not abandoned.
  Explicit owner markers work outside Hermes process trees. Use it for non-service leftovers only.

## Ask first (blocked card, exact command, size or owner, what yes/no mean)

A user's general go-ahead to fix the machine is not approval to stop or disable an owned service.
Do not bypass a refused `collect.py kill` with raw kill, launchctl disable/bootout, or systemctl stop.
Put an owned-service ask on its owner card with the measured cost and exact action, rather than a new duplicate ask.

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
