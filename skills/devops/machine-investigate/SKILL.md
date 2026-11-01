---
name: machine-investigate
description: Use when a host is saturated or a job hangs. Find and fix.
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
A hung job (results stopped, almost no CPU) doesn't show in load numbers: follow `references/stuck-jobs.md`.

## Procedure

1. `python3 <skill>/scripts/collect.py install-recorder --apply` (macOS: makes sure the load recorder runs here,
   so the next spike is attributed; no-op elsewhere or when already loaded).
2. `out="${TMPDIR:-$HOME/.hermes/cache/scratch}/investigate-$(date +%s).json"; mkdir -p "$(dirname "$out")" &&
   python3 <skill>/scripts/collect.py report > "$out"` and read it. The home `du`
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

## Live gateway stacks and restarts

Never signal a live Hermes gateway, its launcher/wrapper, or a Desktop serve/dashboard backend,
including SIGUSR2, SIGQUIT, SIGABRT and SIGKILL. Only signal 0 is a safe existence probe.
For a stack sample use `sample <pid> 5` on macOS or
`py-spy dump --nonblocking --pid <pid>` on Linux, under `sudo -n` for a process that isn't your descendant (Yama
ptrace_scope refuses it otherwise). Do not substitute a signal if sampling is unavailable.
For a gateway restart use
`python3 ~/.hermes/sync-runner/hermes-sync pause-turns --timeout 180 -- hermes gateway restart`.
The backend-restart-guard enforces recognized literal signal commands on both Hermes and Claude Bash;
its fail-open discovery and lexical limits are not permission to bypass it.

## Remote backend timeouts: separate compute from transport

Compare authenticated request timings on the backend host with the same requests through the client's actual SSH forward before attributing timeouts to CPU. Probe independent direct and jump-host routes with a bounded payload transfer, then isolate each hop; a successful TCP port check proves reachability, not end-to-end delivery. Corroborate a stalled route with socket retransmission/queue evidence, and leave the physical loss point unknown unless measured.

Do not pin a jump-host workaround solely because its SSH port answers. If that pin later stalls while the direct route succeeds, remove the harmful override and reconnect only the affected transport masters, not healthy backend processes. Read back effective SSH configuration, the actual master's remote socket, and authenticated application responses.

When the task requires a full post-change timeout window, measure it while the client remains active; if the user disallows a soak, report only the observed interval and do not claim the full window passed. Preserve the detector's raw count and timestamps, not only its threshold verdict. A zero-timeout window proves recovery during that window, not that intermittent network loss cannot recur. Keep that distinction in the completion report and retain existing recurrence detection.

For an existing adaptive direct/jump SSH selector, use bounded end-to-end payload admission when static direct/jump overrides alternate between healthy and stalled; read `references/ssh-transport-recovery.md` before changing or testing that selector (racing, cancellation, cache, ProxyCommand signal cleanup, transport timeouts). Whatever the route mechanism, verify concurrent large application responses through the client's actual forward, not only small health checks, and require successful application reads as well as a zero detector count: fail-closed route admission can suppress timeout logs while still disconnected.

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
