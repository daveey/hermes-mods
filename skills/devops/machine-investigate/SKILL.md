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
at click time. Script: `scripts/collect.py` in this skill (stdlib only). From a chat on another host, pipe it in:
`ssh <host> 'python3 - report --no-du' < <skill>/scripts/collect.py`.
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
   (MacBook, hub, pool workers, mini) naming its label, script or directory. Work a card started over ssh from
   another host carries neither marker (`owner` empty): read the chain root's cwd (task scratch dirs are named
   `t_<hex>-…`) and `SSH_CONNECTION` in `/proc/<root pid>/environ`, and name the source with `tailscale status`. A
   `chain` of dozens of identical python parents is a test re-spawning itself until its outer timeout: attribute the
   root (the chain's last entry), not the leaves. Read the owner card with
   `kanban_show`, not a shell board command. Follow the chain to the launchd job (`launchctl list`,
   `launchctl print gui/$UID/<label>` for its interval), systemd unit, or cron (`crontab -l`,
   `~/.hermes/cron/jobs.json`). Any ask to stop an owned service goes on its owner card, with measured cost.
   A loop spawning `gh`/`git`/`python` every few seconds may be a polling interval problem: find its job first.
5. For Linux package-store pressure, inventory versioned stores before choosing native GC: an installed pnpm 9 prunes v3 but leaves v10. Use an already-cached matching native binary for each supported store, with Corepack downloads and auto-pinning disabled; never force a different major onto it or directly delete store files. Verify with an isolated native-prune fixture preserving a project hardlink and foreign-version data, then compare allocated bytes live. Native file/package counts are not reclaimed disk bytes, and a successful prune does not prove the pressure threshold cleared.
6. Disk: for a macOS disk question also follow `mac-disk-cleanup` (its repo-deletion rules and known big
   hitters); for a Linux Hermes host (the hub, or a "Low disk /autoprune" toast) read
   `references/hermes-disk-pressure.md`. Deletions still go through the ask below.
7. Write the diagnosis with the `five-whys` skill: every why cites evidence from the report; stop at a
   process/guard/design gap.

## Live gateway stacks and restarts

Never signal a live Hermes gateway, its launcher/wrapper, or a Desktop serve/dashboard backend,
including SIGUSR2, SIGQUIT, SIGABRT and SIGKILL. Only signal 0 is a safe existence probe.
For a stack sample use `sample <pid> 5` on macOS or
`py-spy dump --nonblocking --pid <pid>` on Linux, under `sudo -n "$(command -v py-spy)"` for a process that isn't your descendant (Yama
ptrace_scope refuses it otherwise; sudo's PATH omits `~/.local/bin`, so a bare `sudo -n py-spy` exits 127). Redirect the dump to an absolute scratch path: a relative redirect after a persisted `cd` into the core checkout is refused. Do not substitute a signal if sampling is unavailable.
A `py-spy dump` is one instant: enough to see what a hung process waits on, but a single dump can name a function
that isn't the hot path. On Linux, dump headers may omit Python thread names: join each numeric TID to `/proc/<pid>/task/<tid>/comm` before filtering named threads, tolerate threads exiting during that read, and assert nonzero selected-thread coverage. Do not equate voluntary context switches with loop iterations: repeated stacks blocked on an Event, Condition or strict-reader lock require separate wake/pass evidence before a timer-loop patch. To attribute CPU to code, record instead:
`sudo -n py-spy record --nonblocking --pid <pid> --duration 15 --rate 40 --format raw -o <f>` (each line is
`frame;frame;… count`), then rank functions by their inclusive share of samples (count each function once per stack).
To measure time a thread spends blocked on a lock or event, add `--idle`: without it py-spy drops sleeping threads' samples, so lock waits never show up. Never combine `--native` with `--nonblocking`: py-spy rejects that combination before sampling. If production may not be paused, retain nonblocking mode and mark native attribution unknown; do not drop nonblocking to obtain native frames. Under a bounded capture budget preserve failed attempts and correct options/privilege inside the original elapsed budget, never reset its admission time.
For fact-feature canary failures, inspect the backend's redirected application log as well as its systemd journal: a clean or empty journal can omit ASGI exceptions. A sessions feature disabled at startup can still depend on separately enabled annotation or remote-links facts. Trace AnnotationUnavailable and LinksUnavailable to their demand/projection boundary before attributing HTTP 500 to CPU contention; preserve capacity exceptions separately from capture-budget errors. Removing a systemd drop-in changes future launches only, so verify both the serving successor's process environment and the retired canary's identity before claiming rollback. Whole-process CPU above a capture threshold proves only that the conservative upper-bound test did not pass, not that capture CPU itself exceeded it.

For a bounded sync-loop profile, filter raw stacks to the exact sync thread before ranking: detached link-repair and watcher threads can share module names and otherwise dominate the wrong envelope. Preserve sampler error/lag counts and treat an offline one-pair query speedup as a candidate estimate, not a validated end-to-end forecast. Do not infer historical row-volume causes from aggregate counters without row identities.
Use the profile to locate code, not to convert sample counts directly into CPU utilization: thread filtering,
missed samples and nonblocking reads affect that estimate. Cross-check against the step 3 CPU-time delta.
`<f>` may be owned by root after `sudo`; inspect ownership and use `sudo -n` to read/delete it if needed.
For a gateway restart use
`python3 ~/.hermes/sync-runner/hermes-sync pause-turns --timeout 180 -- hermes gateway restart`.
The backend-restart-guard enforces recognized literal signal commands on both Hermes and Claude Bash;
its fail-open discovery and lexical limits are not permission to bypass it.

## Cache-retention native verification

Exercise the production holder-census implementation in a disposable native build/delete/rebuild fixture, not only an injected empty holder set. A collector's own open directory fd can otherwise make every candidate look busy. If closing that fd for the census, reopen with no-follow and verify the original directory identity before fd-relative deletion. Keep native regeneration proof, live dry-run eligibility and actual production reclamation as distinct evidence.

## Historical Linux OOM attribution

Separate every incident carried on an OOM card: one open watcher card can accumulate deliberate private-cgroup tests and unrelated global kills. Read both `oom_memcg` and `task_memcg`; a disappeared transient unit's `MemoryMax=infinity` is not its historical budget. Recover rotated journal evidence from retained `/var/log/kern.log` and `/var/log/syslog`, including the kernel's usage/limit lines and systemd's launched command. Attribute SSH-session victims through session start time, sshd source address and the source host's agent transcript; the hub's own transcript database cannot explain work launched remotely.

For benchmark contention spinners, never use an infinite list comprehension to burn CPU: it retains an unbounded list. A scalar loop preserves contention without accumulated objects. Verify the actual target AST in an isolated subprocess with a low `RLIMIT_AS`, so the unsafe control raises `MemoryError` instead of inducing another kernel OOM; compare it with the corrected target and live benchmark RSS/progress. Do not import or rerun an entire live-data harness merely to exercise its spinner, and do not signal an active benchmark or restart unrelated services.

## Heap attribution coverage

For loop-watch owner censuses, check both deadline and owner-label coverage before choosing a retainer to fix. `truncated: false` alone does not mean attribution is complete: a saturated owner table can hide the largest structures in `<other owners>`. Require `labels_truncated: false` on newer reports; on older reports inspect the owner count against the implementation's cap. Keep Python shallow-object bytes distinct from physical footprint (native buffers and allocator slack are excluded), and do not rerun a consumed one-shot by clearing its lifetime sentinel. Use natural process replacement for the next live scan.

On macOS, inspect the allocator before predicting what a cut saves: `python3 <skill>/scripts/alloc_split.py <pid>` reads `vmmap --summary` (which can suspend the target; use only an authorized diagnostic process). Physical footprint can include memory retained by malloc after objects are freed. The helper reports malloc in-use bytes and a free-memory estimate from zone fragmentation plus dirty and swapped pages of empty malloc regions; this is not an exact additive decomposition of footprint. The Untagged row may include pymalloc arenas but is not uniquely attributable to Python. Never book a footprint difference between runs as retained objects, or treat a zero from `malloc_zone_pressure_relief` as proof that no freed memory remains. Compare the same workload and object census alongside allocator statistics; an A/B with `MallocSpaceEfficient=1` set at process start can test allocator effects, not prove their exclusivity. `heap <pid>` can provide a native malloc size histogram beyond a Python-object census. The helper fails on command errors or missing required tables rather than reporting false zeros; `scripts/test_alloc_split.py` exercises parsing, units, PID validation and error handling.

## Remote backend timeouts: separate compute from transport

When the host is idle but a Python backend is slow, follow `references/python-backend-latency.md`. It covers
thread-queue vs handler time, the GIL-convoy test (per-thread context switches, strace futex share) and
attribution of hot threads.

First read the Desktop's own gate lines for the conn in `desktop.log`: `[remote-down] conn:<c>::... marked down (<reason>)` to `... answered again; gate open` bounds an outage, and while it lasts every poll logs a fail-fast line that quotes one timeout ("is down (Timed out ..., 27s ago)"), so the timeout count is not a request count. Reason `read ECONNRESET` means the backend process went away (check the backend host's `agent.log` for a core update or restart at that second); that is neither CPU nor link. `backend_timeouts.py --dry-run` lists these as `backend_down.episodes`.

