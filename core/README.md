# Core mods: changes to Hermes's own source

These are the changes I carry on my fork of
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). They can't be plugins
(or weren't, yet), so they ship here two ways:

- **`patches/`**: `git format-patch` output of every commit, numbered in apply order (`git am`-ready).
- **One doc per feature**: what it does, how it works, the code that matters, tests, how to apply it
  and the gotchas. It's written so your coding agent can re-implement the feature on a newer upstream
  when a patch no longer applies.

Keyboard shortcuts are summarized in [../shortcuts.md](../shortcuts.md).
The last public/private series reconciliation (what was already equivalent, what was added): [mirror-reconciliation.md](mirror-reconciliation.md).

## Features

| Doc | Patches | What you get |
|---|---|---|
| [ws-orphan-resume-ctx.md](ws-orphan-resume-ctx.md) | 0716 | Orphan-race resume tests build ctx from a real `session.resume` `_Resume` instead of a duck-typed double, so a new ctx attribute cannot break the fixtures. Test harness only. |
| [kanban-worker-task-marker.md](kanban-worker-task-marker.md) | 0705 | A Kanban worker's tool children (terminal, background processes, execute_code) get `HERMES_KANBAN_WORKER_TASK=<card>` so shell tooling knows its card; `HERMES_KANBAN_TASK` stays scrubbed, and a process with only the marker is not a worker. |
| [held-session-refusal-backoff.md](held-session-refusal-backoff.md) | 0702 | A completion or other automatic turn refused because another backend holds the chat backs off (30 s doubling to 300 s) and shows one "open in another Hermes window or device" notice per streak instead of an "internal error" card every poll; the event waits and is delivered once the chat is free. |
| [serve-update-wait.md](serve-update-wait.md) | 0697, 0714 | `hermes serve` waits out a live core update (600 s) or hermes-sync's admission hold (60 s) before loading the agent, so no backend boots on a tree about to be swapped. 0714 re-execs on a `clear` gate when the cached git identity is not HEAD; a ci/build stamp is not compared. |
| [boot-pinned-git.md](boot-pinned-git.md) | 0709 | Boot-pinned imports resolve `git` through `locate_command`; a missing git disables blob reads; an explicit path skips discovery. |
| [update-pause-in-place-handoff.md](update-pause-in-place-handoff.md) | 0704, 0712, 0717, 0719, 0725, 0728 | A core-update-paused turn whose checkout moved waits for a successor backend only while one is coming (retiring, or skew watchdog + idle proof); with no watchdog it continues at once, kept busy 60 s it continues in place (backstop 300 s, was 900 s). 0712 stops owned non-persist background jobs for that pause, journals the outcome before the next kill, and lists them on resume; a failed write or kill refuses the pause. 0717 (`91f48a91`) journals an idle chat's owned non-persist jobs when the backend exits (`backend_shutdown`) and the next backend reports them; a notify job's receipt outlives the turn freshness window (24 h). 0719 re-arms the inherited pause watcher after a synthesized notification turn succeeds, raises, or is refused at preparation. 0725 (2nd attempt, `26691e1c`): from the window end until the continuation starts, automatic turns (notices, completions, /loop, heartbeats) requeue instead of claiming the paused chat (bounded); hand-off bounds count from the move even during a carried turn; a `coming` outlook that never begins retiring continues here after 90 s. 0728 (`9b77743e`): a /goal continuation waits for the paused turn instead of overwriting its marker. |
| [background-review-retirement-hold.md](background-review-retirement-hold.md) | 0700, 0703, 0706 | A running post-turn background review holds the backend retirement fence: skew/idle retirement and `hermes update`'s stale-dashboard stop (0703) wait for it, ask it to stop between provider calls after 300 s, and stops counting it at 900 s; a retiring backend skips new reviews cleanly. 0706 also holds an installation-root cross-process admission reservation for the review's whole run, so an update hold that already counts reservations sees a review with no turn lease, and a new review is refused while that hold stands. |
| [kanban-delegate-child-terminal-isolation.md](kanban-delegate-child-terminal-isolation.md) | 0692 | Delegate children keep their own terminal env; `--board` works from worker shells; `boards list` counts each board. |
| [kanban-blocked-create-needs-input.md](kanban-blocked-create-needs-input.md) | 0693 | A card created blocked with a human_request is classified `needs_input`. |
| [kanban-create-overlap.md](kanban-create-overlap.md) | 0726, 0729 | `kanban_create`, `hermes kanban create` and REST `POST /tasks` refuse a card that open cards on any board or gateway (live boards + hermes-hub replica) already cover, by board/repo name or title overlap, naming them (`code: create_overlap`); `distinct_from` + `distinct_reason` overrides; refusals/overrides are `create_overlap` events counted by the coach's `create_overlap_watch`. |
| [pm-range-server-threaded.md](pm-range-server-threaded.md) | 0694 | Test range server is threaded so a paused download stops in 0.04 s, not 3.7 s. |
| [kanban-wake-check-confirm.md](kanban-wake-check-confirm.md) | 0691 | A wake probe exiting 0 is confirmed by a second run; the reaper stops stealing probe statuses. |
| [retirement-ledger-bom.md](retirement-ledger-bom.md) | 0688 0689 | Retirement ledger reads BOM JSON; a nested `open(..., "rb")` is not a text-mode footgun. |
| [projects-tree-activity-mock.md](projects-tree-activity-mock.md) | 0687 | Projects tree activity test mocks `_all_counts(include_snapshot=...)`. Test harness only. |
| [kanban-unlink.md](kanban-unlink.md) | 0685 | `kanban_unlink` removes an edge the worker owns (orchestrators any); a worker may complete the card it just parked; a parked card with a live run pid is not a leaked-worker env. |
| [transcript-page-request-arg.md](transcript-page-request-arg.md) | 0684 | Direct history-page tests pass `Request` and an explicit `order`. Test harness only. |
| [model-provider-guard.md](model-provider-guard.md) | 0682 | A bare vendor model id on OpenRouter or Nous is refused before any request; API worker logs carry the session tag. |
| [kanban-skill-pin-validation.md](kanban-skill-pin-validation.md) | 0681 | A skill pin the assignee cannot load is refused at create (`unknown_skill`); a stale pin does not kill or retry a dispatcher worker. |
| [test-run-summary-errors.md](test-run-summary-errors.md) | 0680 | The parallel test summary appends `, N errored` when pytest reported setup errors. |
| [kanban-replica-human-asks.md](kanban-replica-human-asks.md) | 0678–0679 | Replica human asks paint the chat red; unverified remote cards count as parked; a local+remote lineage counts once. |
| [ci-bwrap-e2e-upgrade.md](ci-bwrap-e2e-upgrade.md) | 0677 | `e2e-upgrade` runs on self-hosted hub slots labeled `ci-bwrap`. |
| [macos-venv-test-fixtures.md](macos-venv-test-fixtures.md) | 0701 | The source-driver PM-observer fixture links the base interpreter, and the packaged-launch test counts launches instead of every `subprocess.run`, so both pass under a copied-binary macOS venv. Test harness only. |
| [bootstrap-relaunch-test.md](bootstrap-relaunch-test.md) | 0676 | The real-bootstrap relaunch test imports its path fakes before the bootstrap preload. Test harness only. |
| [desktop-project-folder-create.md](desktop-project-folder-create.md) | 0675 | Move-to-project creates the session profile's folder when the gateway lacks the lane path. |
| [kanban-central-service.md](kanban-central-service.md) | 0673 0699 | Worker nodes (card runs): `runs_on` placement labels (`kanban create --runs-on`, `kanban_create(runs_on=...)`, `placement=<enrolled machine>` → `runs_on=[machine:<it>]`, and the create result says so); `kanban.sync.pickup` (`explicit` / `true`) copies eligible cards other gateways created from the replica and claims them through Board leases, which check the operator-set machine registry (labels, `pinned_only`, `hold`, `max_card_runs`); the creator's orphan/stale sweeps and concurrency counts skip cards running under another gateway's lease. |
| [kanban-local-lineage-counts.md](kanban-local-lineage-counts.md) | 0671 | Local cards shared across compression aliases count once, matching the task strip; parked, held and stalled subsets deduplicate with the same snapshot. Distinct boards/cards remain distinct. |
| [kanban-managed-pin-policy.md](kanban-managed-pin-policy.md) | 0670, 0711 | A model pin the managed routing registry cannot serve is refused at create and set-model (code `model_pin_not_in_routing_registry`), not at worker startup. 0711 clears a stored refused pin when no supervisor can take the assignment block, unless it is also a family mismatch, and dispatches that tick on the profile default. |
| [standing-instructions.md](standing-instructions.md) | 0669 | Standing instructions survive compaction word for word, reach workers ahead of the comment cap, and hold a don't-wait block once. |
| [bare-checkout-recover.md](bare-checkout-recover.md) | 0667–0668 | `hermes update` resets `core.bare` on a work tree that `git init --bare` flipped; a genuine bare repo is left alone. The `.git` marker is read as utf-8-sig. |
| [model-id-whitespace-entry-parity.md](model-id-whitespace-entry-parity.md) | 0666 | `-m`/`-z`/`--tui` and Kanban model pins refuse spaced cloud model ids like `/model` does (exit 2 before any API call); Anthropic 404 `model: <id>` classifies as model_not_found. |
| [cron-script-sane-path.md](cron-script-sane-path.md) | 0665 | Cron scripts and the cron worker get the terminal's sane PATH (`~/.local/bin` and the standard dirs) whichever process won the tick lock; run docs name the runner process. |
| [spotlight-noindex-scratch.md](spotlight-noindex-scratch.md) | 0664 0683 | On macOS cache/scratch and cache/uv live in `*.noindex` dirs behind symlinks, so Hermes's temp churn stays out of Spotlight. Spotlight contracts run on the real macOS host, not a `sys.platform` fake. |
| [kanban-spawn-tests-host-independent.md](kanban-spawn-tests-host-independent.md) | 0663 | Kanban spawn env/PYTHONPATH tests pin the restart-safe scope wrapper so they pass with or without systemd. |
| [git-consumer-fallback-tests.md](git-consumer-fallback-tests.md) | 0662 | Inject consumer failures at public PM boundary; cover missing-runtime preflight without losing callback assertions. |
| [kanban-session-poll-coalescing.md](kanban-session-poll-coalescing.md) | 0661, 0727 | Overlapping session-list polls share one annotation refresh; failures remain unknown. 0727: a cold profile's poll behind another profile's or a failed read reads its own scope. |
| [kanban-wait-replica.md](kanban-wait-replica.md) | 0658 | Unreachable SSH card waits use fresh owner-qualified replica rows; missing/stale evidence errors, orphan producers are refused, and automatic probes share the route. |
| [live-git-config-test-isolation.md](live-git-config-test-isolation.md) | 0657 | Tests scrub inherited Git location overrides and restore modified live core.bare values. |
| [display-transcript-compression.md](display-transcript-compression.md) | 0656 0660 | Display-only transcript responses omit redundant provider fields and support gzip; sidebar click prepaints cached transcript. |
| [borrowed-home-bootstrap.md](borrowed-home-bootstrap.md) | 0655 | Bootstrap checkout dependencies before isolated borrowed-home user code; avoid re-exec on the store interpreter. |
| [kanban-pin-provider.md](kanban-pin-provider.md) | 0653 | Bind concrete model pins to a serving provider at create/set time; refuse missing or ambiguous providers; resolve legacy NULL-provider pins at dispatch. |
| [kanban-board-recovery.md](kanban-board-recovery.md) | 0633–0637 0639–0640 0648–0649 0652 0659 | Out-of-process table b-tree salvage and in-place SQLite backup install, tombstone-aware reconcile, shared recovery lock with holder identity, contention/deleted-sidecar refusal and forensic reports. |
| [backend-activity-quiet-hint.md](backend-activity-quiet-hint.md) | 0632 | Backend last_active, not a window's event silence, decides a running turn's quiet hint. |
| [recovery-era-test-isolation.md](recovery-era-test-isolation.md) | 0631 0638 0640 0651–0652 | Sandbox profile wrappers, reach the git fallback branch and synchronize downloader counters. |
| [kanban-descendant-wait-refusal.md](kanban-descendant-wait-refusal.md) | 0641 | Refuse explicit or inferred waits on a card's own descendants and mutual return waits. |
| [kanban-dispatch-fairness.md](kanban-dispatch-fairness.md) | 0643–0644 | Rotate the first board on spawning ticks and measure stranded-ready age from the last wake or claim. |
| [kanban-unroutable-tier.md](kanban-unroutable-tier.md) | 0645 | Keep the profile model when an automatically chosen class has no eligible route; explicit pins stay unchanged. |
| [kanban-remote-reference-diagnostics.md](kanban-remote-reference-diagnostics.md) | 0650 | Replica-known remote ids are not phantom completion references; uncertain replica absence cannot prove a warning. |
| [kanban-central-service.md](kanban-central-service.md) | 0604 | With `kanban.sync.links_reader: replica` the sidebar count and the linked-card strip read one projection (`_remote_cards`): a done card whose release isn't verified stays an `unknown` row in both, staleness comes from the replica's last pull (`pulled_at`), an unreadable replica keeps its last rows as stale, a card shared across one lineage counts once, and `links-diff` compares `unknown` rows and is never clean over an unreadable replica (Upkeep showed 14 counted over 9 chips). |
| [tui-gateway-ws-peer-drop.md](tui-gateway-ws-peer-drop.md) | 0607 | A WebSocket peer dropped mid-send ends the read loop as a disconnect with one WARNING, not an ERROR "ws receive failed" traceback. |
| [browser-managed-leases.md](browser-managed-leases.md) | 0612 0613 | Every built-in browser tool (navigate, cdp, dialog, vault ops) leases the managed Chrome under `browser.managed_agent`; supervisors, sidecars and vault scope are bound to the lease. |
| [checkpoint-staging-cap.md](checkpoint-staging-cap.md) | 0615 0627 | A workspace deleted mid-snapshot is one WARNING; oversize files are excluded before `git add`, staging timeouts clean their debris and back off 60 min. |
| [kanban-lockguard.md](kanban-lockguard.md) | 0616 0618 | Every Kanban board connection takes the OFD lock guard and stranded readers reopen; dashboard requests no longer re-run board init + integrity_check. |
| [interrupt-reason.md](interrupt-reason.md) | 0625 0628 | Every interrupt producer records a fixed reason (user_stop, ws_orphan, backend_exit, lease_takeover, session_close) and phase on the interrupted row, the result and one INFO line. |
| [model-routing-managed-adoption.md](model-routing-managed-adoption.md) | 0619 | Live agents adopt the managed subscription policy when enforcement is enabled; aux and main round-robin counters are split. |
| [process-exit-log-noise.md](process-exit-log-noise.md) | 0620 | A process exit mid-turn logs one line, not a traceback, in the pre-final gate, tool executor and errors.log. |
| [test-host-slot-cap.md](test-host-slot-cap.md) | 0611 0690 | xdist workers are capped per host test slot (and by the host's `tests.xdist_workers`); a full `max_in_progress` is reported as idle, not stuck. |
| [hub-project-groups.md](hub-project-groups.md) | 0602 0603 | Every gateway's project groups come from the hub replica when that gateway can't be reached (or before it answers), so an asleep gateway's groups stay; another gateway's declared project can be renamed or themed from here as a hub shared field, applied by its owner when it wakes (`hermes project tree` / `project update`). |
| [websockets-keepalive-logging.md](websockets-keepalive-logging.md) | 0600 | A websockets peer that stops answering pings is a handled drop: every Hermes connection passes `logger=ws_logger()`, so the library's `ERROR keepalive ping failed` + traceback becomes one INFO line (errors.log and self-heal stay quiet; Kanban sync still reconnects). code_health HX015 flags a new `connect()`/`serve()` without `logger=`. |
| [desktop-remote-update-pause.md](desktop-remote-update-pause.md) | 0592 0610 | While a remote gateway runs its own `hermes update`, its SSH dials pause per host: requests and prewarm fail fast from the cached refusal and one probe per backoff window (2–30 s) checks for the end, instead of a new SSH control master per request (53 in 108 s on 2026-10-27); the connection comes back once. |
| [desktop-remote-dial-backoff.md](desktop-remote-dial-backoff.md) | 0597 | A remote that cannot be dialed (host down, refused, SSH timeout, rejected credentials) is remembered by the same gate as 0592: requests and prewarm fail fast from the cached error and one probe per backoff window (2–60 s) checks for it, instead of one SSH control master and a 15 s wait per request (11 dials, 36 stalled requests in 2 min on 2026-10-27); Reload and retry, apply/save and a reachable Test dial at once. |
| [kanban-central-service.md](kanban-central-service.md) | 0589, 0715 | A stale feed copy cannot reopen a locally ended run: pending child-row edits and acknowledged run versions fence older copies; the orphan pass closes non-current runs older than ten minutes (`close_dangling_runs`). 0715 restores a run the card already ended from its terminal event instead of rewriting it to `reclaimed`. |
| [desktop-stale-list-open.md](desktop-stale-list-open.md) | 0585–0588 0605 | A sidebar row kept from a failed session list (busy local backend) still opens live, and the notice and tooltip name its connection as "not responding" instead of "its gateway is offline … never synced"; replica rows of unreachable gateways are unchanged. |
| [kanban-default-placement.md](kanban-default-placement.md) | 0565 0567 0569 0572 0590 0591 0630 0642 0646 0695 | `kanban.placement.route` (e.g. `{macbook: hub}`): a `kanban_create` card with no local-only need is created on the target through `hermes-hub create-task`, bound to its origin chat, and the call returns the target's task id (`hermes-hub outcomes`, 10 s wait) or `status: queued` with a note that the card IS created; an identical retry is never sent twice; stays local for parents, projects, dir/worktree workspaces, uncarried options, local markers or a non-live target; a caller pin to a gateway it cannot reach is refused (`placement_unavailable`), never a silent local card. `hub_cli_path()` finds `hermes-hub` without the login PATH. A card with parents is created here and forwarded to the target once its parents finish (0695). |
| [sidebar-boot-snapshot.md](sidebar-boot-snapshot.md) | 0571 0621 | Cold start paints the sidebar (every gateway's hub-replica rows and the last projects tree) from a snapshot Electron main reads before the backend is up; the CONNECTING overlay stays down; a failed replica read publishes nothing; `[sidebar-timing]` log line. |
| [anthropic-pool-pin.md](anthropic-pool-pin.md) | 0573 | A session pinned to a credential-pool account keeps that account's token on every request (no swap back to an expired first entry); expired entries skipped by model-aware resolution; drift warning in 401 recovery. |
| [harness-daemon-owner-reap.md](harness-daemon-owner-reap.md) | 0581 0582 | A named browser-harness daemon (`browser_exec` `session=`) is stopped once no live process that drove it remains, or after `browser.harness_daemon_idle_s` (2 h) idle, from `browser_exec` and every process exit; the shared default daemon is kept; the `session` description says to omit it. |
| [terminal-exit-kill-waits-for-spawns.md](terminal-exit-kill-waits-for-spawns.md) | 0577 | The graceful exit kill waits (up to 5 s) for foreground commands that have forked but are not yet registered, so a process exit no longer orphans them. |
| [sessions-list-preview-page-only.md](sessions-list-preview-page-only.md) | 0554 | The sidebar session list computes the preview subquery for the page rows only, not every admitted session (1.39 s to 0.35 s on a 12 GB store). |
| [sidebar-poll-reads.md](sidebar-poll-reads.md) | 0647 | One sidebar poll reads each store once: its three slices share one kanban snapshot and remote projection, boards are read on one cached connection and memoized per board, the session list filters and computes last_active once per pass and back-fills pins from the pinned ids, and the replica memo survives hub pulls: sidebar CPU 167-176 to 79-86 ms per 2 s poll (Desktop's query 85-116 to 56-63), same responses. |
| [kanban-replica-read-memo.md](kanban-replica-read-memo.md) | 0580 | The sidebar's remote-links projection re-reads hub.db's ~6k replica task rows only when an index-only seq probe says they changed (or a stale/heartbeat cut-off passes), and matches legacy cards to receipt rows by index: remote projection 49 to 25 ms per sidebar poll, same answers. |
| [kanban-replica-decode-memo.md](kanban-replica-decode-memo.md) | 0557 | Replica rows are decoded and hashed once per distinct stored text, not on every remote-links rebuild. |
| [sidebar-stripe-transition-log.md](sidebar-stripe-transition-log.md) | 0555 | Each sidebar row logs `stripe-transition old->new` with its row inputs to desktop.log (rate-capped). |
| [session-info-queued-switch-provider.md](session-info-queued-switch-provider.md) | 0556 | `session.info` keeps a queued model switch's provider; goal tests stop faking `Path.home()`. |
| [global-context-files.md](global-context-files.md) | 0552 0568 | `agent.global_context_files` (e.g. `["~/AGENTS.md"]`): user-level rules load in every session of the profile whatever its cwd (Kanban scratch dirs, cron without a workdir, project chats), ahead of the project chain, once when the cwd chain already loads the same file; `/context` lists the rows. |
| [wal-holder-scan-single-flight.md](wal-holder-scan-single-flight.md) | 0550 0551 0574 | macOS deleted-WAL holder scan runs at most one libproc pass per process (joiners share it, a pass past its deadline fails open at once and stops itself), and a clean verdict is cached only while the db directory and sidecar identities are unchanged. |
| [backend-writable-open-guard.md](backend-writable-open-guard.md) | 0549, 0579, 0598 | A call site that keeps opening writable `SessionDB()` handles (30+ a minute) is named in a WARNING with the read-only / registry fix; chat-closeout reads compression lineage read-only, ending the 23-writable-opens-per-sidebar-poll loop that held the Desktop backend near 100% CPU; the per-chat Kanban notify poller skips boards whose files have not moved; `GET /board` reuses its read while the board is unchanged. |
| [kanban-owner-wake-visible-waits.md](kanban-owner-wake-visible-waits.md) | 0544–0547 0564 | Every parked card names what it waits on and since when (`wait`: producer, agent-owned, premise); agent-owned waits over 2 h paint an amber "stalled" sidebar stripe/count; the tracker default `wait-check` wakes on owner review/block/triage/agent-owned parks without re-waking for a status it already handled. Pairs with the chat-closeout owner wake. |
| [instant-session-loads-cache.md](instant-session-loads-cache.md) | 0532, 0533, 0548 0623 0674 | A click paints the chat from a local SQLite transcript cache that a background prefetcher keeps current for every gateway's 200 most recent sessions; the REST read starts at the click; an asleep gateway's chat opens read-only; multi-segment lineage reads page in SQL; per-open timing log. An open Inbox card's hidden or unlisted session is prefetched too. |
| [notification-held-handoff.md](notification-held-handoff.md) | 0530, 0576 | Owner-addressed notifications wait with their owner's poller instead of bouncing through every chat's poller on the shared queue (a busy chat no longer pins the Desktop backend); sidebar kanban counts recompute once for all queued readers; each chat's kanban poll reads only its own subscriptions. |
| [desktop-sidebar-replica-live.md](desktop-sidebar-replica-live.md) | 0528 | Every gateway's sessions show from the hub replica at once on a cold start (no SSH wait); owners that published within 180 s give ordinary, running-aware rows. |
| [kanban-no-producer-no-park.md](kanban-no-producer-no-park.md) | 0529 0608 0626 0707 | Every Kanban park records who produces its condition (`awaiting` = card / ci / deploy / external / clock); agent-action waits with no producing card (`agent_action_wait`) and hidden waits on the user (`human_wait`, ask-gate ledger aware) are refused; the third park on one premise becomes an owner card for a supervisor. 0707 refuses a human wait even when the external ref has a number or URL. |
| [exhausted-provider-recovery.md](exhausted-provider-recovery.md) | 0521 | Exhausted providers (`failure_kind` exhausted/auth_missing, `provider`, nullable `reset_at` in UNIX seconds) open a recovery screen naming the provider and reset time instead of the first-run picker; nine locales. |
| [kanban-central-sync-client.md](kanban-central-sync-client.md) | 0466, 0468, 0470, 0472, 0475, 0502, 0503, 0506–0509, 0534 | Central Board sync client: pull-then-push, shadow/import/diff, full-row sends, transient-rejection backoff, capture-gap fix, push feed, synced chat links, seconds columns normalized on apply. |
| [kanban-remote-observation-scope.md](kanban-remote-observation-scope.md) | 0461, 0462, 0483–0485, 0490, 0497, 0498, 0500, 0501, 0505, 0525, 0543, 0599 | Remote kanban observations scoped to linked cards, non-blocking remote-links projection, retained-row evidence refresh in Desktop; projection keyed on replica content (0599). |
| [prompt-admission.md](prompt-admission.md) | 0441–0444, 0446 | Durable exact-owner prompt admission with atomic cancel/reopen and a projected admitted user-row event. |
| [provider-fallback-and-token-routing.md](provider-fallback-and-token-routing.md) | 0480, 0481, 0504, 0520 | Fallback failures preserved, quota holds bounded to whole chains; routing caps compare token estimates, not bytes. |
| [model-pill-stored-model.md](model-pill-stored-model.md) | 0467, 0478, 0482 | Composer pill and effort menu use the chat's stored model while its agent is unloaded. |
| [update-admission-fence.md](update-admission-fence.md) | 0496 | Cross-process admission fence for Desktop reservations and Kanban claims during `hermes update`. |
| [final-output-withhold-and-submit-survival.md](final-output-withhold-and-submit-survival.md) | 0473, 0493 | Rejected final output withheld until final transforms; an accepted submit survives a client socket drop. |
| [scratch-home-metrics-and-ci-stability.md](scratch-home-metrics-and-ci-stability.md) | 0465, 0479, 0489, 0491, 0492, 0494, 0495, 0499, 0519, 0524 | Scratch-home isolation, shared-metrics offer never shown, and CI/portability fixes. |
| [kanban-claim-lease-fencing.md](kanban-claim-lease-fencing.md) | 0523 0558 | Reset heartbeat at claim and fence stale lease echoes with the authoritative grant version, without dropping unrelated task updates. |
| [pm-target-gap-no-worker.md](pm-target-gap-no-worker.md) | 0601 | `pm.ensure()` refuses a package's target gap (POSIX git) in-process instead of spawning a PM worker per call; `selected_git_env` no longer starts an interpreter on every internal git call. |
| [test-runner-host-slots.md](test-runner-host-slots.md) | 0531 0559 0563 0566 | Concurrent `run_tests.sh` runs share a host-wide cap of `cpu_count` pytest file processes (flock slots in `/tmp/hermes-test-slots-<uid>`), instead of each starting `cpu_count`; nested runs skip it. |
| [gateway-launchd-core-update-launcher.md](gateway-launchd-core-update-launcher.md) | 0527 | The launchd gateway plist runs `stderr_timestamp` under an installed core-update launcher (`<root>/bin/hermes-gateway-launcher`), inside the shim's `sh -c exec` string, so plist refreshes keep the launcher; `gateway.status` never takes the launcher's process for the gateway. |
| [kanban-unloaded-sweep-wake.md](kanban-unloaded-sweep-wake.md) | 0518 | Unclaimed kanban notices start one agent turn (worker thread, budget, skip rules) instead of only a transcript row; a held chat whose poller missed is logged as a "poller miss". |
| [kanban-read-connection-cache.md](kanban-read-connection-cache.md) | 0514 | Reuse read-only board connections while preserving fresh commits, replacement detection and nonblocking checkpoints. |
| [onboarding-defer-default.md](onboarding-defer-default.md) | 0513, 0515 | The provider overlay no longer overwrites a configured default model: on a configured install every write waits for Begin on the confirm card (which says it changes the default for new chats); dismiss writes nothing. |
| [kanban-sync-client-fixes.md](kanban-sync-client-fixes.md) | 0511–0512 | Sync client retries rejected rows under a fresh op id, skips non-board folders, keeps every chat of a shared card and syncs link-only boards. |
| [kanban-parked-vs-live-sidebar.md](kanban-parked-vs-live-sidebar.md) | 0510 | Sidebar stripe tells live kanban work (no stripe) from parked cards (grey) and queues held behind a human ask (red); new `kanban_parked`/`kanban_held` row stamps. |
| [model-class-routing.md](model-class-routing.md) | 0404–0429 0431–0437 0440 | Opt-in sticky class allocator (`class:deep|standard|fast`) with managed-subscription fences across CLI, Desktop, API, messaging, delegation, cron, Kanban and auxiliary calls. |
| [desktop-agent-test-instance-hidden.md](desktop-agent-test-instance-hidden.md) | 0469 | A Desktop test instance a kanban worker launches gets no Dock icon and never shows a window; the owner's own Desktop stays visible. |
| [at-completion-refresh.md](at-completion-refresh.md) | 0464 | `@` completion sources can `subscribe` so an open popover re-asks when their background fetch lands. |
| [core-update-drain-hold.md](core-update-drain-hold.md) | 0459, 0471 | A live `<home>/core-update/drain-hold` stops new Kanban claims so a saturated board drains for a core update; running workers are untouched, and wake probes, promotion and reclaim keep running (0471). |
| [kanban-provider-quota-walls.md](kanban-provider-quota-walls.md) | 0457 | Record provider retry-after walls and re-tier walled Kanban cards, with a storm alert. |
| [kanban-ownerless-blocks.md](kanban-ownerless-blocks.md) | 0456 0583–0584 | Refuse ownerless agent blocks (incl. needs_input with no human_request) and undecided blocked cards; sweep old ones through triage once. |
| [kanban-worker-crash-oom-recovery.md](kanban-worker-crash-oom-recovery.md) | 0438 0454 0570 0594 | Scope-killed workers count as crashes; typed OOM recovery raises MemoryMax with admission and alerts on OOM rate. |
| [kanban-triage-sweep-default-assignee.md](kanban-triage-sweep-default-assignee.md) | 0452 | CLI create defaults the assignee; stale triage cards are swept to the default profile once. |
| [desktop-remote-backend-reuse.md](desktop-remote-backend-reuse.md) | 0448–0450 | Release instead of kill remote SSH backends on quit/resume, macOS launcher ownership proof, reuse-miss logging. |
| [desktop-always-on-remote-dashboard.md](desktop-always-on-remote-dashboard.md) | 0526, 0535, 0595, 0671, 0696 | Always-on supervised remote dashboard (launchd/systemd, no idle exit, retires on a replaced runtime, never in a loop: PM ignores runtime markers, identical re-realizations reattach, 15 min retire backoff), startup prewarm (default + recently used named profiles) + keepalive reconnect, 30 s dispatch probe skip, `[remote-timing]` logs. |
| [desktop-home-session-placement.md](desktop-home-session-placement.md) | 0403 | New cwd-only sessions keep a visible provisional or Home placement in every sidebar view. |
| [kanban-sync-seams.md](kanban-sync-seams.md) | 0445 0458 | Phase 1 of the central Kanban sync service, off by default (`kanban.sync.enabled`): outbox capture triggers with a drift guard, `apply_remote_changes`, lease seams in claim/heartbeat/stale-claim, tombstoned deletes, wide ids, dispatcher wake; no network code yet |
| [kanban-actionability-durable-evidence.md](kanban-actionability-durable-evidence.md) | 0393–0401 0396 0430 | Merge-adapted actionable escalation, scope heartbeat freshness, exact legacy attestation with a distinct wire action, contention timestamp retention, and test followups; locally verified candidate |
| [unresolved-goal-fallback.md](unresolved-goal-fallback.md) | 0392 | Optional structured `pre_final_gate` fallback retained at budget/cap/unavailable boundaries, ownership-bound rechecks, private held text, scoped Kanban/process/dispatcher health receipts; enforcement stays opt-in (coach plugin) |
| [retention-observation.md](retention-observation.md) | 0391 | Fail-closed, observation-only non-CI retention slice (`retention_cli observe`); cannot delete or authorize deletion |
| [kanban-origin-adoption.md](kanban-origin-adoption.md) | 0380–0382, 0708 | State-preserving existing-task origin adoption with immutable request replay/conflict checks. 0708 selects the Kanban toolset around trusted-courier dispatch so a worker-only profile is not stuck `kanban_tool_unavailable`. |
| [kanban-local-owner-projection.md](kanban-local-owner-projection.md) | 0384–0390 0654 | Exact local owner evidence and alias-qualified dedup, foreign-profile preservation, nonblocking ledger refresh, one scoped annotation snapshot and per-projection alias-read reuse; live acceptance remains separate. Readers lock the ledger only to advance an observation, and a busy refresh no longer flips every hub row stale (0654). |
| [kanban-origin-work-ledger.md](kanban-origin-work-ledger.md) | 0221 0223 0242–0247 0268 0270 0273–0279 0281–0282 0293 0295–0297 0299 0311 0313–0314 0316 0324–0328 0342 0361 0402 0455 0710 | Owner-qualified remote work, durable binding/creation receipts, fresh lifecycle evidence, routed drawers/archive checks and deferred completion notices. 0710 verifies pre-receipt goal completions from the closing run, matches a long handoff against that run's full summary, and closes an owner-observed archive as `closed_uncompleted` instead of leaving it unfinished. |
| [kanban-origin-ownership-snapshot.md](kanban-origin-ownership-snapshot.md) | 0516–0517 0593 | Read-only, query_only origin ownership snapshot: binding generation, owner running-claim/wake proof and scoped consumer health with epoch; unknown without proof. |
| [kanban-agent-recovery-routing.md](kanban-agent-recovery-routing.md) | 0269 0301 0307–0308 0334 0365 | Current-run continuation exhaustion and automatic stops route to agent recovery while genuine human asks remain visible |
| [kanban-assignment-capabilities.md](kanban-assignment-capabilities.md) | 0300 0336 | Shared configured-capability/model preflight and bounded durable assignment repair |
| [kanban-capability-routing.md](kanban-capability-routing.md) | 0536–0540 0561 0593 0686 | Cross-gateway capability routing: live-checked capability registry shared via hermes-hub, `needs` on block/schedule opens one owner card on the holding gateway and parks on it, typed refusals (no holder / stale / held locally / exhausted), placement preflight for namespaced `requires`. The owner wait-check reads the owner id from every applier result shape. |
| [kanban-strong-tier.md](kanban-strong-tier.md) | 0541 0542 0562 | `kanban.strong_model` / `strong_provider` name the model Kanban review, retry-after-failure and keep-pattern cards run on (instead of following the profile default); `cron.model_provider` applies only to jobs whose model came from `cron.model`, so a provider-less pinned cron job runs on its own model's provider. |
| [registry-backend-reconnect.md](registry-backend-reconnect.md) | 0309–0310 0315 0317–0323 | Exact-generation Linux registry reconnect, atomic retirement and stopped-owner recovery; repeated source identities retained |
| [kanban-pr-reconciliation-wakes.md](kanban-pr-reconciliation-wakes.md) | 0339 0343–0344 0346 | Explicit checkpoints do not self-rearm holds; terminal PR and verified supersession wakes reconcile without claiming acceptance |
| [kanban-dispatch-ownership.md](kanban-dispatch-ownership.md) | 0362–0363 0366–0369 | Canonical-database dispatch authority, native-connection fencing, lifecycle contention and per-board pre-work isolation |
| [process-ci-test-followups.md](process-ci-test-followups.md) | 0257 0262–0263 0345 0347–0348 0357 0439 0447 | Fresh descendant cleanup, bounded event waits, cancellation handoffs, attribution and repeated Nix pipefail fix |
| [managed-chrome-leases.md](managed-chrome-leases.md) | 0370–0372 | Experimental, not release-approved: opt-in owner-scoped default/local/named browser requests, selected vault scope and task/shutdown lease cleanup; configuration docs corrected locally, exact-head CI and changed-head review remain blockers; native core repetition and genuine credential use are unexercised, not claimed |
| [ci-poll-runner-routing.md](ci-poll-runner-routing.md) | 0330–0331, 0338 | Poll-only CI routing, cancellation-safe aggregators and Detect timeout |
| [kanban-pr-park-handoff.md](kanban-pr-park-handoff.md) | 0349 | Orderly park releases PR guard; broker-aware PR-state reads |
| [orphan-reaper-home-scope.md](orphan-reaper-home-scope.md) | 0352 | Orphan reaping requires proven home/UID ownership and fresh process identity |
| [kanban-pr-broker-transport.md](kanban-pr-broker-transport.md) | 0335 | Profile-scoped GitHub broker execution and sanitized setup receipts |
| [ci-retry-isolation-followups.md](ci-retry-isolation-followups.md) | 0286 | AutomatedRow active-Kanban type coverage |
| [ci-retry-isolation-followups.md](ci-retry-isolation-followups.md) | 0288 | Failed-node retries and deflake cards |
| [ci-retry-isolation-followups.md](ci-retry-isolation-followups.md) | 0289 | CPU/shard-budget-aware workers |
| [ci-retry-isolation-followups.md](ci-retry-isolation-followups.md) | 0290 | Load-tolerant test synchronization |
| [ci-retry-isolation-followups.md](ci-retry-isolation-followups.md) | 0291 | Flaky CI warning annotations |
| [ci-retry-isolation-followups.md](ci-retry-isolation-followups.md) | 0292 | Reporting helpers and BOM-safe reads |
| [ci-retry-isolation-followups.md](ci-retry-isolation-followups.md) | 0305 | Git guard teardown ordering and UTF-8 decoding |
| [ci-retry-isolation-followups.md](ci-retry-isolation-followups.md) | 0306 | LSP Git-bearing scratch regression |
| [kanban-scratch-git-preservation.md](kanban-scratch-git-preservation.md) | 0294 | Tool, CLI and REST completion preserve Git-bearing or uncertain scratch before any optional post-tool cleanup |
| [kanban-touch-subscriptions.md](kanban-touch-subscriptions.md) | 0260 | Touch subscriptions do not own work |
| [kanban-condition-wakes.md](kanban-condition-wakes.md) | 0261 0298 0451 | Condition-driven scheduled wakes with generation fencing and shared-park guards |
| [inline-preview-full-document.md](inline-preview-full-document.md) | 0264 | Whole-document inline previews |
| [kanban-default-provider.md](kanban-default-provider.md) | 0265 | Default tier provider inference |
| [test-live-git-config-guard.md](test-live-git-config-guard.md) | 0266 0271 | Protect live git identity during tests |
| [test-real-home-guard-checkout-probes.md](test-real-home-guard-checkout-probes.md) | 0629 | The tests' real-home guard lets a checkout under a guarded root read its own `install-stamp.json` / `installs/*/facts.json` probes; writes stay refused. |
| [kanban-worker-scope-failures.md](kanban-worker-scope-failures.md) | 0267 | Diagnose worker OOM and signal deaths |
| [kanban-dependency-review-continuation.md](kanban-dependency-review-continuation.md) | 0378–0379 | Authenticated dependency parks permit one same-PR repair after structured independent review; claim release remains transactional |
| [archive-linked-confirm.md](archive-linked-confirm.md) | 0110 0383 0560 0617 | Archiving a chat with open Kanban tasks or rented machines asks first, listing them; plugins add checks via `ctx.registerArchiveGuard` |
| [screen-share.md](screen-share.md) | 0109 | Inline Screen Share plugin opens strictly validated VNC addresses on the client Desktop OS, never the remote gateway |
| [session-navigation-shortcuts.md](session-navigation-shortcuts.md) | 0019 0021 0022 0026 0033 0035 0043 0048 0049 0050 0065 | ⌘]/⌘[ next/prev session with a green/red status stripe (stepping from the focused tab), ⌘B mark blocked (grey, skipped), ⌘⇧]/⌘⇧[ any session, ⌘⇧A archive-and-advance, ⌘R resume (⌥⌘R in 0064–0070), ⌥⇧⌘R window reload |
| [ai-session-rename.md](ai-session-rename.md) | 0020 0032 0042 | ⌘⇧E rename (was ⌘⇧N until 0042), pre-filled with an AI short name (`session.title.suggest` RPC) |
| [project-palette.md](project-palette.md) | 0004 0018 0030 0061 | ⌘⇧P moves a session to a project by typeahead (a new name offers "Create project" with an empty `~/code/<slug>`); typing a project name in ⌘P lists "Move to project: <name>" |
| [multi-gateway-projects.md](multi-gateway-projects.md) | 0023 0024 0025 0028 0029 0031 0054 0101 0149 0150 | Sidebar shows every connected gateway's projects (colored, with show/hide toggles); move a session into any gateway's project; archive on the owning gateway; `@session` ref clicks open on the owning gateway; the overview reads each remote's all-profiles tree, and This device's chats never resume on a selected remote |
| [sidebar-status-stripe.md](sidebar-status-stripe.md) | 0038 0039 0044 0051 0063 0070 0089 0091 0094 0095 0099 0104 0124 0215 | Sidebar row stripe shows status (green = done, waiting on you; red = stuck), never green while its Kanban tasks (also cards it shepherds on other gateways, read from the hermes-hub replica), `/goal` or a live background process are still running (project-mode rows past the recents page included) (and the backend no longer reaps a detached session whose background job still runs); a session that owns a live cron job shows a grey loop glyph and is skipped like a parked row; the row shows open/blocked Kanban task counts and a laptop / Mac mini / cloud glyph for the owning gateway; the travelling arc marks only the selected row |
| [new-chat-gateway-hotkeys.md](new-chat-gateway-hotkeys.md) | 0042 | ⌘N starts the new chat on the saved new-session gateway (0722), else This device (switches the gateway to local); ⌘⇧N keeps the selected gateway |
| [new-session-target.md](new-session-target.md) | 0722, 0724 | Device switcher menu gains "New sessions on ▸" (gateway picker): sticky, survives relaunch, unchanged by opening sessions elsewhere; ⌘N, palette and shell New session use it; unreachable target offers This device for that one session |
| [persistent-new-session-draft.md](persistent-new-session-draft.md) | 0036 0170 | ⌘N returns to the unsent new-session draft (text + attachments) instead of an empty composer; the dead-session prune no longer deletes it after you switch away |
| [session-takeover.md](session-takeover.md) | 0013 0014 0015 0060 | "Take over here" for a chat that's live in another Hermes window, naming the machine that holds it; a hidden chat's agent keeps its Browser page |
| [auto-continue-resume.md](auto-continue-resume.md) | 0008 0010 | `desktop.auto_continue.resume_on_startup`: interrupted sessions resume when the backend boots, including turns cut off by a backend shutdown |
| [auto-continue-stored-source.md](auto-continue-stored-source.md) | 0713 | A client-less auto-continue (startup sweep or in-place update-pause watcher) runs as the session's stored `desktop`/`tui` source instead of the backend's env fallback, rebuilding an agent built for another platform; unknown sources keep the default. |
| [fork-updates-and-relaunch.md](fork-updates-and-relaunch.md) | 0007 0011 0012 0016 0066 0672 0698 | **Start here if you'll carry patches.** `updates.fork_merge_upstream` lets `hermes update` merge upstream into your fork. Also: the app relaunches after a headless rebuild, `hermes serve --stop` spares Desktop's backend, no "Switch to This device?" prompt, and `HERMES_DESKTOP_PREBUILT_APP` installs a prebuilt app instead of packing (also one built from an ancestor of HEAD when no desktop input changed since), and it stays current after the stage that packed it moves on |
| [macos-signed-python.md](macos-signed-python.md) | 0017 0027 0123 0142 0148 | macOS: run Hermes from a Developer-ID-signed Python.app, so Full Disk Access, Accessibility and other permission grants survive Python upgrades; a bare `python3` in terminal commands and cron scripts runs the signed bundle too, and the bundle (not `osascript`) owns the launchd gateway job, so permission prompts name Hermes Python |
| [kanban-board-hotkey.md](kanban-board-hotkey.md) | 0056 | ⌘⇧K opens the Kanban board; composer send-queued moves to ⌘⌥K |
| [kanban-drop-attachments.md](kanban-drop-attachments.md) | 0057 | Drag files from Finder onto a Kanban task card or open task dialog to attach them |
| [kanban-all-gateways.md](kanban-all-gateways.md) | 0090 | One Kanban board across every gateway: merged cards, a gateway filter, and device glyphs on cards; writes go to the owning gateway |
| [reload-backend-hotkey.md](reload-backend-hotkey.md) | 0064 0071 | ⇧⌘R restarts the backend and reloads the window, so core/plugin edits land without quitting the app; ⌘R is resume; forceReload on ⌥⇧⌘R |
| [kanban-toast-status-icon.md](kanban-toast-status-icon.md) | 0058 0062 | Kanban toast status icons and task-specific Open action; 0105 supersedes completion/blocked popups with quiet notifications and a persistent sidebar list |
| [owning-gateway-routing.md](owning-gateway-routing.md) | 0040 0041 0045 0046 0047 | With several gateways, opening a session, its tiles and its background RPCs all go to the gateway that owns it (following a 4007 to the right one) instead of opening an empty chat on the selected gateway |
| [kanban-session-links.md](kanban-session-links.md) | 0059 0067 0068 0092 0093 0111 0112 | Chats stay tied to the Kanban cards they create (including `hermes kanban create` from an agent terminal or execute_code) or shepherd: the cards show in the chat's Tasks view (listed by `GET /api/session-kanban-tasks`), its sidebar row stays busy until they land and turns red while one is blocked |
| [dashboard-cloudflare-access.md](dashboard-cloudflare-access.md) | 0037 | Opt-in: a verified Cloudflare Access login for an allowlisted email counts as a dashboard session, so you log in once |
| [tool-search-eager.md](tool-search-eager.md) | 0114 0120 0168 | `tools.tool_search.eager` keeps chosen tools (plugin tools too) always visible instead of behind `tool_search`; `forward_direct_calls` runs a `tool_call` to a directly-listed tool instead of refusing; `tool_describe` of a tool whose toolset is off says so and names `hermes tools enable` |
| [kanban-timed-waits.md](kanban-timed-waits.md) | 0113 0153 0155 0231 0373 | Kanban waits on time park `scheduled` with a wake (`kind='time'`, a parent-less dependency or `SCHEDULED_UNTIL=`; default 24h), not as a red human block; later comments no longer mask a due wake; `overdue_wake` diagnostic; only human blocks count as blocked in the sidebar |
| [pre-verify-every-turn.md](pre-verify-every-turn.md) | 0052 | `agent.pre_verify_every_turn: true` runs the `pre_verify` plugin hook on every final answer, not only after file edits |
| [skills-review-fork-count.md](skills-review-fork-count.md) | 0055 0097 0128 | The background skill-review fork can patch a skill the session already loaded, as long as the loaded copy still matches the file on disk; a refused write hands back the current file so the next call is the corrected write |
| [process-readoption.md](process-readoption.md) | 0069 0072 0073 0076 0077 0085 | Background jobs stay in the shared `processes.json` while other Hermes processes run; after a Desktop restart the backend re-adopts the orphaned ones and wakes their chat when they exit, even one you haven't opened |
| [gateway-exit-diagnostics.md](gateway-exit-diagnostics.md) | 0074 0075 0081 0614 0624 | Gateway exit records carry an info/warning/error level, so routine restarts stop showing as errors; fewer false Slack/websocket errors; `hermes update` finds a gateway whose interpreter path has a space |
| [credential-pool-429-rotation.md](credential-pool-429-rotation.md) | 0078 | A 429 with a reset window over 60 s rotates to another pooled key right away instead of sleeping out the window |
| [account-limit-causes.md](account-limit-causes.md) | 0133 0134 0144 0340 0463 | Fresh usage with quota left lifts a stale quota bench (early subscription reset); an auxiliary 429 benches only the failed model on the failed key (billing/auth stay account-wide); pooled usage rows say why an account is limited (quota vs cooldown, reason, expiry, model-only cooldowns); a duplicate login's stale bench no longer paints a usable account red |
| [kanban-macos-worker-start-time.md](kanban-macos-worker-start-time.md) | 0079 0080 0117 | Live Kanban workers survive a gateway restart, sleep/wake or a macOS hostname change instead of being marked crashed and started again as duplicates; a retried run gets a bounded tail of the previous run's transcript |
| [update-external-supervisor.md](update-external-supervisor.md) | 0082 0083 0084 | `hermes update` leaves launchd-supervised gateways and dashboards for launchd to restart, instead of starting a racing copy |
| [session-lease-wake.md](session-lease-wake.md) | 0086 0087 0088 | Running turns survive laptop sleep (the lease is renewed on wake if nobody else claimed the session); a turn that really lost its session shows a "Hermes stopped this reply" card with Retry instead of silently stopping |
| [small-fixes.md](small-fixes.md) | 0001 0002 0003 0005 0006 0009 0053 | Completion sound "Off", sidebar chevron, no session preview cap, sleep isn't idleness, compression reload spares context engines, desktop-only plugin packages, full-width tab titles |
| [oauth-wire-tool-names.md](oauth-wire-tool-names.md) | 0096 | Resolve OAuth wire names inside tool_call/tool_describe without changing registered names or bridge restrictions |
| [memory-cap-headroom.md](memory-cap-headroom.md) | 0098 0125 | Show free characters near capacity and recommend consolidation with 10% headroom; unattended reviews get the live budget and the staging rule instead |
| [kanban-block-loop-escalation.md](kanban-block-loop-escalation.md) | 0100 0146 | Keep block-loop escalations out of automatic decomposition; cards that already ran are never auto-decomposed (`--force` to override), a changed needs_input reason isn't a loop, unblock/promote move triage → ready |
| [skill-write-feedback.md](skill-write-feedback.md) | 0102 0103 0116 | Skill size warnings, no-op success, and explicit stale-patch reload/atomic-batch retry guidance |
| [kanban-pr-unblock.md](kanban-pr-unblock.md) | 0115 | An explicit unblock after a PR comment releases the active-PR respawn guard |
| [kanban-blocked-sidebar.md](kanban-blocked-sidebar.md) | 0105 0107 0108 | Persistent blocked cards across boards/gateways, grouped by the chat managing them (0107), whose header opens that chat on its own gateway (0108); completions are silent, blocked events retain only away-only OS notifications |
| [session-tab-status-underline.md](session-tab-status-underline.md) | 0106 | Session tab underline uses the sidebar status-stripe color (green/red/grey; neutral for draft/running) |
| [hidden-preview-open.md](hidden-preview-open.md) | 0118 0119 | An agent in a chat you're not viewing (including another profile's) gets "opened" only once its preview page has actually loaded off screen, so its next read/act works and focusing the chat shows the same page |
| [kanban-soft-deadline.md](kanban-soft-deadline.md) | 0121 | Kanban workers near their runtime cap checkpoint with `kanban_continue` and a fresh run resumes from it instead of being killed and counted as a failure; long waits hand off with a wake time instead of polling |
| [kanban-worktree-validation.md](kanban-worktree-validation.md) | 0122 | `kanban_create` / `hermes kanban create` reject a worktree workspace that can never resolve, naming the fix; such a card blocks on its first spawn failure instead of burning retries |
| [turn-stop-notice.md](turn-stop-notice.md) | 0126 | A turn stopped from a client ends with a visible "Stopped by <login>" line, and the log names who sent the Stop |
| [kanban-open-task-hook.md](kanban-open-task-hook.md) | 0127 | `window.__hermesKanbanOpenTask(taskId, board?, gateway?)` lets disk plugins open a card's drawer on its own board and gateway |
| [review-evidence-gate.md](review-evidence-gate.md) | 0129 | `auxiliary.background_review.require_evidence` makes background skill review edit a skill only when a specific moment in the conversation justifies it; "Nothing to save." becomes a neutral outcome |
| [kanban-block-instructions.md](kanban-block-instructions.md) | 0130 | `kanban_block`'s `reason` asks for ask-first, numbered, concrete unblock steps (what the blocked-panel Desktop plugin shows above the composer) |
| [sidebar-row-actions.md](sidebar-row-actions.md) | 0131 | `SidebarSectionRow` takes hover-revealed `actions` and a `strong` (unread) label, for the inbox plugin's per-row Archive |
| [kanban-notice-refusal-rewind.md](kanban-notice-refusal-rewind.md) | 0132 0720 0721 | Refused notices rewind for the owning backend; 0720 keeps local tui claims in flight until a deduplicated transcript row is durable and recovers dead holders; 0721 tests a notice arriving during compaction landing once in the new tip. |
| [kanban-goal-handoff-judge.md](kanban-goal-handoff-judge.md) | 0135 | Goal-mode handoff judge treats the `kanban_complete` / `kanban_request_review` call it gates as the card's "finish with" step, so such cards can reach review |
| [goal-status-only-not-progress.md](goal-status-only-not-progress.md) | 0522 | `/goal` and Kanban goal-mode continuation prompts say a status-only turn is not progress: take the next concrete action or name the exact blocker |
| [cron-bot-chat-busy-defer.md](cron-bot-chat-busy-defer.md) | 0136 | A Bot Chat cron delivery refused because another delivery holds the chat (`SESSION_NOT_OWNED` / `MAX_CONCURRENT_SESSIONS`) is queued and posted once the chat frees, instead of being dropped |
| [tool-hook-order.md](tool-hook-order.md) | 0137, 0138 | `transform_tool_result` fires after `post_tool_call` on the sequential registry path too, so a hint queued in post lands on the same tool result instead of the next one |
| [account-usage-refresh-claims.md](account-usage-refresh-claims.md) | 0139 | Long-running backends keep refreshing pooled account usage: a refresh claim that leaks (thread start failure, a worker dying mid-batch) is released or expires after 60 s instead of showing every account `unknown` until restart; `diagnostics.account_usage_cache` RPC shows claim and snapshot ages |
| [kanban-complete-rejection-class.md](kanban-complete-rejection-class.md) | 0140 | Every `kanban_complete` refusal carries a stable `rejection` class (`goal_judge`, `phantom_cards`, `empty`, `stale_run`, ...) so digests count failed completions by cause |
| [kanban-shared-blockers.md](kanban-shared-blockers.md) | 0141 | Cards stopped by the same outside wall (CI billing, an outage) share a `blocker_id`: one card probes it every 30 min while the rest wait, and clearing it releases them all without a human |
| [review-wait-stripe.md](review-wait-stripe.md) | 0143 | A chat whose last reply links an open PR still waiting on review stripes red when the review is on you (or others were asked over 24 h ago), grey while only others were asked in the last 24 h |
| [plugin-open-preview.md](plugin-open-preview.md) | 0195 | Plugins open a file in the preview pane (`ctx.openPreview`); For-you `.md` links render in the sidebar |
| [kanban-false-done-attention.md](kanban-false-done-attention.md) | 0194 | A chat whose kanban cards are all done stripes amber, not green, when a card finished in the last 24 h admits dropped work with no follow-up card (judged by a plugin-registered check, e.g. `plugins/kanban-honest-done`) |
| [tool-search-defer-outside-worker.md](tool-search-defer-outside-worker.md) | 0145 | `tools.tool_search.defer_outside_kanban_worker` defers the listed tools (the Kanban worker-lifecycle tools on the fork) in chats, cron and one-shots only; dispatcher-spawned Kanban workers keep them direct, saving ~3.7k tokens per call elsewhere |
| [plugin-archive-hotkey.md](plugin-archive-hotkey.md) | 0147, 0157 | Plugins can take over ⌘⇧A for sessions they own (`ctx.registerArchiveHotkeyHandler`); Inbox uses it to archive and open the next Inbox message. `ctx.openNextSession(id)` gives plugin buttons the same next-session step (Inbox last card) |
| [delegate-route-conflicts.md](delegate-route-conflicts.md) | 0355 | Offline model/provider coherence preflight rejects incompatible delegated batches before spawning |
| [delegate-oneshot-budget.md](delegate-oneshot-budget.md) | 0356 | Advertise the one-shot total delegation budget and distinguish oversized requests from exhaustion |
| [kanban-unfinished-visibility.md](kanban-unfinished-visibility.md) | 0358 | Blocked-only work stays unfinished in both Desktop dots; archive writes invalidate cached counts; conditional child holds require immediate human action |
| [model-tiers.md](model-tiers.md) | 0151 0152 0302 | Lazy selected-route preflight; per-task `model`/`provider` on delegate_task (`delegation.allow_task_model`); Kanban cards with no pinned model run on `kanban.default_model`, except reviews, retries after a failed run and keep-pattern matches (each run logs a `model_tier` event) |
| [sidebar-exclude-sources.md](sidebar-exclude-sources.md) | 0154 | Per-profile `sessions.sidebar_exclude_sources` (e.g. `[api_server]`) keeps a machine profile's sessions out of the sidebar project tree |
| [gemini-provider-defaults.md](gemini-provider-defaults.md) | 0156 0258 | `--provider gemini` without `-m` uses a Gemini model instead of sending your `claude-*` default (404); Gemini aux/title default is `gemini-3.8-flash` |
| [sidebar-created-sort.md](sidebar-created-sort.md) | 0158 0575 0578 | Sidebar 'created' sort orders, clusters and date-buckets by `started_at`, so a reply in an old chat no longer lifts it to the top; projects keep their place too (no active-project hoist, no activity re-sort); lane and preview rows rank by their own `started_at`, so rows outside the loaded page stop moving |
| [session-context-nested-rebind.md](session-context-nested-rebind.md) | 0159 | A nested re-bind of the turn's own session restores the binding on clear, so cards created after a queued model switch keep their session id |
| [credential-pool-norefresh-adopt.md](credential-pool-norefresh-adopt.md) | 0160 | A live credential pool adopts tokens pushed to disk for no-refresh OAuth rows instead of keeping the expired copy |
| [kanban-escalated-triage-red.md](kanban-escalated-triage-red.md) | 0161 0234 | A block-loop-escalated triage card counts as waiting on you, so its chat paints red; 0234: non-human/NULL triage counts as unfinished work, so its chat is not green |
| [kanban-worktree-retention.md](kanban-worktree-retention.md) | 0303 | Age-based terminal-worktree cleanup with verified recovery refs; startup orphan tombstones |
| [kanban-worker-worktree-cleanup.md](kanban-worker-worktree-cleanup.md) | 0162 | Worker guidance: `git worktree remove` every worktree you add before `kanban_complete` |
| [browser-background-tab.md](browser-background-tab.md) | 0163 0164 | `browser_exec`'s own tab and the CDP supervisor's fallback page open with `background=True`, so a new browser session stops stealing keyboard focus on macOS |
| [pre-final-gate.md](pre-final-gate.md) | 0165 0167 | `pre_final_gate` plugin hook: hold a final answer silently (never shown or stored) or append to it; `needs_buffer` keeps a held answer from streaming |
| [sidebar-automated-sessions.md](sidebar-automated-sessions.md) | 0166 0190 0191 0192 | Bot / smoke-test sessions (per-profile `sessions.automated_sources`, `"*"` = whole profile, or an allowlist `sessions.interactive_sources` that marks every other source; root `sessions.sidebar_profiles` makes every unlisted profile automated by default, clones included) are hidden from the Desktop sidebar by default; filter-menu "Show automated & test sessions" brings them back; pinned and blocked / needs-attention rows always stay; ⌘]/⌘[ skips the same rows |
| [kanban-park-invariants.md](kanban-park-invariants.md) | 0169 0335 | Optional profile-scoped GitHub broker argv for PR acceptance; cards never park without showing who they wait on: respawn-guard holds are capped then blocked, a closed-unmerged PR binding can be replaced, every `scheduled` park has a wake (human asks stay blocked), block-loop escalations count as waiting on you |
| [top-level-env-scrub.md](top-level-env-scrub.md) | 0169 0171 0172 | `serve`/`dashboard`/`desktop`/`gui`/`gateway`, the post-update Desktop reopen and Electron main drop the inherited kanban worker / delegate-child / session env (whole agent-scope family, one shared list), so chats are not fenced as delegate children; a process that still carries a leaked marker gets Kanban refusals naming the leak and the dead worker card |
| [create-titled-session-source.md](create-titled-session-source.md) | 0180 | `chat -c <title> --create-if-missing` labels the created session with `--source` (or an inherited automation source) instead of `cli`, so integration threads stay out of the sidebar |
| [fork-ci-hardening.md](fork-ci-hardening.md) | 0173 0176 0177 0179 0181 0182 0183 0188 0189 0193 0196–0207 0209 0218 0224 0226 0230 0249 0252 0474 0476 0487 0488 0606 0609 0622 | Fork CI runs its Linux jobs on self-hosted runners; PM-locked Node for python tests; load/container-tolerant test fixes; 0196–0207/0209: hub-runner timeouts and worker sizing, rootless-docker lane, 3-day artifact retention, per-push concurrency on main, nix `workflow_dispatch` |
| [portability-and-provider-fixes.md](portability-and-provider-fixes.md) | 0174 0175 0178 0187 0233 | Contributor email map, commandcode keeps `COMMANDCODE_API_KEY`, utf-8 subprocess decoding and psutil pid check |
| [sidebar-background-process-badge.md](sidebar-background-process-badge.md) | 0184 | Sidebar row lists the background processes holding it off green, with a Stop button |
| [kanban-unloaded-session-delivery.md](kanban-unloaded-session-delivery.md) | 0185 0186 0596 | Card results are written into chats no backend holds (sweeper); `kanban_unseen` paints the row green; a chat mid-turn defers the notice quietly (one overdue WARNING after 15 min), and HX014 flags contextmanagers that swallow the caller's error at a yield |
| [kanban-delegate-marker-owner.md](kanban-delegate-marker-owner.md) | 0208 | The delegate-child env marker counts only while its owner process is alive and an ancestor of the reader; a process outside the lineage drops the leaked agent-scope env once instead of being fenced |
| [plugin-dir-conflicts.md](plugin-dir-conflicts.md) | 0210 | Plugin discovery skips backup-named dirs (`*.bak*`, `*.orig`, `*.old`) and resolves same-name manifests deterministically (basename match wins); `plugins list` and `doctor` show ignored/shadowed copies |
| [kanban-worker-session-id.md](kanban-worker-session-id.md) | 0211 | Task detail API gives each run its `worker_session_id`, so clients open the worker's transcript, not the creating chat |
| [kanban-failure-notices.md](kanban-failure-notices.md) | 0216 0217 | Kanban `gave_up` / `crashed` / `timed_out` notices (Desktop and messaging gateway) name the real cause (model refusal, provider configuration, no result reported, launch failure, time limit, process exit), promise a retry only when the event records one, and make no unsupported fallback claims |
| [file-safety-token-broker.md](file-safety-token-broker.md) | 0222 | Read- and write-deny `~/.config/token-broker` (broker client token) in file tools, search, vision and `@file` refs |
| [hub-sync-replica.md](hub-sync-replica.md) | 0219 0220 0232 0235 0329 0364 | Desktop reads the hermes-hub replica (hub-sync plugin): owner lookup without probing, Kanban falls back to the replica for an unreachable gateway, and cached sessions of unreachable gateways appear as offline-marked rows in the normal sidebar list (standalone section removed; live rows win) |
| [chat-closeout.md](chat-closeout.md) | 0253 0254 0255 | Chat close-out seams: session rows carry `kanban_waiting_on`, grey `waiting` sidebar stripe while cards are open, plugin waiting sources, and `_wake_session_turn` to run one turn in an unloaded chat |
| [kanban-respawn-guard-hold.md](kanban-respawn-guard-hold.md) | 0212 0225 0227 0241 0248 0250 0251 0312 0453 0339 0460 0718 | A held card gets one comment + fix card; `active_pr` lifts for merged/closed PRs or completed queue rejections. 0718 keeps comments from resetting the hold-cap clock and distinguishes deliberate ready holds from stranded cards. |
| [desktop-kanban-worker-readonly.md](desktop-kanban-worker-readonly.md) | 0213 0229 | Desktop opens a Kanban worker session read-only and quietly instead of a "Resume failed" toast (gateway code 4132) |
| [api-server-thread-hops.md](api-server-thread-hops.md) | 0214 0228 | API-server SSE flushes worker-thread hops before `assistant.completed`/EOS so the last commentary is not dropped |
| [codex-subscription-routing.md](codex-subscription-routing.md) | 0236 0237 0238 0239 0240 | Account-gated Codex models route via an entitled ChatGPT/Codex subscription: union catalog, plan-gated pool selection, subscription-first picker |
| [kanban-quota-recovery.md](kanban-quota-recovery.md) | 0259, 0304 | Recovered quota walls use cooldowns; healthy heartbeats/checkpoints clear stale failure errors; stale guard-hold blocks are released |
| [runtime-safety-followups.md](runtime-safety-followups.md) | 0272 0280 0283–0285 | Portable wake cleanup, disabled-MCP diagnostics, fail-closed delegated lineage and Git-guard PATH isolation |
| [session-start-requests.md](session-start-requests.md) | 0287 | Durable plugin-authorized session start identity, at-most-once first-turn dispatch and owner-local reconciliation |

## Applying

The current local candidate contains **400 patches**, pinned to source
`0146a882edf55e22fc9197e2a2e190a0fbacf068` (source independently reviewed and published).
All 400 apply in order with `git am -3 --empty=keep`; 290 selected Python tests across
15 files pass on the applied public series, with no failures or skips.
Patches 0001–0392 are byte-identical and unrenumbered. Patch 0393 explicitly integrates
merge `2b6644387f`; overlapping branch commits must not also be replayed.
See [current verification](mirror-400-verification.md)
for privacy, preservation and exact execution scope. The earlier 377-patch run's
Desktop/typecheck results are historical, not rerun for this export. No runtime
deployment or legacy migration is claimed.
Chrome 0370–0372 remains experimental; see [managed-chrome-leases.md](managed-chrome-leases.md).
Existing source identities are retained, with renumbered references; do not assume
that repeated changes can be applied twice without an explicit adaptation.
See [ci-retry-isolation-followups.md](ci-retry-isolation-followups.md) for the
historical 275-patch verification and topology renumbering (67 tests passed, two bootstrap
home-guard failures; not a fully green suite).

The earlier 0272–0286 mirror verification (historical numbering) applied all 260 patches with `git am -3 --empty=keep` at
merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`. All five changed regression
files passed on the applied series: 58 tests, zero failures, canonical runner with `-j 1`.
This does not claim full-suite, Desktop rendering, deployment or exact-head CI verification.

The patches touch shared files (keybind registry, i18n, sidebar, the RPC contract), so they are
**not independent**. Apply them in order, either all of them or a prefix up to the last one you
need. Revert what you don't want afterwards.

```bash
cd /path/to/hermes-agent
git worktree add --detach ../hermes-mods-test 4957bbd0a0
cd ../hermes-mods-test
git am -3 --empty=keep ~/code/hermes-mods/core/patches/*.patch
```

Verified on October 17, 2026 (PT): all **127** patches applied cleanly with `git am -3 --empty=keep`
in a detached worktree starting at merge-base `4957bbd0a0`. Start from that merge-base
(`git merge-base upstream/main origin/main` for this fork snapshot), not a newer
`upstream/main`; upstream changes can otherwise conflict with the early patches.

Each doc's **Applying** section describes its dependencies and verification limits.
The verified apply set here is the full ordered series; do not assume individual patches are independent.

If a later patch conflicts because upstream moved, re-implement it from its doc instead of forcing
the hunk. Each doc's **Code** and **Tests** sections are the spec.

## Building and running a patched Hermes

1. **Python changes** (`tui_gateway/`, `hermes_cli/`, `agent/`) take effect on the next process start:
   restart the gateway (`hermes gateway run`, or your launchd job) and the Desktop backend.
2. **Desktop changes** (`apps/desktop/`) need a rebuild:
   ```bash
   # macOS: sign it with your Developer ID, or the app is ad-hoc signed and macOS treats every
   # rebuild as a new app (permission grants reset, signature-checking launchers refuse it)
   APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
     CSC_IDENTITY_AUTO_DISCOVERY=true hermes desktop --build-only --force-build
   ```
   That takes about 3 minutes. Quit and reopen Hermes.app afterwards. You can also set
   `desktop.macos_signing_identity` once so every rebuild is signed.
3. **Tests.** Python: `scripts/run_tests.sh <files>` from the repo root. A bare `venv/bin/python -m pytest`
   can fail on a missing `ruamel`. Desktop: `cd apps/desktop && npx vitest run <file>`; vitest needs a
   current Node (v18 is too old). `npm run typecheck` checks types, and `npx eslint --fix <files>` fixes
   import/prop sort lint. Run the tests each doc lists before you trust a rebased patch.
   When I last ran the full series (Oct 2026): every test the patches add or touch passes, lint has 0 errors,
   and tsc is clean. The rest of the desktop suite had 13 failures (`mac-sign`, `desktop-uninstall`,
   `remote-lifecycle`, native-deps and similar). Those fail the same way on the unpatched base on a Mac, so
   they're environment-dependent, not caused by these patches. One `hermes-bots/group-activity` test is flaky
   with or without the patches.

The 0096–0105 mirror verification (October 16, 2026 PT) applied all 105 patches cleanly
and passed all changed regression files: 197 Python tests across 8 files and 99 Desktop
tests across 7 files. This targeted run did not rerun the full suite or typecheck.

The 0117–0127 mirror verification (October 17, 2026 PT) applied all 127 patches cleanly
and passed every test file those patches add or touch: 195 Python tests across 16 files and
214 Desktop tests across 8 files. Again no full suite or typecheck.

The 0128–0129 mirror verification (October 18, 2026 PT) applied all 129 patches cleanly and
passed the changed test files: 92 Python tests across 3 files.

The 0130 mirror verification (October 18, 2026 PT) applied all 130 patches cleanly with `git am -3 --empty=keep`
at merge-base `4957bbd0a0`; the patch's new schema test passes (and fails without it).

## Historical mirror verification (before the 368-patch export)

Previously verified full ordered series: 292 patches from merge-base `4957bbd0a0`, fetched upstream `9b904dcc22`, fork `e295d0da73` (last non-merge patch `2c047de606`). All patches apply with `git am -3 --empty=keep`. The applied final public series passes 43 tests across six files: shebang checker, durable CLI takeover, adversarial takeover, full-turn takeover, durable recovery and terminal background-chat guard. Coverage and normal privacy hooks pass. Desktop suites and full Nix builds were not independently rerun by this mirror.

Merged branches inserted earlier commits: existing patch bytes and slugs are preserved while numbers and documentation references move together. Patch 0284 substitutes example contributor identifiers. Patch 0360 shortens a mock API key and parenthesizes a fixture argument to avoid scanner false positives; both affected test files pass in the final applied series. Existing privacy scrubs and the 0251 adaptation remain intact.

| Feature | Patches | Behavior |
|---|---|---|
| [CI and source fixes](mirror-ci-source-fixes.md) | 0256 0284 0341 0350–0351 0353 | CI race/warmup fixes, public attribution, pinned source mirror and pipefail guards |
| [Foreign tile lifecycle](foreign-tile-lifecycle.md) | 0359 | Keep owner-routed foreign tiles across ambient refresh; retry missing owner sessions |
| [Session takeover](session-takeover.md) | 0360 | Cooperative durable CLI handoff and detached-chat origin guard |

## Gotchas

- **`hermes update` drops uncommitted edits and resets to `origin`.** Keep the patches as commits on
  a fork you own, and set `updates.fork_merge_upstream: true` (patch 0007), so updates merge upstream
  into your commits. The first update after you adopt a fork still runs the *old* updater, which backs
  up and resets; that's fine once the fork contains upstream. Details are in
  [fork-updates-and-relaunch.md](fork-updates-and-relaunch.md).
- **`hermes update` stops a hand-started gateway or `hermes serve` and doesn't restart it.** Run
  anything you depend on as a supervised service (launchd `KeepAlive`).
- **Never run `hermes serve --stop` just to restart one server.** Without patch 0012 it also kills
  the Desktop app's own backend.
- **RPC contract.** Patches that add JSON-RPC methods or events also edit
  `apps/shared/src/gateway-contract.generated.ts` and `gateway-contract.openrpc.json`. If those
  conflict, regenerate them from the Python contracts (`tui_gateway/contracts/`) with
  `python scripts/gen_gateway_contracts.py`; don't hand-merge them.
- **i18n.** Every new UI string has to be translated in each `apps/desktop/src/i18n/*.ts` locale or
  listed in `overlay-gaps.json`, or `overlay-completeness.test.ts` fails. After changing `en.ts`, run
  `npm run i18n:keys` and commit `locales/_keys.desktop.json`.
- **Fix-up patches 0031–0034.** These close test and lint gaps in earlier patches. Take each one with the
  patch it fixes:

  | Patch | Fixes | Goes with |
  |---|---|---|
  | 0031 | translations for the `sidebar.filter.*` labels | 0024 |
  | 0032 | `session.title.suggest` wire contract + regenerated contract files | 0020 |
  | 0033 | import order lint in `use-desktop-integrations.ts` | 0022 |
  | 0034 | regenerated `locales/_keys.desktop.json` | 0019–0024 |
- **Keybinds.** The defaults live in `apps/desktop/src/lib/keybinds/actions.ts` and the handlers in
  `apps/desktop/src/app/hooks/use-keybinds.ts`. `actions.test.ts` rejects chord conflicts.
- **Tags.** I tag my commits `[daveey]` in the subject so they're easy to list
  (`git log upstream/main..origin/main`). Use your own tag.


| Feature | Patch | Description |
|---|---|---|
| [Process and CI followups](process-ci-test-followups.md) | 0327 0332 0333 0337 0354 0374–0375 | Readiness, duplicate deadline identities, curl detection fallback and bounded fast-check lane. |
| [Session start requests](session-start-requests.md) | 0376–0377 | Trusted metadata, authenticated SSH principal and persisted-owner preservation. |
| [Session-start toolset allowlist](session-start-toolsets.md) | 0477 0486 0553 | A durable start can pin a closed toolset allowlist (validated, persisted, kept across resume, compression and branches; reloads cannot widen it). Runners repair chats use it to get only the runners tools. |
| [Blocked clarify answered elsewhere](clarify-early-answer.md) | 0723 | A decision recorded in the user-steps For-you queue ends a session's blocked `clarify` within seconds (`answered_elsewhere`) instead of at its timeout |

Latest appended range (0458–0460): [456-patch verification](mirror-456-verification.md); before that (0396–0339): [453-patch verification](mirror-453-verification.md). Current full-series apply instructions and executed tests: [390-patch verification](mirror-390-verification.md). Historical adaptations, duplicate treatment and broader prior tests: [377-patch verification](mirror-377-verification.md).

Numbering note (0553–0570): these 18 patches are the fork commits in `upstream/main..origin/main` that had no public patch, appended in fork order to the curated series. A fresh `git format-patch` regeneration (576 patches at origin/main `8b741ea26b`) numbers the same commits differently, because the published series replaced the branch replay of seven actionable-evidence commits with the integration patch 0393 and carries a different order from 0343 on. Published numbers are kept so doc references stay valid. A fresh regeneration therefore does not apply on the merge-base either (it stops at its 0234). The published series used to stop at 0510 on merge-base `4957bbd0a0`. The 2026-10-28 repair adapted 0510, 0543, 0545, 0557, 0604, 0610 and 0618 to series context (numbers unchanged), and `git am -3 --empty=keep` from that merge-base now succeeds through the tip. See [mirror-651-verification.md](mirror-651-verification.md).

Numbering note (0605–0628): these 24 patches are the fork commits in `upstream/main..origin/main` (to `246370c63c`) that had no public patch, appended in fork order. Not given a patch on purpose: `4ab695a8c9` and `3e4f06dd91` (contributor-map chores, already mirrored as 0174 and 0284 with a scrubbed email); `18c419d2a9`, `6ab333af32`, `53a6693092`, `d7235c1661`, `dd13c9b77c`, `c13f6de33e`, `3d0d4ea6a4` (the actionable-evidence replay, mirrored as the single integrated patch 0393). Patch 0604 (`abc3b16214`) precedes 0605 in number but follows several of these in fork order, so the number order differs from fork order there. Series apply order for 0605–0628 was re-verified as part of the full-series `git am` in [mirror-651-verification.md](mirror-651-verification.md).

Numbering addendum (0652–0653): final source refresh added `a934e39df2` and `9c943cbd2f`, mirrored in that order. Every commit after `7f117fa465` through `9c943cbd2f` now appears once. Application and 49 focused test passes for this addendum are recorded in [mirror-651-verification.md](mirror-651-verification.md).

Numbering note (0631–0651): the full fork log was regenerated (657 non-merge commits through `90a63800fb`), compared against the curated public inventory, and this appended range follows `7f117fa465` in fork order. Historical numbering/adaptations above remain unchanged; these are not raw log ordinal numbers. The prematurely published sidebar-poll patch `2bc039b9bf` moved from 0631 to its proper place at 0647; doc references were updated. Every commit after `7f117fa465` through this snapshot is represented once. Application, tests, privacy substitutions and remaining historical limits: [mirror-651-verification.md](mirror-651-verification.md).

Numbering note (0674–0688): appended after the already-published 0673 (worker nodes, `6d29976569`; do not renumber). 0671 is two files on purpose (do not renumber): `a3a632954c` (local lineage counts) and `8e6534e7ad` (SSH runtime markers). 0672 is `fc0f7c9910`. This range skips those three. 0687 and 0688 landed on the fork after 0672 and are included. 0673 and 0681 are adapted to series order (`runs_on` lands before `validate_skills`); From hashes are unchanged. Historical gaps called out above (actionable-evidence replay, contributor-map chores) stay unpatched.

Numbering note (0689–0694): appended after 0688 in fork order (`7137506242`, `175f5c0e57`, `ac43921555`, `12fb47ed09`, `d4cb0dafe1`, `a081ba9470`). 0689 and 0690 extend the existing retirement-ledger and host-slot docs.