Compare authenticated request timings on the backend host with the same requests through the client's actual SSH forward before attributing timeouts to CPU. Probe independent direct and jump-host routes with a bounded payload transfer, then isolate each hop; a successful TCP port check proves reachability, not end-to-end delivery. Corroborate a stalled route with socket retransmission/queue evidence, and leave the physical loss point unknown unless measured.

Do not pin a jump-host workaround solely because its SSH port answers. If that pin later stalls while the direct route succeeds, remove the harmful override and reconnect only the affected transport masters, not healthy backend processes. Read back effective SSH configuration, the actual master's remote socket, and authenticated application responses.

When the task requires a full post-change timeout window, measure it while the client remains active; if the user disallows a soak, report only the observed interval and do not claim the full window passed. Preserve the detector's raw count and timestamps, not only its threshold verdict. A zero-timeout window proves recovery during that window, not that intermittent network loss cannot recur. Keep that distinction in the completion report and retain existing recurrence detection.

For an explicitly required post-deploy storage observation, bind the boundary to the exact active producer helper bytes, not just repository HEAD or clean gateway logs. Reconcile later source drift against the independently reviewed bytes, rerun only the bounded storage regressions, and replace a stale activation timestamp rather than counting its earlier window. A combined attention probe should wake early on helper drift, ENOSPC, missing coverage or a floor breach as well as at the required end; exit 0 means attention, not acceptance. Preserve prior receipts and do not automatically reset a dirty window.

For an autoscaled fleet storage window, distinguish EC2 launch/SSM Online from the first gateway process and emission. Preserve pre-gateway transport/log gaps as bootstrap-unknown, not zero-error coverage. If disposition is justified by startup evidence, bind it to the exact instance, boot ID and fixed first-log boundary; never move that boundary after rotation or apply it to another same-named replacement. Re-evaluate original raw history so real ENOSPC, source drift, floor breaches and later transport failures remain latched. Test both collecting and repeated attention exits through the actual probe entrypoint, without restarting the fleet window.

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
