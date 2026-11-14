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

## Current inventory

[Hostname normalization](hostname-normalization.md) documents the subsequent privacy
repair, regex policy rules, fingerprint re-pins and successful 1923-entry replay.
The [2253 regex escape repair](hostname-escape-repair.md) is a **mandatory post-replay
overlay** in the Applying recipe below, re-bound to snapshot 4059 by `repair_hostname_escapes_4059.py` (snapshot 3727: `repair_hostname_escapes_3727.py`) (snapshot 3713: `repair_hostname_escapes_3713.py`; snapshot 3689: `repair_hostname_escapes_3689.py`; snapshot 3652: `repair_hostname_escapes_3652.py`; snapshot 3622: `repair_hostname_escapes_3622.py`; snapshot 3577: `repair_hostname_escapes_3577.py`; snapshot 3555: `repair_hostname_escapes_3555.py`; snapshot 3529: `repair_hostname_escapes_3529.py`; snapshot 3501: `repair_hostname_escapes_3501.py`; snapshot 3491: `repair_hostname_escapes_3491.py`; snapshot 3473: `repair_hostname_escapes_3473.py`; snapshot 3423: `repair_hostname_escapes_3423.py`; snapshot 2926: `repair_hostname_escapes_2926.py`; snapshot 2678: `repair_hostname_escapes_2678.py`; snapshot 2563: `repair_hostname_escapes_2563.py`; snapshot 2491: `repair_hostname_escapes_2491.py`; snapshot 2409: `repair_hostname_escapes_2409.py`). The immutable mails alone retain two invalid
placement regexes; the original verifier proves historical fidelity, not regex validity.

4059 patches, one entry per non-merge source commit through `3c0d856312756637a976519ed8315cffa4356c33`.
Published ordinals never move. A pass appends exactly the source commits whose SHA header is not yet published,
in `git rev-list --reverse --no-merges` order among themselves (the 1253 pass placed merged-branch commits at their
first-parent merge instead, because that order replays cleanly). So a commit that reached the fork through a merged branch can follow
later-dated mainline commits: ordinals 1203 onward include such entries, and 1282 (`3c2af6ee2e`, merged with PR #312)
follows 1281 although it sorts at position 1259 by date. Each patch is identified by its SHA header, never by its
position in the log. `core/verify_mirror.py` checks exactly this: contiguous ordinals, unique headers, header set equal
to the source set. Patch 1228 carries a merge's conflict resolution (see [gateway-restart-drain-holds-and-reattach.md](gateway-restart-drain-holds-and-reattach.md)).
Upstream exclusion tip: `b51a30c461ee663313ce89d01947bb10b55a619c`; current merge-base: `b51a30c461ee663313ce89d01947bb10b55a619c` (previous snapshots: `55b0b2611f683a12450163b9fe423abbd61a9923`, `d01ed2bfac3469fa582b9aaeec746f3e123bd78a`).
Replay starts at `831637fe8d58f050e9aa776b621bb2c7b614dcd1`. All forty-one published bridges remain unchanged; three source-derived integration bridges precede entries 2254, 2255 and 2256, one more (upstream `d01ed2bfac` via fork merge `91c2b94744`) precedes entry 3185, and one more (upstream `55b0b2611f` via fork merge `db5a056366`, [bridges/upstream-55b0b2611f-via-db5a056366.diff](bridges/upstream-55b0b2611f-via-db5a056366.diff)) precedes entry 3651, and one more (upstream `b51a30c461` via fork merge `a38507dd8f`, [bridges/upstream-b51a30c461-via-a38507dd8f.diff](bridges/upstream-b51a30c461-via-a38507dd8f.diff)) precedes entry 3719 (47 total). The bridge before 2087 excludes the six byte-identical path deltas carried by patch 2087. Entries 1626, 1649 and 1656 are applied and tree-verified at their source parents, then represented by ancestry-only merge markers in the main replay. Entries 2410–2491, 2492–2563, 2564–2678 and 2679–2726 and 2727–2926 and 3424–3473 and 3474–3491 and 3492–3501 and 3502–3529 and 3530–3555 and 3556–3577 and 3578–3622 contain no merges and need no new bridge. Entries 3623–3652 contain the upstream sync merge `db5a056366`: its one side-branch commit (3651) is ordered at that merge, after the new bridge. Entries 3653–3689 contain no merges and need no new bridge. Entries 3690–3713 contain no merges and need no new bridge. Entries 3714–3727 contain the upstream sync merge `a38507dd8f`: its one side-branch commit (3719) is ordered at that merge, after the new bridge. Entries 3728–4059 contain no merges and need no new bridge. Current snapshot: [mirror-replay-4059.json](mirror-replay-4059.json), [mirror-policy-4059.json](mirror-policy-4059.json), and [verification](mirror-4059-verification.md). Previous snapshots: [mirror-3727-verification.md](mirror-3727-verification.md), [mirror-3713-verification.md](mirror-3713-verification.md), [mirror-3689-verification.md](mirror-3689-verification.md), [mirror-3652-verification.md](mirror-3652-verification.md), [mirror-3622-verification.md](mirror-3622-verification.md), [mirror-3577-verification.md](mirror-3577-verification.md), [mirror-3555-verification.md](mirror-3555-verification.md), [mirror-3529-verification.md](mirror-3529-verification.md), [mirror-3501-verification.md](mirror-3501-verification.md), [mirror-3491-verification.md](mirror-3491-verification.md), [mirror-3473-verification.md](mirror-3473-verification.md), [mirror-3423-verification.md](mirror-3423-verification.md), [mirror-2926-verification.md](mirror-2926-verification.md), [mirror-2678-verification.md](mirror-2678-verification.md), [mirror-2563-verification.md](mirror-2563-verification.md), [mirror-2491-verification.md](mirror-2491-verification.md), [mirror-2409-verification.md](mirror-2409-verification.md), [mirror-2253-verification.md](mirror-2253-verification.md). The following links retain historical verification:
Previous replay: [mirror-replay-2018.json](mirror-replay-2018.json) (twenty-four hash-pinned bridges and three source-checked ancestry-only entries) and [mirror-policy-2018.json](mirror-policy-2018.json). Executed checks: [mirror-2018-verification.md](mirror-2018-verification.md); previous snapshots: [mirror-2909-verification.md](mirror-2909-verification.md), [mirror-2726-verification.md](mirror-2726-verification.md), [mirror-2017-verification.md](mirror-2017-verification.md), [mirror-2016-verification.md](mirror-2016-verification.md), [mirror-2015-verification.md](mirror-2015-verification.md), [mirror-2010-verification.md](mirror-2010-verification.md), [mirror-2005-verification.md](mirror-2005-verification.md), [mirror-2004-verification.md](mirror-2004-verification.md), [mirror-1988-verification.md](mirror-1988-verification.md), [mirror-1979-verification.md](mirror-1979-verification.md), [mirror-1932-verification.md](mirror-1932-verification.md), [mirror-1923-verification.md](mirror-1923-verification.md), [mirror-1918-verification.md](mirror-1918-verification.md), [mirror-1821-verification.md](mirror-1821-verification.md), [mirror-1702-verification.md](mirror-1702-verification.md), [mirror-1625-verification.md](mirror-1625-verification.md).
The preceding snapshot's executed checks are in [mirror-1427-verification.md](mirror-1427-verification.md), with its policy [mirror-policy-1427.json](mirror-policy-1427.json) (previous snapshots: [mirror-1381-verification.md](mirror-1381-verification.md), [mirror-1326-verification.md](mirror-1326-verification.md), [mirror-1299-verification.md](mirror-1299-verification.md), [mirror-1281-verification.md](mirror-1281-verification.md), [mirror-1253-verification.md](mirror-1253-verification.md), [mirror-1235-verification.md](mirror-1235-verification.md), [mirror-1157-verification.md](mirror-1157-verification.md)); the inherited curated integration overlay is described in [mirror-1134-verification.md](mirror-1134-verification.md).
The earlier curated-number notes below are historical, not current inventory counts.
See [mirror-958-verification.md](mirror-958-verification.md) for reconciliation, merge-only adaptations, privacy and executed checks.

## Mirror 3728–4059

Patches through `3c0d856312`; verification in [mirror-4059-verification.md](mirror-4059-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Placement refusal isolation and home-path recognition](mirror-4059-a-placement.md) | 3728 | worker placement: a refused pool repin no longer suppresses the origin repin |
| [Duplicate-language and same-creator overlap guards](mirror-4059-a-overlap.md) | 3729 | kanban overlap guards: a keyword stops at the next one; no-op is no denial; a bumped counter is a re-issue; a moved index is an index |
| [Duplicate-language and same-creator overlap guards](mirror-4059-a-overlap.md) | 3730 | kanban overlap guards: a hedged negator still denies; a keyword-only aside is no keyword; none other than is no count; pass/take/try are legs |
| [Live snapshot sweeps and replica-age regression contracts](mirror-4059-a-sync-recovery.md) | 3731 | kanban sync: a 410 on a primary board's live file sweeps this gateway's rows the snapshot lacks |
| [Scratch-state, refusal-backoff and archive restore safeguards](mirror-4059-a-scratch.md) | 3732 | kanban gc: scratch refusal memo timestamps stay below Windows' gmtime limit; skip_logged never raises |
| [Duplicate-language and same-creator overlap guards](mirror-4059-a-overlap.md) | 3733 | kanban overlap: one isolated negative per same-creator / re-issue / dup-detector guard |
| [Content-keyed reuse of the macOS state lane](mirror-4059-a-ci-state.md) | 3734 | ci: mini state lane skips same-PR repeats and unrelated .github edits |
| [Placement refusal isolation and home-path recognition](mirror-4059-a-placement.md) | 3735 | hub-read root paths: keep every path on a bare $NAME, a backtick or a quoted cd |
| [Worker policy-root isolation and held-worker diagnostics](mirror-4059-a-worker-policy.md) | 3736 | worker policy root: deny by default, a durable TMPDIR outside account and HOME never admits HERMES_TEST_POLICY_ROOT |
| [Advisory lint fetch recovery on partial clones](mirror-4059-a-ci-lint.md) | 3737 | CI lint: advisory diff steps fetch through fetch-merge-base, so git 2.55's promisor BUG no longer skips them |
| [Advisory lint fetch recovery on partial clones](mirror-4059-a-ci-lint.md) | 3738 | tests(ci lint advisory fetch): healthy-path shim survives the runner's own git 2.55 BUG |
| [Placement refusal isolation and home-path recognition](mirror-4059-a-placement.md) | 3739 | worker-first pickup gate: pin INVALID_REQUIRES through the placement tick |
| [Scratch-state, refusal-backoff and archive restore safeguards](mirror-4059-a-scratch.md) | 3740 | kanban park pin: a commit-less repo (git init, nothing committed) holds no state |
| [Attended claim-quiet observation and work custody](mirror-4059-a-quiet-observation.md) | 3741 | feat(quiet): gateway-local quiet proof — activity registry, claim-quiet lease, executed-code attestation [daveey] |
| [Attended claim-quiet observation and work custody](mirror-4059-a-quiet-observation.md) | 3742 | fix(quiet): CI regressions — boot closure, replaced Thread class, stand-in agents, adapter labels [daveey] |
| [Attended claim-quiet observation and work custody](mirror-4059-a-quiet-observation.md) | 3743 | fix(quiet): stand-in review agents dispatch via defining class; track gitignored attestation stub fixture [daveey] |
| [Attended claim-quiet observation and work custody](mirror-4059-a-quiet-observation.md) | 3744 | fix(quiet): seed registry membership from the process reservation; real Registry membership API |
| [Attended claim-quiet observation and work custody](mirror-4059-a-quiet-observation.md) | 3745 | test(quiet): RED-on-parent coverage for review nits (shut-down pool submit, cancelled queued audio job, same-boot cooldown, expired-episode end) |
| [Attended claim-quiet observation and work custody](mirror-4059-a-quiet-observation.md) | 3746 | fix(quiet): dispatcher custody attaches via adopt_spawned (health CC); test waits on condition, not clock |
| [Attended claim-quiet observation and work custody](mirror-4059-a-quiet-observation.md) | 3747 | refactor(quiet): split worker Popen out of _default_spawn so custody keeps its CC at main's 19 [daveey] |
| [Attended claim-quiet observation and work custody](mirror-4059-a-quiet-observation.md) | 3748 | fix(quiet): executed-code index cap fits the grown gateway graph (MAX_LOADED_CODE 2^18, 2x headroom asserted) |
| [Attended claim-quiet observation and work custody](mirror-4059-a-quiet-observation.md) | 3749 | fix(quiet): quiet-only stuck classification never masks main's new admission_deferred hold |
| [Live snapshot sweeps and replica-age regression contracts](mirror-4059-a-sync-recovery.md) | 3750 | kanban live resync sweep test: exercise the since guard and the in-transaction keep re-check |
| [Lifecycle guard stdin groups and clustered shell commands](mirror-4059-a-lifecycle.md) | 3751 | lifecycle guard: pin five #2393 conditions no test reached |
| [Worker policy-root isolation and held-worker diagnostics](mirror-4059-a-worker-policy.md) | 3752 | kanban pickup-stall: read the registry hold by truthiness, like pickup's eligible() |
| [Duplicate-language and same-creator overlap guards](mirror-4059-a-overlap.md) | 3753 | kanban overlap guards: a counter hides only itself; v2.1 and try/take counters are re-issues; not/never before as/because is no denial |
| [Desktop quota-wall deferral with session routing identity](mirror-4059-a-quota-routing.md) | 3754 | fix(tui-gateway): a Desktop session on a router-picked model moves off a far quota wall [daveey] |
| [Desktop quota-wall deferral with session routing identity](mirror-4059-a-quota-routing.md) | 3755 | test(tui-gateway): the _make_agent stubs take the session id the resolver now receives [daveey] |
| [Profile-board ledger fallback boundaries](mirror-4059-a-chat-links.md) | 3756 | kanban chat links: tighten the profile-board ledger fallback (PR #2372 review nits) |
| [Worker policy-root isolation and held-worker diagnostics](mirror-4059-a-worker-policy.md) | 3757 | tests: pin #2454's relative HERMES_TEST_POLICY_ROOT refusal under a temp cwd and the primary-board-only unknown-streak sweep |
| [Lifecycle guard stdin groups and clustered shell commands](mirror-4059-a-lifecycle.md) | 3758 | lifecycle guard: a group's own stdin redirection feeds its commands |
| [Scratch-state, refusal-backoff and archive restore safeguards](mirror-4059-a-scratch.md) | 3759 | kanban gc test: the scratch refusal memo loader drops a bool last_at/retry_at |
| [Foreign process-management regression contracts](mirror-4059-a-process-contracts.md) | 3760 | tests: pin the foreign process_manage gates and the exit recorder's status (#2376 survivors) |
| [Scratch-state, refusal-backoff and archive restore safeguards](mirror-4059-a-scratch.md) | 3761 | tests: scratch archive restore drops only gone remote refs; a detached HEAD is checked |
| [Foreign process-management regression contracts](mirror-4059-a-process-contracts.md) | 3762 | test: foreign poll/log/wait/kill report a recorded kill marker before the supervisor settles |
| [Update-paused turns remain logically live](mirror-4059-a-sidebar-update.md) | 3763 | fix(sidebar): a turn paused for a core update keeps turn_live, so the row does not paint red for the update [daveey] |
| [Live snapshot sweeps and replica-age regression contracts](mirror-4059-a-sync-recovery.md) | 3764 | replica-age resume: pin future seen_at, the memo-only detach and seen_at = last observation |
| [Worker-liveness evidence regression pins](mirror-4059-a-liveness.md) | 3765 | tests: pin three #2420 liveness guards that post-merge mutants survived |
| [Same-call worker persistence receipt resend](mirror-4059-a-receipt-resend.md) | 3766 | worker store: re-send a receipt whose reply timed out instead of latching |
| [Same-call worker persistence receipt resend](mirror-4059-a-receipt-resend.md) | 3767 | worker store: re-send an unknown receipt inside the same call, never on a later write |
| [Duplicate-language and same-creator overlap guards](mirror-4059-a-overlap.md) | 3768 | existing-work record: pin CRLF list stripping (CR-only normalisation survived) |
| [Placement refusal isolation and home-path recognition](mirror-4059-a-placement.md) | 3769 | placement: linear cd-home .hermes scan; pushd home and commented cd count |
| [Source-workspace launch cleanup and clone-root diagnostics](mirror-4059-a-source-workspace.md) | 3770 | kanban source launch: never write the fenced branch_name on a source card; a workspace-record failure releases the claim |
| [Requested cron stops are warnings, not failed-job defects](mirror-4059-a-cron-stop.md) | 3771 | cron: a requested stop (cancel, Stop, remove) logs a WARNING, not 'Job failed' (cron-7c04faf754) |
| [Duplicate-language and same-creator overlap guards](mirror-4059-a-overlap.md) | 3772 | overlap guards: pin newline clause end, "never", the ’ apostrophe and one-sided replace markers |
| [Scratch-state, refusal-backoff and archive restore safeguards](mirror-4059-a-scratch.md) | 3773 | tests: deflake test_cli_budget_flag_and_default float compare |
| [Source-workspace launch cleanup and clone-root diagnostics](mirror-4059-a-source-workspace.md) | 3774 | kanban workspace_source tests: the unregistered-clone refusal names the exact clone root |
| [Awaited-card wake and unreachable-owner regression contracts](mirror-4059-a-awaited-wake.md) | 3775 | awaited-card wake tests: kill the #2470 post-merge mutants M2 (ref change in one episode) and M5 (expedite re-arm) |
| [Awaited-card wake and unreachable-owner regression contracts](mirror-4059-a-awaited-wake.md) | 3776 | test(kanban): an ssh timeout/OSError to the owner still wakes on a fresh Board replica [daveey] |
| [Attended claim-quiet observation and work custody](mirror-4059-a-quiet-observation.md) | 3777 | fix(quiet): all_quiet_deferred uses a positive allowlist of DispatchResult fields |
| [Hold new queue-reason comments while preserving events](mirror-4059-a-queue-comments.md) | 3778 | kanban queue reason: comment a new wait reason only once it has held 120 s |
| [Hold new queue-reason comments while preserving events](mirror-4059-a-queue-comments.md) | 3779 | kanban queue reason tests: origin-repin refusal pins which reasons are commented, so run it without the comment hold |
| [Lifecycle guard stdin groups and clustered shell commands](mirror-4059-a-lifecycle.md) | 3780 | lifecycle guard: walk the payload of a clustered shell -c (`bash -ec`, `-lc`, `-xc`, `-c -e`) |
| [Per-pass CPU accounting for loops and polled routes](mirror-4059-a-loop-accounting.md) | 3781 | loop_accounting: per-pass CPU accounting for Kanban loops and polled routes (P1) |
| [Live snapshot sweeps and replica-age regression contracts](mirror-4059-a-sync-recovery.md) | 3782 | test(kanban-sync): pin the live resync sweep's child guards: event window, card outbox hold, child since, children-first [daveey] |
| [Awaited-card wake and unreachable-owner regression contracts](mirror-4059-a-awaited-wake.md) | 3783 | awaited-card wake: pin five #2432 conditions no test reached |
| [Remote task and completion identity contracts](mirror-4059-b-remote-identity-test-contracts.md) | 3784 | Direct `_task_rows` calls exclude the supplied own gateway but include all gateways when omitted; the no-seq snapshot keeps only the remote card; indexed cold reads equal planner order and the fixture proves that order differs from rowid order. |
| [Cron rerun boundaries and expected-stop logging](mirror-4059-b-cron-reruns-and-stop-logging.md) | 3785 | Restart-rerun cases pin awake-versus-asleep classification, a fresh budget for attempts claimed before another rerun, preserved spent budget on deferral, original/replanned/unsaved-slot non-reruns and exact-slot settlement. The gateway case supplies fake owner clocks and requires restart-cut flags with awake 50 and wall 900. |
| [PR-aware Kanban CI waits](mirror-4059-b-pr-ci-waits.md) | 3786 | Parser, park and sweep cases pin conflict refusal, one wake/comment without shared blocker, fresh-head grace, no-run expiry, runner-offline routing for queued runs, quiet null mergeability, moved/closed heads and fail-open API errors. |
| [Scratch archive, retry and empty-tree safety guards](mirror-4059-b-scratch-archive-and-tree-guards.md) | 3787 | Changing directory mtime or completion timestamp after two refusals resets count to one, retry delay to BASE_S and the stored fingerprint to the changed tree. |
| [Scratch archive, retry and empty-tree safety guards](mirror-4059-b-scratch-archive-and-tree-guards.md) | 3788 | A shallow clone retains all objects; a hard-linked tracked file stays whole and restores shared inode/bytes; tampered omitted size or SHA-256 is refused before the target file appears. |
| [Quiet lease preflight and canonical-home clocks](mirror-4059-b-quiet-lease-preflight.md) | 3789 | Gateway tests compare file-preflight reasons with start at zero margin and require the default margin to reject near-stale windows. CLI tests require exit 78, no cooldown/observer and immediate successful retry after fresh idle state, including missing or exploding preflight cases. |
| [Worker admission and recovery test contracts](mirror-4059-b-worker-admission-and-recovery-tests.md) | 3790 | With 16 effective cores, pinned load 24 returns worker_load_pressure before leaked-board occupancy and records no unknown streak; pinned load 1 returns worker_resources_unknown and records it. |
| [Duplicate-completion idempotence and denial parsing](mirror-4059-b-duplicate-completion-idempotence.md) | 3791 | Repeated completion of the body-listed task adds no comments; a task ID only quoted in that body still gets its own completion comment. |
| [Kanban queue clocks, resume receipts and overlap intent](mirror-4059-b-kanban-intent-test-contracts.md) | 3792 | Two uncommented queue sightings retain the original commented_at timestamp whether the comment table is kept or absent; the eventual refresh adds a second comment instead of merely another sighting. |
| [CI cache timeout and duration-fixture reliability](mirror-4059-b-ci-cache-and-duration-tests.md) | 3793 | Workflow structure assertions require both cache lookup and save timeout-minutes values to equal two. |
| [Kanban queue clocks, resume receipts and overlap intent](mirror-4059-b-kanban-intent-test-contracts.md) | 3794 | A None-after receipt survives no runs and update-only requeues, is spent by the first real claim even after requeues, and grants nothing for missing/non-False merge_authorized values. |
| [Scratch archive, retry and empty-tree safety guards](mirror-4059-b-scratch-archive-and-tree-guards.md) | 3795 | Boundary-parameterized memo records enforce the safe epoch limit and retry logging must include the expected ISO UTC timestamp rather than a raw epoch. |
| [Board feed clears this host’s dead local claims](mirror-4059-b-feed-clears-dead-local-claims.md) | 3796 | Primary crash-release feed tests require immediate cleared local claim/PID fields for ready/triage/blocked, immediate retry claimability and triage archiving; no queued release, live worker or still-running feed preserves the claim. Leftover sweep parameterization adds triage. |
| [Board feed clears this host’s dead local claims](mirror-4059-b-feed-clears-dead-local-claims.md) | 3797 | The restart-resume crash fixture requires claim_lock and worker_pid already cleared after feed apply, and the subsequent leftover sweep returns an empty list. |
| [Late session inventory and MCP reload reporting](mirror-4059-b-late-inventory-and-mcp-reload.md) | 3798 | Gateway tests pin reply-before-late-info ordering, a settled empty inventory on early/late build failure and pending-map cleanup. Classic CLI tests pin one-time deferred notices/warnings and no listener for settled replies. MCP reload uses session.info and adopts its full tool inventory. |
| [Home-state placement and shell spelling guards](mirror-4059-b-home-path-placement.md) | 3799 | Home-spelling and pool tests distinguish local state paths/direct state-directory changes from repository-relative paths after later cd; own-worker artifacts retain a local marker; long no-match inputs satisfy bounded scan timings. |
| [Remote task and completion identity contracts](mirror-4059-b-remote-identity-test-contracts.md) | 3800 | With equal legacy numeric run IDs and a later foreign receipt, completion replay still binds this gateway’s own receipt and verifies both completion flags. |
| [Quiet lease preflight and canonical-home clocks](mirror-4059-b-quiet-lease-preflight.md) | 3801 | Canonical relative homes follow changing cwd while absolute keys cache correctly; future prior-boot wall records permit acquisition, but exact zero age raises a full cooldown. |
| [Quiet lease preflight and canonical-home clocks](mirror-4059-b-quiet-lease-preflight.md) | 3802 | Symlink-before-dotdot paths resolve identically for absolute, relative and cached spellings and agree with hermes_home_key; adjacent quiet-test wording is clarified without changed runtime assertions. |
| [Quota-wall fallback for resumed provider-native routes](mirror-4059-b-quota-wall-resumed-overrides.md) | 3803 | A resumed provider-native URL/wire builds the fallback agent, includes the fallback notice and provenance, and does not carry the old endpoint/wire; explicit endpoint/key controls remain refusal cases. |
| [Explicit handshakes in asynchronous regression fixtures](mirror-4059-b-async-test-handshakes.md) | 3804 | The mixed-batch test now waits for its own queued completion and still requires exactly one non-notice batch result with no early child-failure notice. |
| [Scratch archive, retry and empty-tree safety guards](mirror-4059-b-scratch-archive-and-tree-guards.md) | 3805 | Garbage and zero-length refs plus a failing HEAD probe all preserve the unpushed-commits fail-safe rather than proving an unborn repository. |
| [Paused-turn activity follows configured continuation bounds](mirror-4059-b-configured-paused-turn-activity.md) | 3806 | Disabled/expired/exhausted pauses do not annotate turn_live; raised valid windows do; profiles use distinct policy; parser tests pin producer/reader defaults and coercions. |
| [Worker admission and recovery test contracts](mirror-4059-b-worker-admission-and-recovery-tests.md) | 3807 | Parameterized truthy/falsy hold values require stranded listing to agree with the actual pickup eligibility gate. |
| [CI cache timeout and duration-fixture reliability](mirror-4059-b-ci-cache-and-duration-tests.md) | 3808 | Parsed slow-case lines may name only the fixture cases, must include the sleeper at least 0.6 seconds, and every reported case must meet the 0.4-second reporting floor. |
| [Settled chat-link parity across asynchronous stores](mirror-4059-b-chat-links-settle.md) | 3809 | Settling tests require lag to become in_flight, persistent differences to remain unclean, early clean exit, later/reappearing entries, both diff directions, bounded final sleep, configured poll cadence and CLI raw/default behavior. |
| [Settled chat-link parity across asynchronous stores](mirror-4059-b-chat-links-settle.md) | 3810 | Unreadable samples neither clear nor confirm their board entries; last-read unreadability prevents clean; a difference first visible after unreadability is persistent until a readable absence clears it. |
| [Kanban queue clocks, resume receipts and overlap intent](mirror-4059-b-kanban-intent-test-contracts.md) | 3811 | A moved marker next to one-sided enumeration returns None from both enumerated and own_distinct. |
| [Worker admission and recovery test contracts](mirror-4059-b-worker-admission-and-recovery-tests.md) | 3812 | Hold/registry uncertainty keeps repairs open; live Board leases preserve owner work, expired target leases still wait, moved/imported/repinned targets resolve as appropriate; no-card repairs and racing claims stay open, and resolve exceptions do not stop detection. |
| [Settled chat-link parity across asynchronous stores](mirror-4059-b-chat-links-settle.md) | 3813 | An owner’s only other carried card cannot hide this gateway’s missing ledger obligation, whether that other card belongs to a different gateway or profile; profile-board counts stay empty. |
| [Desktop WebSocket dial limits follow slow-link mode](mirror-4059-b-desktop-slow-link-dial.md) | 3814 | The slow-link integration file drives the real tracker and shared client with fake sockets/timers: normal/local 15s versus remote slow 60s, 20s successful open, live/dead SSH strikes, answer reset, malformed inputs and IPC/drop-scope behavior. The dial file checks HermesGateway forwarding, typed timeout reporting, non-timeout preservation and absent bridge defaults. |
| [Quiet lease preflight and canonical-home clocks](mirror-4059-b-quiet-lease-preflight.md) | 3815 | With an otherwise idle window, a drain/admission hold or unreadable hold exits preflight with no active lease, cooldown or spawned observer. |
| [Lifecycle guard follows consecutive shell group closers](mirror-4059-b-lifecycle-glued-closers.md) | 3816 | Parameterized scan cases cover consecutive braces and mixed if/loop/case closers, nested output pipelines and Python stdin; clean later commands, quoted/data uses and unrelated redirects remain allowed. |
| [Explicit handshakes in asynchronous regression fixtures](mirror-4059-b-async-test-handshakes.md) | 3817 | The dock fixture uses rc-free bash and still asserts the idle starting label, process block ordering, failure exit display, viewport width and retirement behavior. |
| [Desktop resubmits after an explicit stale-runtime refusal](mirror-4059-b-desktop-stale-runtime-resubmit.md) | 3818 | Renderer roll fixtures require exactly one admitted identityless or identified turn after resume, retaining a supplied submission ID; ambiguous failures are not automatically resent and explicit stale-runtime retries remain safe. Sidecar tests pin exact pre-admission 4001 and capability rejection before handlers. |
| [Home-state placement and shell spelling guards](mirror-4059-b-home-path-placement.md) | 3819 | Added hub-read-root controls keep home placement for split-dot traversal, split cd and CR/newline forms rather than incorrectly clearing the root marker. |
| [Home-state placement and shell spelling guards](mirror-4059-b-home-path-placement.md) | 3820 | An escaped backslash before newline does not join commands and hide a later continued cd; the added control must still pin. |
| [Explicit handshakes in asynchronous regression fixtures](mirror-4059-b-async-test-handshakes.md) | 3821 | The live-reader test requires a HEAD commit answer before reclaim, then still asserts reclaim refuses and index.lock remains while Git is working. |
| [Loop accounting survives interrupted passes and thread churn](mirror-4059-b-loop-accounting-lifecycle.md) | 3822 | Tests require two lease runs/one error after a missing beat, nonnegative fork depth with later outer CPU counted, no reused-TID inheritance while exited CPU is retained, config-unavailable defaults/start survival and interval lookup outside the accounting lock. |
| [PR-aware Kanban CI waits](mirror-4059-b-pr-ci-waits.md) | 3823 | Ambiguous distinct SHA hints still wake on current-head conflict; one old SHA treats a moved head as ok; parser rows distinguish repeated identical SHAs from multiple distinct candidates and explicit/ref precedence. |
| [Cron rerun boundaries and expected-stop logging](mirror-4059-b-cron-reruns-and-stop-logging.md) | 3824 | Shutdown wins over a cancel marker with one no-traceback shutdown warning, no generic failure or requested-stop warnings, lifecycle-interrupt last_failure and no failure streak. |
| [Cron rerun boundaries and expected-stop logging](mirror-4059-b-cron-reruns-and-stop-logging.md) | 3825 | Requested/shutdown prefixes still produce failed admission/results without an ERROR traceback; an unrelated error retains ERROR with exception info. |
| [Worker admission and recovery test contracts](mirror-4059-b-worker-admission-and-recovery-tests.md) | 3826 | A set_workspace_path OSError prevents spawn/capacity use, restores ready/no claim/current run, ends the run as released and records infrastructure metadata without consecutive task failures. |
| [Duplicate-completion idempotence and denial parsing](mirror-4059-b-duplicate-completion-idempotence.md) | 3827 | New denial phrases containing not as duplicate are excluded by the existing parser parameterization, while positive and other denial cases remain in the same suite. |
| [Quota-wall fallback for resumed provider-native routes](mirror-4059-b-quota-wall-resumed-overrides.md) | 3828 | Sessionless build cases preserve the quota refusal and selected route without not-deferred warnings, including repeated calls. |
| [Managed-stub relaunch attestation and read-only quiet check](mirror-4059-b-quiet-code-attestation.md) | 3829 | Real subprocess relaunch cases cover spaced interpreter/current stub, trace-only handoff env and strict refusal of altered flags/argv/root/prefix/body/literals. Launcher-event tests enforce one-shot exact frame/code. Quiet tests pin direct/relaunch agreement, missing ledger pending, cheap latched snapshots, read-only check with unchanged files/epoch, stale-window/refusal parity, unavailable gateway and usage rejection. Existing route/membership fixtures are adapted to provide the captured ledger. |
| [Managed-stub relaunch attestation and read-only quiet check](mirror-4059-b-quiet-code-attestation.md) | 3830 | No test file changes in this mail; the only changed assertion surface is CI classifier membership for the shared launch template. Upgrade-lane execution is not established by this documentation pass. |
| [Late session inventory and MCP reload reporting](mirror-4059-b-late-inventory-and-mcp-reload.md) | 3831 | A successful reload followed by session.info timeout prints success with unknown count, preserves unknown inventory and makes no false available-tools claim; reload.mcp timeout still prints failure and performs no count read. |
| [Home-state placement and shell spelling guards](mirror-4059-b-home-path-placement.md) | 3832 | State-path home-form cases cover fd-redirected cd/pushd and Unicode text before the relative marker, asserting the queue class, normalized path and capability label. |
| [Process-wide host change feed for subscribed loops](mirror-4059-b-host-change-feed.md) | 3833 | Feed tests cover first baseline, topic-specific/thread/async wakes, deadlines, coalescing, unknown probes, topic appearance/disappearance, stop/close, probe accounting, replica cursor-only keys, state external commits, default counter inventory and pytest singleton non-autostart. |
| [Idle process-output tail backoff](mirror-4059-b-process-output-idle-backoff.md) | 3834 | Scripted empty reads require the 0.2/0.4/0.8/1.6/2.0 capped wait sequence; late output is ingested on the next poll and resets the wait; a real finish-event test joins the reader promptly. |
| [Cron list memoizes normalized job records](mirror-4059-b-cron-list-memo.md) | 3835 | Repeated lists do not reparse, rename/add/delete and inode replacement refresh immediately, caller mutations cannot corrupt nested records, execution metadata stays live, profile stores isolate and memo hits increment accounting skips. |
| [Credential-scoped web delta frame memo](mirror-4059-b-web-delta-frame-memo.md) | 3836 | ASGI polling fixtures decode deltas and require one encode for repeated unchanged requests, new encode on changed body, credential/base isolation, whole-body fallback after base eviction, byte/entry bounds including cached None and accounting skips. |
| [Done replica cards remain pending until verified release](mirror-4059-b-done-pending-release.md) | 3837 | Replica link/parity tests keep done_pending_release counted and unfinished until release, including never-verifying cards; missing/statusless/archived rows remain unknown. Activity revision and remote-outcome tests keep observing the release transition. |
| [Done replica cards remain pending until verified release](mirror-4059-b-done-pending-release.md) | 3838 | The closed-card/profile reader expects done_pending_release with released=False for the unreleased done row while retaining profile filtering and released/archived controls. |
| [Scratch archive, retry and empty-tree safety guards](mirror-4059-b-scratch-archive-and-tree-guards.md) | 3839 | A silent failing for-each-ref subprocess still returns the unpushed-commits fail-safe, even when both output streams are empty. |
| [Retire host loops before a replaced backend finishes draining](mirror-4059-c-host-loop-retirement.md) | 3840 | superseded/stale backend: release host-loop leases at once, start no new wakes |
| [Lifecycle guard: shell groups, cat relays and numeric substitutions](mirror-4059-c-lifecycle-shell-dataflow.md) | 3841 | lifecycle guard tests: a closed group's redirection ends at the next operator |
| [Lifecycle guard: shell groups, cat relays and numeric substitutions](mirror-4059-c-lifecycle-shell-dataflow.md) | 3842 | lifecycle guard: a bare `cat` relays its stdin feed; a subshell's redirection keeps its output |
| [Lifecycle guard: shell groups, cat relays and numeric substitutions](mirror-4059-c-lifecycle-shell-dataflow.md) | 3843 | lifecycle guard: move _split_logical_lines to cron/lifecycle_guard_lines.py (FILE_LINES offset) |
| [Requeue proven owner persistence failures without spending the card budget](mirror-4059-c-owner-persistence-requeue.md) | 3844 | Requeue a mid-turn unreachable-owner persistence failure without charging the card |
| [Session-scoped inventory waits and reply publication ordering](mirror-4059-c-session-inventory.md) | 3845 | classic CLI: a pending-inventory wait never prints a left session's startup notices |
| [Lifecycle guard: shell groups, cat relays and numeric substitutions](mirror-4059-c-lifecycle-shell-dataflow.md) | 3846 | lifecycle guard: `$(( $(date +%s) ... ))` is no shell sink |
| [Compaction anchors only intent whose turn is still live](mirror-4059-c-compaction-live-intent.md) | 3847 | fix(compression): never anchor a request whose turn already ended |
| [Kanban live rebuilds preserve concurrent writes and cold-start at the feed head](mirror-4059-c-sync-live-rebuild.md) | 3848 | kanban sync: the live resync sweep spares rows whose outbox op existed when the rebuild began |
| [Kanban live rebuilds preserve concurrent writes and cold-start at the feed head](mirror-4059-c-sync-live-rebuild.md) | 3849 | kanban sync: the live resync sweep bounds task_links to links present when the rebuild began |
| [Kanban live rebuilds preserve concurrent writes and cold-start at the feed head](mirror-4059-c-sync-live-rebuild.md) | 3850 | kanban sync: the live resync sweep spares a link whose child card's links changed during the rebuild |
| [Kanban live rebuilds preserve concurrent writes and cold-start at the feed head](mirror-4059-c-sync-live-rebuild.md) | 3851 | kanban sync: the 410 sweep test backdates its rows before the rebuild |
| [Skip unchanged notifier boards without suppressing recovery](mirror-4059-c-notifier-board-gate.md) | 3852 | kanban notifier: skip an unchanged board's collect pass on a full recovery key (H1) |
| [Skip unchanged notifier boards without suppressing recovery](mirror-4059-c-notifier-board-gate.md) | 3853 | kanban notifier gate: a probe RuntimeError falls back to the writable open |
| [Portable worktree requirements and home-relative placement boundaries](mirror-4059-c-workspace-and-home-placement.md) | 3854 | kanban workspace_source: a portable worktree card's stored host_repo no longer blocks its move or pickup |
| [Independent exact-head review for fork-authored upstream syncs](mirror-4059-c-upstream-sync-review.md) | 3855 | upstream sync gate: require an independent exact-head review for a fork-authored sync |
| [Reuse background read handles with generation-safe retirement](mirror-4059-c-kept-readers.md) | 3856 | kanban/state.db: kept read connections for background readers (card I) |
| [Scheduled macOS test lane yields to waiting runner work](mirror-4059-c-macos-scheduled-tests.md) | 3857 | ci: scheduled macOS OS-marked test lane on a self-hosted macOS runner (non-required) |
| [Quiet admission preflight and fail-closed dispatch test pins](mirror-4059-c-quiet-preflight.md) | 3858 | test(quiet): pin literal hold fields + non-dataclass fail-closed in all_quiet_deferred |
| [Scratch archiving, Board custody discovery and explicit preservation pins](mirror-4059-c-scratch-archive-custody.md) | 3859 | scratch archive: archive the shapes it refused forever (promisor tags, bare repos, linked worktrees, fifos) |
| [Earlier-run overlap and precise duplicate-language recognition](mirror-4059-c-overlap-and-duplicate-tests.md) | 3860 | kanban overlap: the key-family exemption covers a creator's own cards only from its current run |
| [Quiet admission preflight and fail-closed dispatch test pins](mirror-4059-c-quiet-preflight.md) | 3861 | quiet preflight parity test: a host-file update hold refuses like start (pin #2591 M2c) |
| [Portable worktree requirements and home-relative placement boundaries](mirror-4059-c-workspace-and-home-placement.md) | 3862 | test: pin canonical_home tilde expansion and relative bytes spelling |
| [Profile-specific state topics and race-safe subscription seeding](mirror-4059-c-profile-state-change-feed.md) | 3863 | host_change_feed: resolve the state topic per subscriber profile; detach the probe thread |
| [Links-diff raw reads and unreadable-board settling](mirror-4059-c-links-diff-tests.md) | 3864 | profile-board links-diff test: settle=0 so the CLI read is one raw read, not a 90 s real-clock window |
| [PR conflict detection tests exercise either GitHub conflict field](mirror-4059-c-pr-conflict-tests.md) | 3865 | test: pin each half of the PR conflict check in kanban_ci_progress |
| [Earlier-run overlap and precise duplicate-language recognition](mirror-4059-c-overlap-and-duplicate-tests.md) | 3866 | test: dup-detector body occurrence match needs the leading newline and the trailing space |
| [Quiet admission preflight and fail-closed dispatch test pins](mirror-4059-c-quiet-preflight.md) | 3867 | quiet-hold: ask quiet-observe-check before taking the claim-quiet lease |
| [Portable worktree requirements and home-relative placement boundaries](mirror-4059-c-workspace-and-home-placement.md) | 3868 | test(kanban): pin case-insensitive relative .HERMES/ hit naming in _state_path |
| [Cron delivery test synchronization and racy queue stamps](mirror-4059-c-cron-delivery-watch.md) | 3869 | deflake cron delivery tests: wait for the loop thread to be running before delivering |
| [Cron delivery test synchronization and racy queue stamps](mirror-4059-c-cron-delivery-watch.md) | 3870 | queue watch: distrust a stamp seen within its mtime granule (deflake settle test) |
| [Pending notices share a boundary turn without crossing category or human input](mirror-4059-c-pending-notice-turns.md) | 3871 | fix(gateway): pending notices ride the turn that starts at a boundary |
| [Pending notices share a boundary turn without crossing category or human input](mirror-4059-c-pending-notice-turns.md) | 3872 | test(gateway): released notifier wakes ride one turn after a core drain |
| [Pending notices share a boundary turn without crossing category or human input](mirror-4059-c-pending-notice-turns.md) | 3873 | fix(gateway): fold only the head's notice category; a fold error never fails the head |
| [Lifecycle guard: shell groups, cat relays and numeric substitutions](mirror-4059-c-lifecycle-shell-dataflow.md) | 3874 | lifecycle guard tests: pin `--`/lone `-` option end and the rcfile-scan bound at a clustered -c payload |
| [Walled Desktop route refusal test pins provider ownership and logs](mirror-4059-c-walled-provider-tests.md) | 3875 | test: Desktop wall guard refuses a known other provider's endpoint and logs the refusal reason |
| [Loop accounting atomic snapshots and CPU attribution tests](mirror-4059-c-loop-accounting-tests.md) | 3876 | test(loop_accounting): pin atomic write, live-pid prune, writer prune, stop flush, record(), procfs CPU fields |
| [Kanban live rebuilds preserve concurrent writes and cold-start at the feed head](mirror-4059-c-sync-live-rebuild.md) | 3877 | kanban sync: a cold primary live file rebuilds from a snapshot; the failed-result observer reads the board's sync mode |
| [Kanban live rebuilds preserve concurrent writes and cold-start at the feed head](mirror-4059-c-sync-live-rebuild.md) | 3878 | kanban sync: a cold live rebuild peeks the first page instead of applying it |
| [Pending notices share a boundary turn without crossing category or human input](mirror-4059-c-pending-notice-turns.md) | 3879 | deflake resume live-text snapshot: a late session.info may follow the snapshot |
| [Session-scoped inventory waits and reply publication ordering](mirror-4059-c-session-inventory.md) | 3880 | test: pin the inventory print-once lock and shared-build hold release |
| [Portable worktree requirements and home-relative placement boundaries](mirror-4059-c-workspace-and-home-placement.md) | 3881 | placement: home-relative span ends only at a cd of this shell; cd ~/.hermes && cd hermes-agent is the code tree |
| [Normalize crash errors before the fingerprint width limit](mirror-4059-c-crash-fingerprint.md) | 3882 | Fingerprint crash errors after PID normalization, not before the 80-char cut |
| [Earlier-run overlap and precise duplicate-language recognition](mirror-4059-c-overlap-and-duplicate-tests.md) | 3883 | kanban overlap tests: pin dotted re-issue counters, "tries", every join word and judging verb |
| [Scheduled macOS test lane yields to waiting runner work](mirror-4059-c-macos-scheduled-tests.md) | 3884 | macos_os_tests_gate: read every page of open runs before deciding |
| [Background process output excludes the login-shell startup banner](mirror-4059-c-background-start-marker.md) | 3885 | background jobs: drop the login shell's rc output before the command (start marker) |
| [Revision-gated reclaim sub-sweeps keep clocks and process death live](mirror-4059-c-dispatcher-reclaim-gates.md) | 3886 | kanban dispatcher: gate reclaim sub-sweeps on the content revision or a data-derived due time (G2) |
| [Revision-gated reclaim sub-sweeps keep clocks and process death live](mirror-4059-c-dispatcher-reclaim-gates.md) | 3887 | kanban db: skip task_runs partial indexes until a legacy TEXT-PK table is rebuilt (G2) |
| [Revision-gated reclaim sub-sweeps keep clocks and process death live](mirror-4059-c-dispatcher-reclaim-gates.md) | 3888 | kanban release_on_feed: pin the four guards that survived removal (#2573 test gap) |
| [Links-diff raw reads and unreadable-board settling](mirror-4059-c-links-diff-tests.md) | 3889 | test(kanban): pin settle's "at least one read could read the board" rule |
| [Desktop read sharing isolates native grants and invalidates on WS writes](mirror-4059-c-desktop-native-read-sharing.md) | 3890 | Desktop read sharing: key on the native grant; WS-RPC session writes start a new generation |
| [Scratch archiving, Board custody discovery and explicit preservation pins](mirror-4059-c-scratch-archive-custody.md) | 3891 | fix(kanban): close scratch custody gaps (archived rows, Board-claim receipts, preserve list) |
| [Scratch archiving, Board custody discovery and explicit preservation pins](mirror-4059-c-scratch-archive-custody.md) | 3892 | fix(kanban): scratch preserve pin also guards detached-trash sweep; non-mapping kanban section fails closed |
| [Profile-specific state topics and race-safe subscription seeding](mirror-4059-c-profile-state-change-feed.md) | 3893 | host_change_feed: keep a state seed subscribed during an in-flight probe |
| [Requeue proven owner persistence failures without spending the card budget](mirror-4059-c-owner-persistence-requeue.md) | 3894 | Requeue an exhausted-resend owner stall (worker_receipt_timeout / owner_link_closed) without charging the card |
| [Independent exact-head review for fork-authored upstream syncs](mirror-4059-c-upstream-sync-review.md) | 3895 | upstream sync gate: trust only the real upstream, refuse ship-tool faults, drop HERMES_UPSTREAM_SYNC_IMPL_TASK |
| [Checkpoint restart waits, pause bounds, and replay retirement](mirror-4059-d-restart-replay.md) | 3896 | Adds the short checkpoint settle for managed-only update waits. |
| [Checkpoint restart waits, pause bounds, and replay retirement](mirror-4059-d-restart-replay.md) | 3897 | Restricts the short wait to unfenced boolean checkpoint/cron-cut intents. |
| [Checkpoint restart waits, pause bounds, and replay retirement](mirror-4059-d-restart-replay.md) | 3898 | Pins TUI pause freshness and malformed configuration bounds. |
| [Shell-aware home-path placement regression coverage](mirror-4059-d-placement.md) | 3899 | Rejects false cd-home matches with operands after redirections. |
| [Checkpoint restart waits, pause bounds, and replay retirement](mirror-4059-d-restart-replay.md) | 3900 | Keeps quota reset timing on code skew without host-loop retirement. |
| [Change-proportional cron execution list reads](mirror-4059-d-cron-execution-reader.md) | 3901 | Memoizes cron execution list rows on a kept reader data version. |
| [Off-loop boot recovery and CLI session-switch ordering](mirror-4059-d-gateway-boot-cli.md) | 3902 | Moves boot owner lookup off-loop and batches stale-route end reasons. |
| [Kept-reader eviction and stale-generation tests](mirror-4059-d-kept-readers.md) | 3903 | Explicitly closes cap-evicted kept readers without closing borrowers early. |
| [Off-loop boot recovery and CLI session-switch ordering](mirror-4059-d-gateway-boot-cli.md) | 3904 | Publishes CLI current session before switch listeners. |
| [Lifecycle guard redirections and arithmetic shell sinks](mirror-4059-d-lifecycle-shell.md) | 3905 | Pins closed-group output reset across shell operators. |
| [Lifecycle guard redirections and arithmetic shell sinks](mirror-4059-d-lifecycle-shell.md) | 3906 | Preserves variable/numeric descriptor redirections in lifecycle scanning. |
| [Kanban notifier, overlap, sync, and refusal regression contracts](mirror-4059-d-kanban-sync-gates.md) | 3907 | Pins conservative notifier keys and TUI pending-kind filtering. |
| [Desktop submit refusal guards and slow-link connect wiring](mirror-4059-d-desktop-refusal-link.md) | 3908 | Pins gateway error class/code guards against ambiguous submit resend. |
| [Shell-aware home-path placement regression coverage](mirror-4059-d-placement.md) | 3909 | Pins placement descriptor digits, path offsets, and independent spans. |
| [Kanban notifier, overlap, sync, and refusal regression contracts](mirror-4059-d-kanban-sync-gates.md) | 3910 | Pins same-family overlap creator and unknown-time exemptions. |
| [Per-task Kanban change cursors on boards and shadows](mirror-4059-d-task-change-cursor.md) | 3911 | Adds per-task indexed change cursors with conservative full-read fallback. |
| [Per-task Kanban change cursors on boards and shadows](mirror-4059-d-task-change-cursor.md) | 3912 | Uses conflict-policy-safe UPSERT for task-change marks. |
| [Per-task Kanban change cursors on boards and shadows](mirror-4059-d-task-change-cursor.md) | 3913 | Creates task-change tables with rowid for generic snapshots. |
| [Desktop submit refusal guards and slow-link connect wiring](mirror-4059-d-desktop-refusal-link.md) | 3914 | Requires tunnel liveness registration and tests main/preload connect wiring. |
| [Exact-head upstream review discovery and CI gate boundary tests](mirror-4059-d-upstream-review-ci.md) | 3915 | Pins bounded gh reads and fail-open scheduled test selection. |
| [Kanban notifier, overlap, sync, and refusal regression contracts](mirror-4059-d-kanban-sync-gates.md) | 3916 | Pins resync held-card protection and initial link sweep bounds. |
| [Kanban notifier, overlap, sync, and refusal regression contracts](mirror-4059-d-kanban-sync-gates.md) | 3917 | Pins stop prefixes, ambiguous PR SHA hints, and negated duplicates. |
| [Delivery queue timestamp race settling](mirror-4059-d-delivery-watch.md) | 3918 | Settles future delivery timestamps after a first-sight recheck. |
| [Shell-aware home-path placement regression coverage](mirror-4059-d-placement.md) | 3919 | Pins backslash-CR as distinct from shell line continuation. |
| [Shell-aware home-path placement regression coverage](mirror-4059-d-placement.md) | 3920 | Pins uppercase CD prose and all home-relative pool artifact hits. |
| [Off-loop boot recovery and CLI session-switch ordering](mirror-4059-d-gateway-boot-cli.md) | 3921 | Waits for completed SIGUSR2 diagnostics before child shutdown. |
| [Lifecycle guard redirections and arithmetic shell sinks](mirror-4059-d-lifecycle-shell.md) | 3922 | Detects substitution output anywhere inside arithmetic shell contexts. |
| [Host change-feed contracts and producer-admission/PID read gates](mirror-4059-d-change-feed-admission.md) | 3923 | Pins change-feed topic, waiter, UNKNOWN, and thread contracts. |
| [Host change-feed contracts and producer-admission/PID read gates](mirror-4059-d-change-feed-admission.md) | 3924 | Pins independent state-probe failure handling and passive threads. |
| [Quiet admission and relaunch attestation boundary tests](mirror-4059-d-quiet-attestation.md) | 3925 | Pins quiet code-ledger and relaunch-prefix attestation refusals. |
| [Exact-head upstream review discovery and CI gate boundary tests](mirror-4059-d-upstream-review-ci.md) | 3926 | Models trusted upstream identity in local updater fixtures. |
| [Scratch archive shape and custody regression tests](mirror-4059-d-scratch-archive.md) | 3927 | Pins archive Git-shape, source-index, and external-history boundaries. |
| [Shared account tool-store borrowing for fake-home runners](mirror-4059-d-account-tool-store.md) | 3928 | Shares account tool-store borrowing across fake-home shell runners. |
| [Kanban notifier, overlap, sync, and refusal regression contracts](mirror-4059-d-kanban-sync-gates.md) | 3929 | Reads only complete records from the live sync timing log fixture. |
| [Host change-feed contracts and producer-admission/PID read gates](mirror-4059-d-change-feed-admission.md) | 3930 | Gates producer-admission reads and memoizes profile PID probes. |
| [Host change-feed contracts and producer-admission/PID read gates](mirror-4059-d-change-feed-admission.md) | 3931 | Releases observers on lifecycle changes and fences remembered keys. |
| [Kanban notifier, overlap, sync, and refusal regression contracts](mirror-4059-d-kanban-sync-gates.md) | 3932 | Pins bound legacy/modern midrun owner-refusal receipt proof. |
| [Exact-head upstream review discovery and CI gate boundary tests](mirror-4059-d-upstream-review-ci.md) | 3933 | Pins the final supported CI queue page before saturation decisions. |
| [Checkpoint restart waits, pause bounds, and replay retirement](mirror-4059-d-restart-replay.md) | 3934 | Pins host-loop retirement races and repeated skew confirmation. |
| [Lifecycle guard redirections and arithmetic shell sinks](mirror-4059-d-lifecycle-shell.md) | 3935 | Pins date rebindings that disable the numeric-producer exemption. |
| [Process output, compression anchors, and memo regression tests](mirror-4059-d-test-harness-memos.md) | 3936 | Pins compression anchor copies without source-request mutation. |
| [Delivery queue timestamp race settling](mirror-4059-d-delivery-watch.md) | 3937 | Pins newest queue mtime selection and inclusive granule boundaries. |
| [Process output, compression anchors, and memo regression tests](mirror-4059-d-test-harness-memos.md) | 3938 | Pins split process start markers across hold-buffer overflow. |
| [Exact-head upstream review discovery and CI gate boundary tests](mirror-4059-d-upstream-review-ci.md) | 3939 | Pins octopus and unparsed remerge output as review-required. |
| [Process output, compression anchors, and memo regression tests](mirror-4059-d-test-harness-memos.md) | 3940 | Pins idle-finish wakeups and jobs/delta-frame memo boundaries. |
| [Process output, compression anchors, and memo regression tests](mirror-4059-d-test-harness-memos.md) | 3941 | Synchronizes idle-finish regression with actual wait entry. |
| [Quiet admission and relaunch attestation boundary tests](mirror-4059-d-quiet-attestation.md) | 3942 | Pins inconsistent quiet admission and unavailable preflight behavior. |
| [Pending-fold admission custody boundary tests](mirror-4059-d-pending-fold.md) | 3943 | Pins pending-fold custody, replay idempotency, and generation fences. |
| [Kanban notifier, overlap, sync, and refusal regression contracts](mirror-4059-d-kanban-sync-gates.md) | 3944 | Pins completed sweep-key failure invalidation and unreadable due reads. |
| [Kanban notifier, overlap, sync, and refusal regression contracts](mirror-4059-d-kanban-sync-gates.md) | 3945 | Pins cold rebuild repair despite an earlier completed repair window. |
| [Persistent delta bases and fenced Desktop cache writes](mirror-4059-d-delta-persistence.md) | 3946 | Persists scoped delta bases across client/backend restarts. |
| [Persistent delta bases and fenced Desktop cache writes](mirror-4059-d-delta-persistence.md) | 3947 | Retains hot disk bases without rewriting unchanged polled copies. |
| [Persistent delta bases and fenced Desktop cache writes](mirror-4059-d-delta-persistence.md) | 3948 | Fences async delta-base publication against delete, replacement, and quit. |
| [Kept-reader eviction and stale-generation tests](mirror-4059-d-kept-readers.md) | 3949 | Pins kept-reader stale generations, transactions, and shutdown. |
| [Exact-head upstream review discovery and CI gate boundary tests](mirror-4059-d-upstream-review-ci.md) | 3950 | Discovers exact-head off-key review candidates without granting approval. |
| [Exact-head upstream review discovery and CI gate boundary tests](mirror-4059-d-upstream-review-ci.md) | 3951 | Preserves terminal rejection across every off-key review candidate. |
| [Worker receipt retry boundaries](mirror-4059-e-receipt-retry.md) | 3952 | Test sent-only 1011 receipt recovery and owner-fault latch |
| [Desktop write invalidation across preload and IPC](mirror-4059-e-desktop-write-bridge.md) | 3953 | Exercise Desktop write invalidation across preload and IPC |
| [Profile-isolated boot recovery contracts](mirror-4059-e-boot-recovery.md) | 3954 | test boot recovery route filtering and batched profile isolation |
| [Quiet-board claim and provider-blocker clocks](mirror-4059-e-blocker-clock-boundaries.md) | 3955 | test quiet local claims and blocker lookback scope expiry |
| [Update checkpoint intent reread contracts](mirror-4059-e-checkpoint-intent.md) | 3956 | test(gateway): pin checkpoint intent type and reread semantics |
| [PID memo bounds and producer-sweeper cleanup](mirror-4059-e-pid-and-sweeper-lifecycle.md) | 3957 | Test PID memo retention and sweeper shutdown cleanup |
| [Fork-safe nested loop CPU accounting](mirror-4059-e-fork-loop-accounting.md) | 3958 | loop_accounting: pin the exited-tid fold paths; a fork child books pre-fork passes from the fork, not negative CPU |
| [Fork-safe nested loop CPU accounting](mirror-4059-e-fork-loop-accounting.md) | 3959 | fix(loop-accounting): preserve inherited nesting after fork |
| [Retirement-reader failures retain replay reset deadlines](mirror-4059-e-retirement-reader.md) | 3960 | Pin replay reset preservation on retirement reader failure |
| [Kanban change cursor identity and REPLACE contract](mirror-4059-e-task-change-cursor.md) | 3961 | test cursor invalidation on shm identity change |
| [Cron execution-reader and restart-safe claim liveness](mirror-4059-e-cron-claim-and-readers.md) | 3962 | test(cron): pin populated execution reader contracts |
| [Cron execution-reader and restart-safe claim liveness](mirror-4059-e-cron-claim-and-readers.md) | 3963 | fix(cron): a restart-safe worker still running a fire keeps its claim live after the claiming gateway exits |
| [Resync link preservation at the inclusive boundary](mirror-4059-e-resync-link-boundary.md) | 3964 | resync sweep: document link preservation trade and pin inclusive boundary |
| [Upstream review selection, approval guards and blob recovery](mirror-4059-e-upstream-review.md) | 3965 | Select updater integration consumers for copied upstream gate changes |
| [Per-build provider-pool and auth-store memo](mirror-4059-e-picker-build-memo.md) | 3966 | model.options: one pool and auth-store read per provider per picker build (card J rpc_dispatch) |
| [Per-build provider-pool and auth-store memo](mirror-4059-e-picker-build-memo.md) | 3967 | fix(picker): retain auth loader fallback when version stat fails |
| [Direct activation borrowing controls](mirror-4059-e-activation-borrowing.md) | 3968 | test(activation): cover direct borrowing overrides and stderr |
| [Review workspace archival does not reopen dependency history](mirror-4059-e-review-workspace-archive.md) | 3969 | Ignore review workspace archival in dependency continuation history |
| [Enabled-credential walls and pre-reference routing owner](mirror-4059-e-routing-disabled-and-owner.md) | 3970 | Exclude disabled credentials from quota and auth-wall classification |
| [Home-state placement with shell redirects](mirror-4059-e-home-redirect-placement.md) | 3971 | test(placement): pin single-quoted redirect targets |
| [Folded notices settle once across checkpoints and preserve notes](mirror-4059-e-folded-notices.md) | 3972 | fold-pending: a turn paused for an update settles its folded notices; resume no longer re-delivers them |
| [Kanban change cursor identity and REPLACE contract](mirror-4059-e-task-change-cursor.md) | 3973 | test(kanban): reject in-memory change cursors |
| [Commit-keyed watcher stores and subscription signatures](mirror-4059-e-watch-store-gates.md) | 3974 | tui_gateway: watch signatures and the kanban poll read only on change (card J part 2) |
| [Commit-keyed watcher stores and subscription signatures](mirror-4059-e-watch-store-gates.md) | 3975 | tui_gateway.watch_store: release on close_all_under and on a new WAL generation (card J part 2) |
| [Commit-keyed watcher stores and subscription signatures](mirror-4059-e-watch-store-gates.md) | 3976 | fix(watcher): keep released stores transient until an owner returns |
| [Upstream review selection, approval guards and blob recovery](mirror-4059-e-upstream-review.md) | 3977 | test(ci): cover upstream identity and ship import failures |
| [Upstream review selection, approval guards and blob recovery](mirror-4059-e-upstream-review.md) | 3978 | fix(ci): guard every approving review receipt |
| [Claim release compares lock and worker independently](mirror-4059-e-claim-cas.md) | 3979 | Test release claim CAS fields independently |
| [Race-free Chrome startup in real-profile login tests](mirror-4059-e-chrome-start-fixture.md) | 3980 | deflake real-profile login live tests: read Chrome's CDP port from DevToolsActivePort, 120 s start deadline |
| [Workspace source custody and sibling materialization](mirror-4059-e-source-materialization.md) | 3981 | Fix workspace source event custody and isolate materialized trees |
| [Bounded queue pagination and confirmation scans](mirror-4059-e-ci-queue-pagination.md) | 3982 | confirm mini queue scans and continue short nonterminal pages |
| [Kanban change cursor identity and REPLACE contract](mirror-4059-e-task-change-cursor.md) | 3983 | Document cross-task REPLACE cursor precondition |
| [Submitter records witnessed owner-restart exit](mirror-4059-e-outer-restart-receipt.md) | 3984 | Kanban submitter books its own exit 76 when no owner can |
| [Origin-scoped replica projection reuse](mirror-4059-e-replica-profile-states.md) | 3985 | Keep replica projections per origin scope and share slim decoded stages |
| [Origin-scoped replica projection reuse](mirror-4059-e-replica-profile-states.md) | 3986 | Exercise replica scope cache reset and cross-origin controls |
| [Home-state placement with shell redirects](mirror-4059-e-home-redirect-placement.md) | 3987 | placement: recognize home redirects and redirected code-tree transitions |
| [Quiet-board claim and provider-blocker clocks](mirror-4059-e-blocker-clock-boundaries.md) | 3988 | kanban blocker probe lane: recheck routes of an open provider-scoped blocker on a 300 s cadence (G2 follow-up) |
| [Enabled-credential walls and pre-reference routing owner](mirror-4059-e-routing-disabled-and-owner.md) | 3989 | fix(gateway): bind routing owner before reference expansion |
| [Upstream review selection, approval guards and blob recovery](mirror-4059-e-upstream-review.md) | 3990 | Recover refused partial-clone blobs for upstream classification |
| [Upstream review selection, approval guards and blob recovery](mirror-4059-e-upstream-review.md) | 3991 | Adapt unparsed remerge fixture to blob recovery Git seam |
| [Transactional per-session change cursor](mirror-4059-e-session-change-cursor.md) | 3992 | state.db: per-session change cursor (session_change + triggers) |
| [Loop-command traceback diagnostics survive capture](mirror-4059-e-loop-hang-diagnostics.md) | 3993 | tests: capture blocked stacks in loop command CI hang |
| [Loop-command traceback diagnostics survive capture](mirror-4059-e-loop-hang-diagnostics.md) | 3994 | tests: preserve hang diagnostics across pytest capture |
| [Fork-safe nested loop CPU accounting](mirror-4059-e-fork-loop-accounting.md) | 3995 | Test nonzero fork clock baselines for inherited passes |
| [Fork-safe nested loop CPU accounting](mirror-4059-e-fork-loop-accounting.md) | 3996 | test(loop-accounting): exercise nonzero baselines through real fork |
| [Quiet dispatcher capability, supervisor and wake deadlines](mirror-4059-e-dispatcher-due-gates.md) | 3997 | Memoize capability refresh deadlines on quiet dispatcher ticks |
| [Quiet dispatcher capability, supervisor and wake deadlines](mirror-4059-e-dispatcher-due-gates.md) | 3998 | Gate idle local supervisor drains while retaining external polling |
| [Quiet dispatcher capability, supervisor and wake deadlines](mirror-4059-e-dispatcher-due-gates.md) | 3999 | Defer wake probe selection and policy reads until indexed due time |
| [Quiet dispatcher capability, supervisor and wake deadlines](mirror-4059-e-dispatcher-due-gates.md) | 4000 | Preserve refresh invalidation and incoming error isolation; correct drain replay |
| [Quiet dispatcher capability, supervisor and wake deadlines](mirror-4059-e-dispatcher-due-gates.md) | 4001 | Index wake-check deadlines for idle due probes |
| [Quiet dispatcher capability, supervisor and wake deadlines](mirror-4059-e-dispatcher-due-gates.md) | 4002 | Keep schema index addition within facade line budget |
| [Folded notices settle once across checkpoints and preserve notes](mirror-4059-e-folded-notices.md) | 4003 | preserve completion notes when folding pending turns |
| [Causal loss receipts after worker rebind](mirror-4059-e-rebound-worker-loss.md) | 4004 | Witness rebound worker loss in modern failure recovery |
| [Isolated seeds for fake Board release matrices](mirror-4059-e-release-matrix-seeds.md) | 4005 | Reuse isolated Board seeds for fake release matrices |
| [Sweep idle handles for removed Kanban boards](mirror-4059-e-removed-board-handles.md) | 4006 | kanban: close kept board handles once the board file is removed |
| [Bounded queue pagination and confirmation scans](mirror-4059-e-ci-queue-pagination.md) | 4007 | test(ci): pin short-page total-count equality boundary |
| [Replica shared-stage lifecycle](mirror-4059-f-replica-stage-lifecycle.md) | 4008 | Pin replica shared-stage cleanup and malformed-row reuse |
| [Quiet extension reporting and measurement custody](mirror-4059-f-quiet-extension-observation.md) | 4009 | report unattested extension code in quiet observations |
| [Quiet extension reporting and measurement custody](mirror-4059-f-quiet-extension-observation.md) | 4010 | preserve admitted batch horizon as quiet lease elapses |
| [Quiet extension reporting and measurement custody](mirror-4059-f-quiet-extension-observation.md) | 4011 | fix(quiet): keep unresolved native loader provenance blocking |
| [Restart receipts and child-policy test isolation](mirror-4059-f-restart-receipt-tests.md) | 4012 | Test submitter restart receipt fences independently |
| [Restart receipts and child-policy test isolation](mirror-4059-f-restart-receipt-tests.md) | 4013 | isolate restart-survival child account policy |
| [Upstream-sync evidence and fetch replay tests](mirror-4059-f-upstream-sync-evidence-tests.md) | 4014 | test(ci): pin off-key receipt full SHA validation |
| [Nested-shell alias scopes in lifecycle scanning](mirror-4059-f-nested-shell-scan.md) | 4015 | lifecycle guard: walk a nested startup-file shell's input with unknown alias names (PR #2358 review 5894325109 nit 1) |
| [Nested-shell alias scopes in lifecycle scanning](mirror-4059-f-nested-shell-scan.md) | 4016 | lifecycle guard: key visited scripts by walk scope (PR #2560 review 5897863903 B1) |
| [SQLite observer restore and install-epoch boundaries](mirror-4059-f-sqlite-observer-tests.md) | 4017 | Deflake same-inode SQLite restore timestamp fixture |
| [Creator-scoped cross-gateway scratch archive](mirror-4059-f-creator-archive.md) | 4018 | fix(kanban): route creator scratch cleanup to the home gateway |
| [Creator-scoped cross-gateway scratch archive](mirror-4059-f-creator-archive.md) | 4019 | test(kanban): exercise creator cleanup authorization and races |
| [Creator-scoped cross-gateway scratch archive](mirror-4059-f-creator-archive.md) | 4020 | test(kanban): isolate creator cleanup predicate boundaries |
| [Exact incremental Kanban dashboard reads](mirror-4059-f-dashboard-incremental.md) | 4021 | WIP incremental dashboard P2 reads and bookkeeping overlay |
| [Exact incremental Kanban dashboard reads](mirror-4059-f-dashboard-incremental.md) | 4022 | Refresh only changed dashboard neighbours and preserve exact GET tests |
| [Exact incremental Kanban dashboard reads](mirror-4059-f-dashboard-incremental.md) | 4023 | Register intended incremental dashboard core boundaries |
| [Bash printf output-sink reconstruction](mirror-4059-f-printf-sink-scan.md) | 4024 | lifecycle guard: printf `*` width/precision, `%.Ns` truncation, `%b` escapes and `-v NAME` no longer hide the printed command |
| [Bash printf output-sink reconstruction](mirror-4059-f-printf-sink-scan.md) | 4025 | lifecycle guard printf: C-exact numbers, long-double models, output cap, whole-argument walk kept |
| [Bash printf output-sink reconstruction](mirror-4059-f-printf-sink-scan.md) | 4026 | lifecycle guard printf: saturate >4300-digit numbers instead of aborting the walk |
| [Bash printf output-sink reconstruction](mirror-4059-f-printf-sink-scan.md) | 4027 | printf guard: preserve conversions after unknown format escapes |
| [Credential singleton read and home-path fast paths](mirror-4059-f-picker-path-allocation.md) | 4028 | perf(picker): remove redundant singleton reads and home path allocation |
| [Credential singleton read and home-path fast paths](mirror-4059-f-picker-path-allocation.md) | 4029 | test(picker): guard new home tests against unittest discovery |
| [Independent local and relayed credential disable policy](mirror-4059-f-relay-disable-policy.md) | 4030 | Preserve independent local and relay credential disable policy |
| [Independent local and relayed credential disable policy](mirror-4059-f-relay-disable-policy.md) | 4031 | Report remaining relay policy after local auth enable |
| [Independent local and relayed credential disable policy](mirror-4059-f-relay-disable-policy.md) | 4032 | Cover relay reporting ownership scope and unreadable roots |
| [Authoritative account-board worker census](mirror-4059-f-worker-board-inventory.md) | 4033 | fix(worker): bound real inventory to account board roots |
| [Authoritative account-board worker census](mirror-4059-f-worker-board-inventory.md) | 4034 | fix(worker): use authoritative account boards for slot census |
| [Authoritative account-board worker census](mirror-4059-f-worker-board-inventory.md) | 4035 | fix(worker): preserve unknown profile discovery errors |
| [Session checkpoint, thread handoff and reply-delivery tests](mirror-4059-f-session-delivery-regressions.md) | 4036 | Cover folded checkpoint history boundaries and pending publication |
| [Session checkpoint, thread handoff and reply-delivery tests](mirror-4059-f-session-delivery-regressions.md) | 4037 | Wait for prompt worker startup before joining rewind turns |
| [Upstream-sync evidence and fetch replay tests](mirror-4059-f-upstream-sync-evidence-tests.md) | 4038 | Test first approving receipt in upstream sync success detail |
| [Successor-aware durable notice retry deadlines](mirror-4059-f-notice-successor-retry.md) | 4039 | let successor code re-evaluate durable wall retries |
| [Successor-aware durable notice retry deadlines](mirror-4059-f-notice-successor-retry.md) | 4040 | fix(notices): preserve reset budget across successor rechecks |
| [Arithmetic sink scanning across quotes and heredocs](mirror-4059-f-arithmetic-heredoc-scan.md) | 4041 | fix(shell): keep arithmetic sink scanning aligned across quoted data |
| [Arithmetic sink scanning across quotes and heredocs](mirror-4059-f-arithmetic-heredoc-scan.md) | 4042 | fix(guards): retain masked heredoc terminators |
| [Arithmetic sink scanning across quotes and heredocs](mirror-4059-f-arithmetic-heredoc-scan.md) | 4043 | fix(shell): scan executable literal heredoc bodies for arithmetic sinks |
| [Arithmetic sink scanning across quotes and heredocs](mirror-4059-f-arithmetic-heredoc-scan.md) | 4044 | refactor(shell): isolate arithmetic scan fast paths |
| [Shell comment and continuation word boundaries](mirror-4059-f-comment-word-boundaries.md) | 4045 | lifecycle guard: a `#` comments only at a word start; a quote or backslash in a comment opens nothing |
| [Shell comment and continuation word boundaries](mirror-4059-f-comment-word-boundaries.md) | 4046 | fix(cron): preserve escaped comment word boundaries |
| [Exact-head repair continuation after awaited reviews](mirror-4059-f-awaited-review-repair.md) | 4047 | fix(kanban): resume timed review waits for exact-head repair |
| [Deterministic CPU-advisory age boundaries](mirror-4059-f-admission-clock-test.md) | 4048 | Freeze admission clock in advisory age boundary test |
| [Upstream-sync evidence and fetch replay tests](mirror-4059-f-upstream-sync-evidence-tests.md) | 4049 | Replay fetch integration tests with the CI bash version |
| [Quiet extension reporting and measurement custody](mirror-4059-f-quiet-extension-observation.md) | 4050 | test: await PTY EOF custody before closing test client |
| [Quiet extension reporting and measurement custody](mirror-4059-f-quiet-extension-observation.md) | 4051 | Test named-PID CPU reads with controlled work deltas |
| [Session checkpoint, thread handoff and reply-delivery tests](mirror-4059-f-session-delivery-regressions.md) | 4052 | test(gateway): allow independent busy-reply delivery order |
| [SQLite observer restore and install-epoch boundaries](mirror-4059-f-sqlite-observer-tests.md) | 4053 | Isolate reinstall epoch invalidation from schema cookies |
| [Upstream-sync evidence and fetch replay tests](mirror-4059-f-upstream-sync-evidence-tests.md) | 4054 | Test recovered blob store output identity validation |
| [Home-state placement across nested directory changes](mirror-4059-f-home-span-placement.md) | 4055 | placement: a cd nested in ( ), $( ) or `...` never ends the home span; pushd -n neither |
| [Targeted late pytest exit diagnostics](mirror-4059-f-pytest-exit-diagnostics.md) | 4056 | capture late pytest exit stacks for derived-session investigation |
| [Supervised gateway dashboard READY barrier](mirror-4059-f-supervised-dashboard-ready.md) | 4057 | Wait for supervised dashboard mount before gateway READY |
| [Deterministic shared retry-header parsing test](mirror-4059-f-retry-header-clock-test.md) | 4058 | Freeze retry header clock in metrics sender test |
| [Session-scoped final buffering after pre-call hooks](mirror-4059-f-session-final-buffer.md) | 4059 | Arm final buffering after hooks with session-scoped eligibility |

## Mirror 3714–3727

Patches through `78c49a510b`; verification in [mirror-3727-verification.md](mirror-3727-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Kanban local status wake for parked cards](mirror-3727-a-kanban-local-status-wake.md) | 3714 | Re-probes a park awaiting a card soon after that card changes status on the same gateway, via a temp trigger recording status changes and a post-commit expedite. |
| [Kanban local status wake for parked cards](mirror-3727-a-kanban-local-status-wake.md) | 3715 | Makes the status-wake flush swallow any exception so a non-sqlite stand-in connection cannot break a commit. |
| [Kanban placement: home-relative state paths](mirror-3727-a-kanban-home-path-placement.md) | 3716 | Treats $HOME, ${HOME} and home-relative .hermes paths after a cd to home as state-path markers for card placement. |
| [Worker policy root and comment-writer guard nits](mirror-3727-a-worker-guard-review-nits.md) | 3717 | Stops an odd TMPDIR from admitting a durable HERMES_TEST_POLICY_ROOT and adds a refusal-streak test. |
| [Worker policy root and comment-writer guard nits](mirror-3727-a-worker-guard-review-nits.md) | 3718 | Matches the interactive ssm-session-worker name on its 15-character comm prefix in the comment writer. |
| [Test-only follow-ups: upstream sync and lifecycle guard rc-file rows](mirror-3727-a-test-follow-ups.md) | 3719 | Adjusts fork tests to upstream's extra _seed_tree root argument and delegation explicit_base_url. |
| [Test-only follow-ups: upstream sync and lifecycle guard rc-file rows](mirror-3727-a-test-follow-ups.md) | 3720 | Adds lifecycle guard tests for an rc file right before the script operand and a trailing value-less --rcfile. |
| [Desktop shares identical in-flight backend reads](mirror-3727-b-desktop-read-coalescing.md) | 3721 | Adds read coalescing so identical concurrent bodyless backend GETs share one wire request with per-caller copies. |
| [Lifecycle guard: benign session aliases no longer make call arguments executed](mirror-3727-b-lifecycle-guard-session-aliases.md) | 3722 | Tracks known alias names instead of an any-alias flag so call-argument mentions are not treated as executed. |
| [Lifecycle guard: benign session aliases no longer make call arguments executed](mirror-3727-b-lifecycle-guard-session-aliases.md) | 3723 | Adds tests for a heredoc body reading an over-cap spill file under benign aliases. |
| [Lifecycle guard: benign session aliases no longer make call arguments executed](mirror-3727-b-lifecycle-guard-session-aliases.md) | 3724 | Moves the alias helper functions into cron/lifecycle_guard_aliases.py without behavior change. |
| [Kanban wait-check: Board replica fallback and completion-origin pin](mirror-3727-b-kanban-wait-check-board-replica.md) | 3725 | Lets wait-check --gateway read a fresh Board replica to turn an unreachable-owner answer into met. |
| [Kanban wait-check: Board replica fallback and completion-origin pin](mirror-3727-b-kanban-wait-check-board-replica.md) | 3726 | Adds a test pinning that a recorded completion origin outranks its run's Board lease. |
| [Routing: a Desktop chat's own provider URL does not block the walled-pin reroute](mirror-3727-b-routing-walled-pin-gui-url.md) | 3727 | Uses overrides_endpoint so a GUI chat's own provider URL no longer blocks the walled-pin reroute, and logs non-deferral reasons. |

## Mirror 3690–3713

Patches through `126d9a35b5`; verification in [mirror-3713-verification.md](mirror-3713-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Cron: deferred restart rerun stays pending](mirror-3713-a-cron-deferred-rerun-stays-pending.md) | 3690 | Records a fire_at re-arm instant so a deferred restart rerun stays pending, held in DarkWake and exempt from the one-shot grace. |
| [Kanban: core-update requeue keeps one-continuation grants](mirror-3713-a-kanban-one-continuation-requeue.md) | 3691 | Makes operator resume skip core-update requeues so the resume receipt is not spent by them. |
| [Kanban: core-update requeue keeps one-continuation grants](mirror-3713-a-kanban-one-continuation-requeue.md) | 3692 | Adds a cross-path test that every one-continuation grant survives core-update requeues and is spent by real claims. |
| [Kanban chat links and completion refs: test pins](mirror-3713-a-kanban-chat-links-test-pins.md) | 3693 | Adds tests pinning profile-board dedup, origin filtering and the unlinked-chat count in chat links. |
| [Scratch archive restore: tolerate a deleted remote branch](mirror-3713-a-scratch-archive-restore-deleted-remote-branch.md) | 3694 | Restore drops and reports remote-tracking refs the remote deleted instead of aborting, still refusing for local refs. |
| [Kanban chat links and completion refs: test pins](mirror-3713-a-kanban-chat-links-test-pins.md) | 3695 | Adds tests pinning completion-ref pick (latest, before, origin) and released-links reader limits. |
| [Kanban worker-first: never reserve a card every worker refuses](mirror-3713-a-kanban-worker-first-pickup-gate.md) | 3696 | Placement tick stops reserving enrolled cards the pickup gate refuses, noting capability_mismatch and repinning to origin. |
| [Desktop: per-plugin JSON pools and single-flight hub-sync status](mirror-3713-a-desktop-plugin-pools-single-flight.md) | 3697 | Gives each plugin its own capped JSON socket pool and makes hub-sync status reads single-flight with a 60 s miss cache. |
| [Continued run's clean exit is a protocol violation](mirror-3713-b-continued-run-clean-exit.md) | 3698 | Books a continued run's clean worker exit as a protocol violation and stops comment paths promising a live run once the worker has exited. |
| [worker_resources_unknown streak alarm and inventory hardening](mirror-3713-b-worker-resources-unknown-alarm.md) | 3699 | Treats /dev/shm as an ephemeral root so a deleted board there retires from the worker inventory. |
| [worker_resources_unknown streak alarm and inventory hardening](mirror-3713-b-worker-resources-unknown-alarm.md) | 3700 | Adds a streak alarm that opens one repair card per host per incident when worker_resources_unknown holds every claim over 10 minutes. |
| [worker_resources_unknown streak alarm and inventory hardening](mirror-3713-b-worker-resources-unknown-alarm.md) | 3701 | Adds HERMES_TEST_POLICY_ROOT, honoured only under a temp root, to isolate a non-pytest harness's account inventory. |
| [worker_resources_unknown streak alarm and inventory hardening](mirror-3713-b-worker-resources-unknown-alarm.md) | 3702 | Reads the alarm's streak file as utf-8-sig. |
| [Process adoption: parse-failure status, foreign-kill settlement, unknown docks](mirror-3713-b-process-adoption-kill-and-status.md) | 3703 | Records rc 2 for parse failures, settles foreign kills as killed via a marker, applies local policy to foreign poll/wait, and maps unknown exits in the docks. |
| [LC_HERMES_AGENT marker so agent comments over ssh stay agent-written](mirror-3713-b-kanban-comment-ssh-agent-marker.md) | 3704 | Adds an LC_HERMES_AGENT marker that survives ssh so agent-written Kanban comments are not recorded as human. |
| [Awaited-card report_lag re-warns on a new park episode](mirror-3713-b-awaited-card-report-lag-rewarn.md) | 3705 | Re-arms the report_lag warning when a warned park re-parks on a finished card within one scan window. |
| [Lifecycle guard: printf and function-keyword shapes](mirror-3713-c-lifecycle-guard-printf.md) | 3706 | Walks printf output (format applied, capped at 64 rounds) as executed shell and pins function-keyword echo shapes in the lifecycle guard. |
| [Hosted-room test: plant the old cache with the driver stopped](mirror-3713-c-hosted-room-cache-test.md) | 3707 | Stops the hosted-room driver before planting the pre-migration cache in a flaky test. |
| [Kanban test pins: reopen reclaim and CI-progress scan](mirror-3713-c-kanban-test-pins.md) | 3708 | Pins reopen's dangling-run reclaim for blocked, scheduled and triage stale copies and the done-hold refusal text. |
| [Unpublished-workspace park refusal names the publish refusal](mirror-3713-c-kanban-park-refusal-hint.md) | 3709 | Appends the underlying publish refusal (and clone-registration fix) to the unpublished_workspace park refusal. |
| [Kanban scratch GC refusal memo hardening](mirror-3713-c-kanban-scratch-gc-memo.md) | 3710 | Validates loaded memo entries and drops malformed ones with a warning so one bad entry cannot abort a GC pass. |
| [Kanban scratch GC refusal memo hardening](mirror-3713-c-kanban-scratch-gc-memo.md) | 3711 | Saturates the refusal count at MAX_COUNT and drops loaded entries above it so the backoff exponent stays small. |
| [Replica-freshness alarm resumes the awake clock across restarts](mirror-3713-c-replica-freshness-resume.md) | 3712 | Persists the replica-age observation with the boot id so the awake clock resumes across gateway restarts, and pins two surviving mutants. |
| [Kanban test pins: reopen reclaim and CI-progress scan](mirror-3713-c-kanban-test-pins.md) | 3713 | Adds tests for run-list-error continue, non-I/O error propagation and the in-progress page size in the CI-progress scan. |

## Mirror 3653–3689

Patches through `bf08acbc3e`; verification in [mirror-3689-verification.md](mirror-3689-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Test hardening: review requeue deflake and single cancelled completion](mirror-3689-a-review-requeue-test-hardening.md) | 3653 | Joins the background-review thread before asserting its requeue log so the wind-down test stops flaking. |
| [Process registry: adopted jobs record their real exit](mirror-3689-d-process-adopted-job-exit.md) | 3654 | Records an adopted job's real exit via a child-shell recorder, settles unrecorded exits as unknown, and lets the owning chat poll, log, wait and kill a job another process adopted. |
| [Kanban create: same-creator duplicate guard](mirror-3689-a-kanban-create-same-creator-overlap.md) | 3655 | Stops exempting a creator's own open cards from the overlap check, dedups same-run re-issues, and adds a completion-time duplicate detector. |
| [Kanban CI progress: pool-scan robustness](mirror-3689-d-kanban-ci-progress-pool-scan.md) | 3656 | A job-read error no longer ends the pool scan before a later batch's match. |
| [without_evidence: strip only the "Existing work checked" record](mirror-3689-a-without-evidence-existing-work-strip.md) | 3657 | Heading-form record strips only its first paragraph so later steps are still scanned for host markers. |
| [Test hardening: review requeue deflake and single cancelled completion](mirror-3689-a-review-requeue-test-hardening.md) | 3658 | Tests now assert a stopped queue head's cancelled completion is published exactly once. |
| [Cron: re-arm a restart rerun the host slept through](mirror-3689-b-cron-restart-rerun-darkwake.md) | 3659 | Re-arms (max 3) a restart rerun cut by host sleep and holds reruns out of DarkWakes, exempt from one-shot grace. |
| [Test pins for post-merge mutant survivors](mirror-3689-b-post-merge-mutant-pins.md) | 3660 | Desktop tests pin 15 surviving mutants in status-dot, idle-motion-pause and kanban events coalescing, plus a docstring fix. |
| [Scratch archive: omit objects the remote already holds](mirror-3689-d-scratch-archive-remote-omit.md) | 3661 | Test-only: marks the fsync-spy scratch-gc tests posix-platform and asserts the injected fault fired. |
| [Kanban CI progress: pool-scan robustness](mirror-3689-d-kanban-ci-progress-pool-scan.md) | 3662 | Test-only: the scan fake honours per_page and pins the in-progress run-list page size. |
| [Lifecycle guard: shell-parsing fail-open fixes](mirror-3689-b-lifecycle-guard-shell-parsing.md) | 3663 | Scans stdin feeds through brace/if/loop/case groups, glued redirections and process substitution via a new groups module. |
| [Kanban reopen and unblock of a stale terminal-held copy](mirror-3689-c-kanban-reopen-stale-copy.md) | 3664 | Reopening a stale copy reclaims its dangling run, and CLI reopen of an archived copy prints the unblock-style refusal. |
| [Kanban reopen and unblock of a stale terminal-held copy](mirror-3689-c-kanban-reopen-stale-copy.md) | 3665 | Adds a test pinning the operator-call unblock refusal text for a card held done by its completed event. |
| [Kanban replayed completions bind their run's receipt](mirror-3689-c-kanban-completion-receipt-replay.md) | 3666 | Binds a pre-origin replayed completion to its run's receipt using the Board lease holder of that run. |
| [Kanban chat links: batched machine-chat classification](mirror-3689-c-kanban-chat-links-machine-chats.md) | 3667 | Makes _unlinked_chats reuse _machine_by_profile and pins the links_diff machine-chat behavior with a test. |
| [Kanban sync: Board live_claim refusal of run and event rows](mirror-3689-c-kanban-live-claim-child-rows.md) | 3668 | Records a Board live_claim refusal of task_runs and task_events rows and restores or deletes the local row on a primary board. |
| [CI: state-store mini lane follow-up nits](mirror-3689-b-ci-state-lane-nits.md) | 3669 | Fixes the gate-lanes comment, turns the main-push test into an invariant, and documents scheduled-red alerting. |
| [Remote notice index: bounded memory](mirror-3689-d-remote-notice-index-memory.md) | 3670 | Keeps only routed cards in the remote index and pages cold payload reads to bound memory. |
| [Kanban scratch GC: per-tree refusal backoff and pass budget](mirror-3689-d-scratch-gc-refusal-backoff.md) | 3671 | Adds per-tree archive-refusal backoff and a scratch-gc pass budget with a scratch-budget-seconds flag. |
| [Hub-read root paths: keep every path when a traversal is hidden](mirror-3689-a-hub-read-root-paths-fail-closed.md) | 3672 | Keeps hub-read root paths when a hidden traversal, expansion or cd could move the path. |
| [Lifecycle guard: shell-parsing fail-open fixes](mirror-3689-b-lifecycle-guard-shell-parsing.md) | 3673 | Makes zsh clustered -O valueless and scans --rcfile only before the script operand. |
| [Test pins for post-merge mutant survivors](mirror-3689-b-post-merge-mutant-pins.md) | 3674 | Adds tests pinning requeue stop-latch cleanup and memo-heal retry/failure-streak behavior. |
| [Scratch archive: omit objects the remote already holds](mirror-3689-d-scratch-archive-remote-omit.md) | 3675 | Omits git objects and clean tracked files the remote holds from complete scratch archives, with a v2 manifest, supplement pack and restore helper. |
| [Scratch archive: omit objects the remote already holds](mirror-3689-d-scratch-archive-remote-omit.md) | 3676 | Counts a remote-tracking tip as held only while the remote still advertises it, and hardens restore for read-only directories. |
| [Kanban sync replica alarm and awaited-card re-probe](mirror-3689-c-kanban-sync-replica-wake.md) | 3677 | Counts any change of the replica stamp as an advance so a backward wall-clock step files no false stale-replica card. |
| [Kanban comment writer: automation and handle attribution](mirror-3689-d-comment-writer-agent-attribution.md) | 3678 | Records agent instead of human for automation runners, systemd services and agent-handle authors. |
| [Kanban create: same-creator duplicate guard](mirror-3689-a-kanban-create-same-creator-overlap.md) | 3679 | Detector ignores denials like "not a duplicate", and an added round or sha no longer counts as an enumeration. |
| [Kanban pickup-stall: fresher sightings and no stale hold repairs](mirror-3689-d-pickup-stall-sighting-and-hold-repair.md) | 3680 | Writes uncommented sighting events every 300 s during volatile flaps so pickup-stall sightings stay fresh. |
| [Kanban test pins: sync follow-ups and source clone proof](mirror-3689-d-kanban-test-pins.md) | 3681 | Test-only: pins backlog noting after an after-pull send failure and that deferred rows never age the alarm. |
| [Kanban test pins: sync follow-ups and source clone proof](mirror-3689-d-kanban-test-pins.md) | 3682 | Test-only: pins prove_clone's fetch-URL compare against a global fetch-only rewrite. |
| [Kanban replayed completions bind their run's receipt](mirror-3689-c-kanban-completion-receipt-replay.md) | 3683 | Makes pick fall back to the same-run receipt when a pre-uid replayed id names another run's receipt. |
| [Kanban sync replica alarm and awaited-card re-probe](mirror-3689-c-kanban-sync-replica-wake.md) | 3684 | Re-probes a park awaiting a card when its status arrives by replica rebuild or sync reconcile, and reads replica status for the lag warning. |
| [Lifecycle guard: shell-parsing fail-open fixes](mirror-3689-b-lifecycle-guard-shell-parsing.md) | 3685 | Adds tests pinning bare-dash bash script handling and coproc CMD ARGS. |
| [Kanban pickup-stall: fresher sightings and no stale hold repairs](mirror-3689-d-pickup-stall-sighting-and-hold-repair.md) | 3686 | Skips hold repairs once the live hold lifted and adds a sweep that closes resolved host-wide repairs. |
| [Test pins for post-merge mutant survivors](mirror-3689-b-post-merge-mutant-pins.md) | 3687 | Adds a CLI test pinning the assert-dist-built throttled-host hint and timeout knob name. |
| [without_evidence: strip only the "Existing work checked" record](mirror-3689-a-without-evidence-existing-work-strip.md) | 3688 | Normalises CRLF and CR line endings so the record strip cannot fail open. |
| [Kanban sync: Board live_claim refusal of run and event rows](mirror-3689-c-kanban-live-claim-child-rows.md) | 3689 | Restores a child row refused live_claim on its compat retry, like an upsert. |

## Mirror 3623–3652

Patches through `9f1a0b1eb9`; verification in [mirror-3652-verification.md](mirror-3652-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Cron: hold a fire whose gateway dial failed](mirror-3652-a-cron-connect-failure-hold.md) | 3623 | Holds a scheduled fire whose gateway dial failed before submit (up to 3 times) instead of booking it FAILED. |
| [Cron: hold a fire whose gateway dial failed](mirror-3652-a-cron-connect-failure-hold.md) | 3624 | Restricts the dial-failure hold to scheduled fires so manual fires are booked FAILED. |
| [Kanban placement: no worker-first card that no pool worker can claim](mirror-3652-a-kanban-placement-existing-work-record.md) | 3625 | Strips "Existing work checked" records from placement scans and stops enrolling unclaimable or queue-miss cards worker-first. |
| [Kanban sync round follow-ups and scratch-archive promisor check](mirror-3652-a-kanban-sync-and-scratch-archive-followups.md) | 3626 | Reads remote promisor config as a git boolean so an empty value no longer counts as a partial clone. |
| [Kanban sync round follow-ups and scratch-archive promisor check](mirror-3652-a-kanban-sync-and-scratch-archive-followups.md) | 3627 | Sync rounds still pull after a failed push, outbox alarm ages only sendable ops, repin pulls under the board lock, and sync diff moves nothing. |
| [Test hardening: disposable host policy for owner probes, placeholder chats in links_diff](mirror-3652-a-worker-host-probe-isolation-and-links-diff-test.md) | 3628 | Isolates the host worker policy for real-dispatcher probes and their child processes (tests only). |
| [Test hardening: disposable host policy for owner probes, placeholder chats in links_diff](mirror-3652-a-worker-host-probe-isolation-and-links-diff-test.md) | 3629 | Adds a test pinning that placement placeholder chats are counted, not compared, in links_diff. |
| [Kanban: unblock of a terminal-held stale copy points at reopen](mirror-3652-a-kanban-unblock-stale-copy-reopen.md) | 3630 | Unblock refusal names the terminal hold and reopen now revives a stale held-done copy. |
| [Kanban: unblock of a terminal-held stale copy points at reopen](mirror-3652-a-kanban-unblock-stale-copy-reopen.md) | 3631 | Routes the dashboard through kanban_db for the refusal text and pins reopen's UPDATE statuses. |
| [Update-pause requeue settle and remote-owner tick cleanup](mirror-3652-a-update-pause-requeue-and-remote-owner-cleanup.md) | 3632 | Settles a stopped head's cancel that committed before raising and documents the post-commit Stop window. |
| [Update-pause requeue settle and remote-owner tick cleanup](mirror-3652-a-update-pause-requeue-and-remote-owner-cleanup.md) | 3633 | Removes the dead tick throttle in the remote owner and tightens its notify_in_gateway test. |
| [Declared hub:read clears read-only hub paths in kanban placement](mirror-3652-b-hub-read-placement-clearing.md) | 3634 | A declared hub:read card drops hub ssh read subcommands and paths under hub_read_roots from the placement marker scan. |
| [Declared hub:read clears read-only hub paths in kanban placement](mirror-3652-b-hub-read-placement-clearing.md) | 3635 | Restricts root paths to an allow-list of characters and refuses dot-only segments. |
| [Declared hub:read clears read-only hub paths in kanban placement](mirror-3652-b-hub-read-placement-clearing.md) | 3636 | Strips a root path only when it ends its shell word. |
| [Declared hub:read clears read-only hub paths in kanban placement](mirror-3652-b-hub-read-placement-clearing.md) | 3637 | Adds _ends_word so closing quotes must match the opening one and at most one trailing dot is allowed. |
| [Declared hub:read clears read-only hub paths in kanban placement](mirror-3652-b-hub-read-placement-clearing.md) | 3638 | Judges root paths before backtick spans are dropped so a glued backtick keeps the card pinned. |
| [Declared hub:read clears read-only hub paths in kanban placement](mirror-3652-b-hub-read-placement-clearing.md) | 3639 | Preserves backtick boundaries when stripping hub-read paths. |
| [Declared hub:read clears read-only hub paths in kanban placement](mirror-3652-b-hub-read-placement-clearing.md) | 3640 | Requires an opening quote to close at the path and keeps every path when a standalone .. segment exists. |
| [Outcome watch mark: audit and Desktop label follow-ups](mirror-3652-b-outcome-watch-audit-followups.md) | 3641 | Audit joins all outcome reasons, lazy watch read, dated far wake and titled tooltip in Desktop, and surfaces locked-board errors. |
| [workspace_source clone proof on pool workers and the hub](mirror-3652-b-workspace-source-clone-proof-hosts.md) | 3642 | Clone proof ignores global url rewrites that miss the origin and uses the dispatcher gh route for the GitHub id lookup. |
| [Test hardening: sandbox exit probe and requeue single-write](mirror-3652-c-test-hardening-sandbox-and-requeue.md) | 3643 | Treats ESRCH from a reaped process like ENOENT in the sandbox normal-exit probe. |
| [Test hardening: sandbox exit probe and requeue single-write](mirror-3652-c-test-hardening-sandbox-and-requeue.md) | 3644 | Adds a test pinning that the wake re-queue and steer hand-back are one atomic write. |
| [Lifecycle guard: uv options and hidden stdin feeds](mirror-3652-c-lifecycle-guard-uv-and-stdin-feeds.md) | 3645 | Guard reads uv value options and hidden stdin feeds (redirections, cat stdin, piped groups) via a new uv argv reader. |
| [Kanban chat links: profile-own board cards under the replica reader](mirror-3652-c-kanban-chat-links-profile-board.md) | 3646 | Keeps profile-own board cards in their chat from the ledger under links_reader=replica and counts them in links_diff. |
| [Kanban dispatch signals: quieter queue reasons and faster awaited-card wakes](mirror-3652-c-kanban-queue-reason-and-awaited-card-wake.md) | 3647 | Writes queue-reason events and comments only on meaning changes, throttling volatile load flaps to one per 10 minutes. |
| [Kanban dispatch signals: quieter queue reasons and faster awaited-card wakes](mirror-3652-c-kanban-queue-reason-and-awaited-card-wake.md) | 3648 | Re-probes a park awaiting a remote card when its status change reaches the replica and warns on park lag. |
| [Kanban sync: stale-replica alarm](mirror-3652-c-kanban-sync-replica-age-alarm.md) | 3649 | Files one incident card when a board replica stamp does not advance for 20 awake minutes. |
| [Kanban sync: stale-replica alarm](mirror-3652-c-kanban-sync-replica-age-alarm.md) | 3650 | Reads the replica stamp through the lock guard. |
| [Upstream sync 55b0b2611f: full-suite fixups](mirror-3652-c-upstream-sync-55b0b2611f.md) | 3651 | Fixes cwd leak in multiplexed gateways, extends machinery kinds, and repairs tests after the upstream merge. |
| [Desktop chunk-check budget: integer-only timeout](mirror-3652-c-desktop-chunk-check-budget.md) | 3652 | Rejects fractional chunk-check timeouts and improves the harness timeout hint. |

## Mirror 3578–3622

Patches through `2a35439ef1`; verification in [mirror-3622-verification.md](mirror-3622-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Notice fan-out transaction rollback and shared executor-cancel prefixes](mirror-3622-a-notice-fanout-rollback-and-cancel-prefixes.md) | 3578 | Roll back an open write transaction on any exception in the cached notice fan-out connection. |
| [Notice fan-out transaction rollback and shared executor-cancel prefixes](mirror-3622-a-notice-fanout-rollback-and-cancel-prefixes.md) | 3579 | Share one public executor-cancel prefix tuple between restart continuation and update pause, and pin the mid-tool race in a test. |
| [Kanban dashboard refuses writes under another machine's live lease](mirror-3622-a-kanban-dashboard-remote-lease-refusal.md) | 3580 | Make the Kanban dashboard refuse status, reclaim, terminate and reassign writes on a card under another machine's live Board lease. |
| [Desktop Kanban events: never resume at since=0, coalesce re-reads](mirror-3622-a-desktop-kanban-events-cursor-coalescing.md) | 3581 | Stop the Desktop Kanban events socket opening at since=0 for unknown snapshots and coalesce frame-driven board re-reads. |
| [TUI update pause: killed notify jobs reported once from the marker](mirror-3622-a-tui-update-pause-obligation-settle.md) | 3582 | Settle the delivery obligation of jobs killed by an update pause so they are reported once from the marker. |
| [TUI update pause: killed notify jobs reported once from the marker](mirror-3622-a-tui-update-pause-obligation-settle.md) | 3583 | Defer that settlement until the pause stands so a later user Stop cannot lose the notice. |
| [Desktop idle-motion-pause removes a sheet that survives did-finish-load](mirror-3622-a-desktop-idle-motion-pause-sheet.md) | 3584 | Remove the idle-motion pause sheet that survives did-finish-load so resume unfreezes the window. |
| [Lifecycle guard: echo option words no longer hide the printed command](mirror-3622-a-lifecycle-guard-echo-option-words.md) | 3585 | Walk echo and printf lines with leading option words stripped so the lifecycle guard sees the printed command. |
| [Kanban workspace_source: source attach and pre-spawn abort at launch](mirror-3622-b-workspace-source-attach-launch.md) | 3586 | Fixes review re-block recurrence, follow-up quarantine, the empty-drain write transaction and the worker-side enabled gate. |
| [Kanban workspace_source: source attach and pre-spawn abort at launch](mirror-3622-b-workspace-source-attach-launch.md) | 3587 | Adds Board source actions, source attach and pre-spawn abort launch, per-call source_protocol, old-Board fallback and source env vars. |
| [Kanban workspace_source: source attach and pre-spawn abort at launch](mirror-3622-b-workspace-source-attach-launch.md) | 3588 | Fixes a health/footgun issue in the source launch module. |
| [Kanban workspace_source: ending preflight and branch publish](mirror-3622-b-workspace-source-preflight-publish.md) | 3589 | Adds the ending preflight and publish steps (branch scope, local state, remote proof, secret scan, lease fence, push, manifest). |
| [Kanban workspace_source: move, reclaim, CLI claim, source-release and docs](mirror-3622-b-workspace-source-move-claim-cli.md) | 3590 | Adds move/reclaim checks, capability handling, CLI claim refusal and source-release, and flips SOURCE_PROTOCOL_COMPLETE. |
| [Kanban workspace_source: source attach and pre-spawn abort at launch](mirror-3622-b-workspace-source-attach-launch.md) | 3591 | Adds launch tests (attach, abort, old-Board, legacy adopt) and makes legacy adopt read created_on from the row. |
| [Kanban workspace_source: ending preflight and branch publish](mirror-3622-b-workspace-source-preflight-publish.md) | 3592 | Adds publish tests and makes the secret scan force redaction regardless of display settings. |
| [Kanban workspace_source: move, reclaim, CLI claim, source-release and docs](mirror-3622-b-workspace-source-move-claim-cli.md) | 3593 | Adds move and CLI tests and stops push from setting an empty remote refspec. |
| [Kanban workspace_source: move, reclaim, CLI claim, source-release and docs](mirror-3622-b-workspace-source-move-claim-cli.md) | 3594 | Documents portable worktree sources in the Kanban user guide and config comment. |
| [Kanban workspace_source: source attach and pre-spawn abort at launch](mirror-3622-b-workspace-source-attach-launch.md) | 3595 | Lint and footgun fixes plus a shared git world test fixture. |
| [Kanban workspace_source: hardened publish secret scan](mirror-3622-b-workspace-source-secret-scan-hardening.md) | 3596 | Hardens the diff-based secret scan and makes the old-Board path refuse every later custody card. |
| [Kanban workspace_source: source attach and pre-spawn abort at launch](mirror-3622-b-workspace-source-attach-launch.md) | 3597 | Fills review test gaps (admitted claim, post-attach refusal, old-Board 400) and shares failure-command DDL, ls-remote batch env and budget nits. |
| [Kanban workspace_source: move, reclaim, CLI claim, source-release and docs](mirror-3622-b-workspace-source-move-claim-cli.md) | 3598 | Builds the source-release ls-remote environment through build_subprocess_env. |
| [Kanban workspace_source: hardened publish secret scan](mirror-3622-b-workspace-source-secret-scan-hardening.md) | 3599 | Scans the exact pushed objects in an isolated git environment instead of parsing diff text. |
| [Kanban workspace_source: hardened publish secret scan](mirror-3622-b-workspace-source-secret-scan-hardening.md) | 3600 | Matches the entire raw bytes of every new object and drops commit parsing. |
| [Kanban workspace_source: move, reclaim, CLI claim, source-release and docs](mirror-3622-b-workspace-source-move-claim-cli.md) | 3601 | Folds source-release wiring to keep kanban.py at its 2000-line cap. |
| [Kanban workspace_source: hardened publish secret scan](mirror-3622-b-workspace-source-secret-scan-hardening.md) | 3602 | Matches tree entries by name only and restricts PII-style patterns to text blobs. |
| [Kanban workspace_source: move, reclaim, CLI claim, source-release and docs](mirror-3622-b-workspace-source-move-claim-cli.md) | 3603 | Moves the claim command into kanban_claim_cli.py to keep kanban.py under its line cap. |
| [Kanban workspace_source: move, reclaim, CLI claim, source-release and docs](mirror-3622-b-workspace-source-move-claim-cli.md) | 3604 | Reads absent source columns as NULL so claim, move/reclaim and source-release work on unsynced boards. |
| [Kanban hub and worker-lease hardening: probe writes, uid-bound completions, closed chat links, assignee fence](mirror-3622-c-kanban-hub-and-lease-hardening.md) | 3605 | Test-only: pins the hub-admission verdict timestamp to the Board's holder claim instead of the later lock claim to remove a one-second flake. |
| [Desktop sidebar dot: held cards keep "running", owner-route changes repaint, store list built lazily](mirror-3622-c-desktop-dot-held-label-and-lazy-stores.md) | 3606 | A held card no longer reads "waiting" on the sidebar dot, and owner-route stores are subscribed so owner changes repaint at once. |
| [Desktop sidebar dot: held cards keep "running", owner-route changes repaint, store list built lazily](mirror-3622-c-desktop-dot-held-label-and-lazy-stores.md) | 3607 | Builds the dot row store list lazily and memoized on first use instead of at import, so partial store mocks load. |
| [Update-pause requeue: a Stop latched during the write cancels the head](mirror-3622-c-update-requeue-stop-cancels-head.md) | 3608 | A Stop that latches during the update-hold requeue write now cancels the re-queued row instead of letting it run after the hold. |
| [Kanban hub and worker-lease hardening: probe writes, uid-bound completions, closed chat links, assignee fence](mirror-3622-c-kanban-hub-and-lease-hardening.md) | 3609 | A held card's silent remote wake probe calls skip_wake first, so sweeps stop writing an episode barrier each time. |
| [Desktop kanban events: a board refresh that finds a read in flight re-reads after it settles](mirror-3622-c-desktop-board-refresh-after-inflight-read.md) | 3610 | A trailing board refresh that finds a read in flight waits for it to settle and then re-reads once, without cancelling or joining it. |
| [Kanban hub and worker-lease hardening: probe writes, uid-bound completions, closed chat links, assignee fence](mirror-3622-c-kanban-hub-and-lease-hardening.md) | 3611 | Completion receipts carry stable uids so a hub card completed on a pool worker is verified on the hub, and chat-tasks keeps released closed cards. |
| [Kanban hub and worker-lease hardening: probe writes, uid-bound completions, closed chat links, assignee fence](mirror-3622-c-kanban-hub-and-lease-hardening.md) | 3612 | Dashboard PATCH and bulk assignee writes on a card under another machine's live Board lease are refused like the CLI reassign. |
| [Desktop renderer chunk check: budget sized for a throttled background build](mirror-3622-c-desktop-chunk-check-budget.md) | 3613 | The renderer chunk syntax check defaults to a 30 minute budget, overridable by environment variable, and names the budget on overrun. |
| [Kanban CI progress: pool scan when runner lists are unreadable](mirror-3622-d-kanban-ci-progress-pool-scan.md) | 3614 | Scan every in-progress run (batched, cached 60 s) for the pool's job when runner lists are unreadable. |
| [Kanban CI progress: pool scan when runner lists are unreadable](mirror-3622-d-kanban-ci-progress-pool-scan.md) | 3615 | Keep the bound Hermes home on pool-scan reader threads and key the scan cache by home. |
| [SessionDB schema memo keeps retrying failed repairs](mirror-3622-d-session-db-schema-memo-heals.md) | 3616 | Run the NULL-active heal on memo hits, refuse to memoize a store without the unique title index, and make the marker heal atomic. |
| [SessionDB schema memo keeps retrying failed repairs](mirror-3622-d-session-db-schema-memo-heals.md) | 3617 | Back off a failing schema-memo heal 30 s, warn once per streak, and let a failed full init override another key's claim. |
| [Desktop idle-motion pause re-inserts after stale removal](mirror-3622-d-desktop-idle-motion-pause-reinsert.md) | 3618 | Insert the idle-motion pause sheet only after the stale sheet's removal settles, so repeated insertCSS keys cannot delete it. |
| [Kanban test harness: portable fd paths and JS-faithful sync fake](mirror-3622-d-kanban-test-harness-portability-and-js-fidelity.md) | 3619 | Resolve fd paths portably (F_GETPATH on macOS, /proc on Linux) in the scratch-archive fsync tests. |
| [Kanban test harness: portable fd paths and JS-faithful sync fake](mirror-3622-d-kanban-test-harness-portability-and-js-fidelity.md) | 3620 | Make the kanban sync fake's release path coerce inputs like JS Number()/String() with nullish defaults. |
| [CI fetch-merge-base full-history fallback](mirror-3622-d-ci-fetch-merge-base-fallback.md) | 3621 | Fall back to a full-history fetch (restoring partial-clone keys) when the filtered merge-base fetch fails. |
| [Lifecycle guard: clustered shell options and reserved-word walk](mirror-3622-d-lifecycle-guard-shell-options.md) | 3622 | Handle clustered shell -o values, --rcfile and external time in the lifecycle guard via shared option and reserved-word walks. |

## Mirror 3556–3577

Patches through `346b6b7b34`; verification in [mirror-3577-verification.md](mirror-3577-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Remote-owner pass on its own watcher, and wake-probe pinning](mirror-3577-a-remote-owner-watcher.md) | 3556 | Moves the remote-owner notice pass onto its own supervised watcher and scans the transcript only once for expired notices. |
| [Kanban assign fence, pool-worker home-marker placement, held-default stall liveness](mirror-3577-a-kanban-placement-fences.md) | 3557 | Refuses `hermes kanban assign` on a card under another machine's live lease before writing. |
| [Kanban assign fence, pool-worker home-marker placement, held-default stall liveness](mirror-3577-a-kanban-placement-fences.md) | 3558 | Classifies home-gateway-marker hits on pool workers so cards queue to the pool unless the worker holds the needed state. |
| [Kanban assign fence, pool-worker home-marker placement, held-default stall liveness](mirror-3577-a-kanban-placement-fences.md) | 3559 | Stops stale or draining machines from silencing the held-default stall alarm. |
| [Darwin holder scan: warn on failing background refreshes](mirror-3577-a-darwin-holder-scan-warnings.md) | 3560 | Logs a rate-limited warning when a refresh-only holder scan expires or errors, and drops a dead length check. |
| [Owner-forward safety: pause notes, unconfirmed receipts, ledger refusal order](mirror-3577-a-owner-forward-pause-notes.md) | 3561 | Unifies the pause-note prefix and keeps the backoff for transient owner-forward failures with a retry cap. |
| [Owner-forward safety: pause notes, unconfirmed receipts, ledger refusal order](mirror-3577-a-owner-forward-pause-notes.md) | 3562 | Treats a receipt naming an admission without a confirmed state as unconfirmed and never resends it. |
| [CI review_status: a scan with no verdict is "did not run"](mirror-3577-a-review-status-scan-not-run.md) | 3563 | Makes review_status emit report an action_required "did not run" result when a scan wrote no verdict. |
| [Owner-forward safety: pause notes, unconfirmed receipts, ledger refusal order](mirror-3577-a-owner-forward-pause-notes.md) | 3564 | Reads every ledger row non-forwardable first in the legacy turn refusal so prompts are not forwarded past an unknown admission. |
| [Kanban scratch archive: offline, partial-clone aware](mirror-3577-a-scratch-archive-partial-clone.md) | 3565 | Makes scratch archiving offline and records promisor-only base blobs instead of lazy-fetching them. |
| [Remote-owner pass on its own watcher, and wake-probe pinning](mirror-3577-a-remote-owner-watcher.md) | 3566 | Adds tests pinning that a non-landing wake keeps a live card's probe row and a landed wake drops it. |
| [Core-update pause winds down a background review](mirror-3577-b-update-pause-background-review.md) | 3567 | A core-update pause stops a running background review at its next step boundary, requeues it, and labels admission reservations with their owner. |
| [Test pins for scratch-archive fixture races and managed-worker teardown](mirror-3577-b-test-race-pins.md) | 3568 | Scratch-archive test fixture git runs with auto-maintenance off, plus a guard test against background maintenance. |
| [Lifecycle guard scans bodies of `function NAME { … }`](mirror-3577-b-lifecycle-guard-function-keyword.md) | 3569 | The lifecycle guard peels the `function NAME` keyword so scripts called in its body are scanned. |
| [Test pins for scratch-archive fixture races and managed-worker teardown](mirror-3577-b-test-race-pins.md) | 3570 | Test pinning that a lost managed worker stays unknown when its close raises. |
| [Kanban sync: push before pulling, bounded pulls, outbox-age alarm](mirror-3577-b-kanban-sync-push-before-pull.md) | 3571 | Sync rounds push before pulling, bound each pull to a page budget, allow one puller per board, and alarm on outbox age. |
| [Kanban sync: push before pulling, bounded pulls, outbox-age alarm](mirror-3577-b-kanban-sync-push-before-pull.md) | 3572 | Rounds behind the feed head hold run rows, busy rounds are skipped, and reconcile pulls in chunks. |
| [Kanban sync: push before pulling, bounded pulls, outbox-age alarm](mirror-3577-b-kanban-sync-push-before-pull.md) | 3573 | Two order-dependent sync tests follow push-before-pull and the outbox-age alarm message is amended. |
| [Kanban sync: push before pulling, bounded pulls, outbox-age alarm](mirror-3577-b-kanban-sync-push-before-pull.md) | 3574 | Moves the completion heal into the divergence module for the file-size cap. |
| [Kanban sync: push before pulling, bounded pulls, outbox-age alarm](mirror-3577-b-kanban-sync-push-before-pull.md) | 3575 | Claim-screen test suites opt out of same-round promotion and the fake Board serves reserve. |
| [Kanban sync: push before pulling, bounded pulls, outbox-age alarm](mirror-3577-b-kanban-sync-push-before-pull.md) | 3576 | The pre-head push holds a status for a card the Board already has as done. |
| [Pickup ranking tolerates a candidate that finished between reads](mirror-3577-b-pickup-rank-vanished-candidate.md) | 3577 | Ranking uses the listed row for a candidate that vanished between replica reads instead of raising KeyError. |

## Mirror 3530–3555

Patches through `da34cee8c4`; verification in [mirror-3555-verification.md](mirror-3555-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Kanban CLI refuses lifecycle writes under another machine's live lease](mirror-3555-a-kanban-remote-lease-cli-refusal.md) | 3530 | Refuses every CLI lifecycle verb on a card under another machine's live Board lease before any write, sharing the tool-side message. |
| [Lifecycle guard: output-sink quote pairing and echo/printf producers](mirror-3555-a-lifecycle-guard-output-sink.md) | 3531 | Fixes quote pairing, adds echo/printf producers and treats a piped grep for bash as a sink in the guard's output-sink walk. |
| [Desktop completion obligations and lineage walk follow-ups](mirror-3555-a-tui-completion-and-lineage-nits.md) | 3532 | Adds an in-memory owed-obligation set so Desktop notice-turn start skips receipt reads, and updates docstrings. |
| [Desktop remote-notice sweep stands aside while the gateway's turn holds a chat](mirror-3555-a-kanban-remote-sweep-stand-aside.md) | 3533 | Makes the Desktop remote-notice sweep stand aside while the gateway's unexpired turn lease holds the chat. |
| [Kanban pickup-stall alarm for a held machine's default-placement cards](mirror-3555-a-kanban-pickup-stall-held.md) | 3534 | Files one alarm for a held machine's default-placement cards no other machine can copy, and lets the re-place scope accept them. |
| [Desktop completion obligations and lineage walk follow-ups](mirror-3555-a-tui-completion-and-lineage-nits.md) | 3535 | Defaults the session lineage walk to the shared lineage limit and corrects lineage docstrings with no behavior change. |
| [Kanban follow-ups: review-reopen pin, capability wake text, parent-missing clear](mirror-3555-a-kanban-lifecycle-followups.md) | 3536 | Adds tests pinning review_reopened as a reopen in the terminal guard. |
| [Kanban follow-ups: review-reopen pin, capability wake text, parent-missing clear](mirror-3555-a-kanban-lifecycle-followups.md) | 3537 | Makes capability-owner rejection wake text generation-aware and fixes overlap docs and comments. |
| [Kanban follow-ups: review-reopen pin, capability wake text, parent-missing clear](mirror-3555-a-kanban-lifecycle-followups.md) | 3538 | Records parents_unlinked as the self-clear source and posts the repair note as a system comment. |
| [Kanban repair-unblock audit and CI-run progress refinements](mirror-3555-b-kanban-repair-audit-and-ci-progress.md) | 3539 | Writes the missing-parent requeue audit row when the home applies a forwarded repair unblock. |
| [Update-pause cancelled-JSON handling and multiplex test diagnostics](mirror-3555-b-cancel-json-and-multiplex-test-diagnostics.md) | 3540 | Test-only: names the boot-time park when the multiplex downtime-ensure precondition fails. |
| [Desktop Kanban current-board alias cached for 30 s](mirror-3555-b-desktop-board-alias-cache.md) | 3541 | Caches the current-board alias for 30 s so board reads stop triggering a boards read each. |
| [Kanban host lease and remote notice index hardening](mirror-3555-b-kanban-host-lease-and-remote-index.md) | 3542 | Restarts remote index holder backoff on re-acquire or new stamp and pins the same-process lease hand-off. |
| [Kanban repair-unblock audit and CI-run progress refinements](mirror-3555-b-kanban-repair-audit-and-ci-progress.md) | 3543 | Refines CI-run progress wording, idle/busy runner handling, busy cap, sweep race and stranded-release guard. |
| [Kanban host lease and remote notice index hardening](mirror-3555-b-kanban-host-lease-and-remote-index.md) | 3544 | Beats the host lease after a raising pass, sizes wake slots by real lease ownership and reuses one fanout connection. |
| [Update-pause cancelled-JSON handling and multiplex test diagnostics](mirror-3555-b-cancel-json-and-multiplex-test-diagnostics.md) | 3545 | Treats the executor's cancelled JSON as no own result for the update-pause stamp. |
| [Session create/resume reply bounded on the banner inventory](mirror-3555-b-session-create-inventory-budget.md) | 3546 | Bounds the session create/resume reply to 2 s of inventory wait and publishes full info later. |
| [Relay managed stream: no stranger-thread close of a provider stream](mirror-3555-c-relay-stream-close.md) | 3547 | Defers closing a provider stream to its reading thread (shutdown only from other threads) and makes managed-stream close wait for abandoned reads. |
| [Update-pause wake re-queue and managed-worker teardown hardening](mirror-3555-c-update-pause-teardown-hardening.md) | 3548 | Gates unsupported-scope notifier wakes at turn start under the update hold and re-queues steering transfers together with the wake. |
| [Update-pause wake re-queue and managed-worker teardown hardening](mirror-3555-c-update-pause-teardown-hardening.md) | 3549 | Suppresses teardown BrokenPipeError in the managed worker and routes owner teardown through close_quietly so a finished turn keeps its answer. |
| [Scratch archive: .git marker judging through the scan directory fd](mirror-3555-c-scratch-archive-git-markers.md) | 3550 | Judges .git markers via the scan's own directory fd, adds an on_git_file callback, and documents the gzip level 1 size cost. |
| [Scratch archive: .git marker judging through the scan directory fd](mirror-3555-c-scratch-archive-git-markers.md) | 3551 | Treats an unjudged .git marker as a gitlink so the workspace is kept instead of raising KeyError. |
| [CI diff-read failures and lifecycle guard interpreter coverage](mirror-3555-c-ci-and-lifecycle-guards.md) | 3552 | Makes the supply-chain scans and base-snapshot extraction fail when git cannot read the PR diff or base tree. |
| [CI diff-read failures and lifecycle guard interpreter coverage](mirror-3555-c-ci-and-lifecycle-guards.md) | 3553 | Extends the lifecycle guard to scan uv run, py/pypy runners and programs fed on stdin. |
| [Kanban remote stand-aside lease matching and held-card wake recovery test](mirror-3555-c-kanban-remote-wake-followups.md) | 3554 | Matches the lease holder against both verified gateway pids in the remote stand-aside and tests the compacted-chat case. |
| [Kanban remote stand-aside lease matching and held-card wake recovery test](mirror-3555-c-kanban-remote-wake-followups.md) | 3555 | Documents that the held-card skip in _wake covers every producer and pins probe recovery after the hold lifts. |

## Mirror 3502–3529

Patches through `43899644c6`; verification in [mirror-3529-verification.md](mirror-3529-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Done chat names the cards it still watches](mirror-3529-a-done-chat-names-watched-cards.md) | 3502 | Done outcome carries watch-parked cards, with an eye glyph, tooltip and done_watching audit. |
| [/model refuses concrete pins the managed routing registry rejects](mirror-3529-a-model-switch-refuses-unregistered-pin.md) | 3503 | switch_model refuses a pin the managed registry rejects and the error names the allowed pins. |
| [Kanban chat-links diff ignores machine chats and unsettled closed cards](mirror-3529-a-kanban-chat-links-diff-parity.md) | 3504 | links_diff leaves out machine chats and a released sibling no longer settles a closed card. |
| [Aux release_client defer hardening and SSL-close config test](mirror-3529-a-aux-release-client-defer-hardening.md) | 3505 | release_client defers inside the sweep try so a raising defer cannot strand the daemon, plus an SSL-close config test. |
| [Kanban capability-owner requests: placeholder exemption and origin-link fixes](mirror-3529-a-kanban-owner-origin-routing.md) | 3506 | Capability-owner creates skip the requester's bound placeholder, and a rejected owner request wakes the card with the refusal. |
| [Respawn guard resumes after a historical parent's exact-head approval](mirror-3529-a-respawn-guard-historical-review.md) | 3507 | Respawn guard resumes a captured PR approved before the park by a historical parent at the same head. |
| [Kanban capability-owner requests: placeholder exemption and origin-link fixes](mirror-3529-a-kanban-owner-origin-routing.md) | 3508 | Single OWNER_WRITTEN source, clarified origin_link_fields semantics, and a done-row carry test. |
| [Docs: one-shot budget warning default and turn-cap requirement](mirror-3529-a-budget-warning-ratio-docs.md) | 3509 | Config docs state the 80% one-shot and delegate-child budget warning default. |
| [Docs: one-shot budget warning default and turn-cap requirement](mirror-3529-a-budget-warning-ratio-docs.md) | 3510 | Config docs note the one-shot 80% default needs a finite turn cap. |
| [Kanban config docs: budget checkpoint default and worktree retention hours](mirror-3529-b-kanban-config-docs.md) | 3511 | Documents that the Kanban 90% checkpoint default wins over the one-shot 80% default. |
| [Lifecycle guard scans scripts behind shell reserved words](mirror-3529-b-lifecycle-guard-reserved-words.md) | 3512 | Makes the lifecycle guard scan scripts, interpreters, shell payloads and launchctl submits that sit behind shell reserved words and `time`. |
| [Kanban CI progress, worker eviction and remote notice fixes](mirror-3529-b-kanban-reliability-fixes.md) | 3513 | Treats a self-hosted job as busy rather than not_starting when runner lists are unreadable but a job on the same labels is running or recently started. |
| [Update pause re-queues a notifier wake claimed before the hold](mirror-3529-b-update-pause-claimed-wake.md) | 3514 | Adds a turn-start gate so a notifier wake claimed before an update hold is re-queued unstarted instead of running inside the hold. |
| [Client closes idle keep-alive sockets before the backend does](mirror-3529-b-remote-keepalive-idle-close.md) | 3515 | Raises the backend keep-alive idle timeout to 120 s and makes Desktop pooled agents close idle sockets after 30 s. |
| [Kanban CI progress, worker eviction and remote notice fixes](mirror-3529-b-kanban-reliability-fixes.md) | 3516 | Makes the open-chat notice skip re-check the poller's claim row and look it up by the link session, for diagnostic logging only. |
| [Kanban CI progress, worker eviction and remote notice fixes](mirror-3529-b-kanban-reliability-fixes.md) | 3517 | Keeps origin-adopted copies during worker eviction, deletes each copy in its own savepoint, and lets pickup continue if eviction fails. |
| [Kanban config docs: budget checkpoint default and worktree retention hours](mirror-3529-b-kanban-config-docs.md) | 3518 | Documents kanban.worktree_retention_hours and seeds it as None in the default config. |
| [Kanban CI progress, worker eviction and remote notice fixes](mirror-3529-b-kanban-reliability-fixes.md) | 3519 | Corrects the `_collect_kanban_notifications` docstring; no behavior change. |
| [Kanban stale-wake probe cleanup and PR-park respawn guard](mirror-3529-c-kanban-stale-wake-and-pr-park-guard.md) | 3520 | An ignored stale wake now deletes the card's probe row and wake episode in the same transaction. |
| [Kanban stale-wake probe cleanup and PR-park respawn guard](mirror-3529-c-kanban-stale-wake-and-pr-park-guard.md) | 3521 | The respawn guard counts PRs named by the latest orderly park as known, so a request-review park is not held. |
| [Kanban placement: labels shape guard and forward-evidence fixes](mirror-3529-c-kanban-placement-hardening.md) | 3522 | The linux-label check uses the list-of-str guard and registry_carries is hoisted to module level. |
| [Kanban placement: labels shape guard and forward-evidence fixes](mirror-3529-c-kanban-placement-hardening.md) | 3523 | Unclosed evidence markers no longer swallow card text and the footer names the board of the opened DB. |
| [Kanban schema-owner and remote-index test hardening](mirror-3529-c-kanban-schema-and-index-test-pins.md) | 3524 | Tests pin the per-mode at-par reference cache and the CLI missing-board refusal line. |
| [restart_ignore_paths: rev-parse no-answer pin](mirror-3529-c-restart-ignore-paths-rev-parse-pin.md) | 3525 | Pins the rev-parse no-answer branch with a test and softens the never-comes-back comments. |
| [Desktop sidebar background dot: live stores, listed-row fallback, waiting label](mirror-3529-c-desktop-sidebar-waiting-dot.md) | 3526 | Background dot subscribes to all row execution stores, falls back to the listed row, and adds an Open work, waiting label in 8 locales. |
| [Aux Gemini cancel-mid-stream close and socket shutdown](mirror-3529-c-aux-gemini-cancel-close.md) | 3527 | Gemini cancel closes once on the reading worker and its httpx sockets are swept and shut down. |
| [Annotation host copy: per-profile demand, early renewal, disk copy v2](mirror-3529-c-annotation-host-copy-renewal.md) | 3528 | Holder demand is checked per profile, the copy renews before expiry, and the disk copy version becomes 2. |
| [Kanban schema-owner and remote-index test hardening](mirror-3529-c-kanban-schema-and-index-test-pins.md) | 3529 | The remote-index republish test waits for the publishing pass before counting publishes. |

## Mirror 3492–3501

Patches through `4e3a6ab837`; verification in [mirror-3501-verification.md](mirror-3501-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Kanban placement: malformed registry labels fail closed](mirror-3501-a1-kanban-placement-labels-fail-closed.md) | 3492 | label_machines judges registry labels via _carries so non-list-of-str labels fail closed while missing labels stay an empty set. |
| [Remote notice index: busy is not corrupt, holder backoff, role upgrade](mirror-3501-a2-remote-notice-index-busy-backoff.md) | 3493 | Busy index copies are skipped not deleted, failing holders back off up to 60 s, no holder without flock, and gateway claims upgrade a serve holder. |
| [Kanban restart continuation keeps a stopped call's own result](mirror-3501-a3-kanban-restart-keeps-stopped-call-result.md) | 3494 | Restart continuation rewrites stopped tool rows with the interrupted stamp plus the call's own truncated result and an end line. |
| [Update-pause continuations hand off to the gateway owner](mirror-3501-b1-update-pause-gateway-owner.md) | 3495 | Forward a ledger-refused paused continuation to the owner's FIFO, retire the pause, and reword the stale-continuation note. |
| [Update-pause continuations hand off to the gateway owner](mirror-3501-b1-update-pause-gateway-owner.md) | 3496 | Name the chain when a continuation that paused again is replayed or queued. |
| [Update-pause continuations hand off to the gateway owner](mirror-3501-b1-update-pause-gateway-owner.md) | 3497 | Exclude the paused turn's own request row from the later-turns count. |
| [Concurrent session writes share owner reads during gateway start](mirror-3501-b2-session-owner-shared-reads.md) | 3498 | Single-flight shared owner reads per home for writes issued while the gateway is starting. |
| [Kanban schema owner: sync DDL in the reference, no phantom boards](mirror-3501-b3-kanban-schema-owner-sync.md) | 3499 | Include sync DDL in the at-par reference, refuse to create a missing board, and print a one-line CLI refusal. |
| [Kanban schema owner: sync DDL in the reference, no phantom boards](mirror-3501-b3-kanban-schema-owner-sync.md) | 3500 | Docstring and comment clarifications for the links-unchanged re-record condition; no behavior change. |
| [Host loop lease: hand-off waiter refreshes its own .want](mirror-3501-b4-host-loop-lease-want-refresh.md) | 3501 | Refresh a waiting standby's .want from its own daemon thread, with held() as fallback. |

## Mirror 3474–3491

Patches through `a0307c048c`; verification in [mirror-3491-verification.md](mirror-3491-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Kanban missing-parent self-clear and repair-grant hardening](mirror-3491-a-kanban-parent-missing-repair.md) | 3474 | A parentless detector-blocked child returns to ready and the open repair card is commented when the block clears. |
| [Desktop kanban drawer: pin refetch-restart reads as polls](mirror-3491-a-desktop-kanban-drawer-retry-pin.md) | 3475 | Adds a drawer test pinning that a fetch replacing a failed one in its retry delay reads as polls, and documents retryingFetch internals. |
| [Desktop kanban drawer: pin refetch-restart reads as polls](mirror-3491-a-desktop-kanban-drawer-retry-pin.md) | 3476 | Documents the by-design open-read settle and lengthens the test retry delay to 10 s to avoid runner flakiness. |
| [Kanban worktree GC: sub-day retention in hours](mirror-3491-a-kanban-worktree-retention-hours.md) | 3477 | Adds optional kanban.worktree_retention_hours that overrides the days key for worktree GC. |
| [Review follow-ups: code-skew drift cache, move-guard timeout cause, quota proof release](mirror-3491-a-review-nit-followups-drift-move-quota.md) | 3478 | Caches a missing-commit drift verdict as skew and tightens related docstring and test. |
| [Kanban missing-parent self-clear and repair-grant hardening](mirror-3491-a-kanban-parent-missing-repair.md) | 3479 | Repair grant forwards only its own child, names the release-event invariant, and audits the unblock tool branch. |
| [Review follow-ups: code-skew drift cache, move-guard timeout cause, quota proof release](mirror-3491-a-review-nit-followups-drift-move-quota.md) | 3480 | Pins the move-guard timeout cause text with tests and updates the limitations doc line. |
| [Review follow-ups: code-skew drift cache, move-guard timeout cause, quota proof release](mirror-3491-a-review-nit-followups-drift-move-quota.md) | 3481 | Logs a failed quota proof release at DEBUG instead of swallowing it and documents every release path. |
| [Kanban session-list annotation: shared host copy](mirror-3491-a-kanban-annotation-host-copy.md) | 3482 | Publishes one fingerprinted host copy of the session-list annotation that other processes adopt, with a holder refresher and kill switch. |
| [Lifecycle guard: public command walk and sync hint](mirror-3491-b-lifecycle-guard-sync-hint.md) | 3483 | Makes the command walk public as executes_command_named and lets the sync hint see through shell options and reserved words. |
| [Delta responses: Cache-Control no-store](mirror-3491-b-delta-response-no-store.md) | 3484 | Replaces Cache-Control with no-store on responses that actually send a delta frame. |
| [Kanban review follow-ups: chat links, forwarding, unblock guard](mirror-3491-b-kanban-review-follow-ups.md) | 3485 | Releases a compressed chat's owner-written ancestor link row, skips cron chats in the owner, and indexes task_id. |
| [Auxiliary client: no stranger-thread close of TLS sockets](mirror-3491-b-aux-no-stranger-thread-close.md) | 3486 | Hands to_thread workers' clients off safely on cancellation and stops closing a replaced cached client under a live read. |
| [Kanban CI-run progress sweep follow-ups](mirror-3491-b-kanban-ci-progress-sweep.md) | 3487 | Makes a woken card the immediate probe, separates saturated from offline runners, and bounds the sweep off-thread. |
| [Kanban review follow-ups: chat links, forwarding, unblock guard](mirror-3491-b-kanban-review-follow-ups.md) | 3488 | Corrects the unchecked-worktree cause text to say the filter discovery gave no usable answer. |
| [Desktop notify receipts survive a backend crash](mirror-3491-b-desktop-notify-delivery-obligation.md) | 3489 | Records an owner-marked delivery obligation for Desktop notify jobs and re-delivers the notice after an unclean backend death. |
| [Kanban review follow-ups: chat links, forwarding, unblock guard](mirror-3491-b-kanban-review-follow-ups.md) | 3490 | Fences forwarded parent handoffs as placement evidence and names the source board in the footer. |
| [Kanban review follow-ups: chat links, forwarding, unblock guard](mirror-3491-b-kanban-review-follow-ups.md) | 3491 | Makes unblock_task refuse a stale copy of a done card and moves reopen_review_task to its own module. |

## Mirror 3424–3473

Patches through `bfe6e0898c`; verification in [mirror-3473-verification.md](mirror-3473-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Desktop remote-update boot wait: hard cap and shared refusal phrase](mirror-3473-a-desktop-remote-update-wait-cap.md) | 3424 | Makes the 30-minute remote-update wait cap hard and shares one refusal-phrase constant across producers and the renderer. |
| [Kanban background loops: CI sweep, remote-notice claims, quota proof gate](mirror-3473-a-kanban-loop-hardening.md) | 3425 | Keeps the CI-run sweep from raising out of the scope bind and tightens the per-home warning test. |
| [Restart-free code-skew rule: submodules, resolved git, no cached transient failures](mirror-3473-a-restart-free-skew-rule.md) | 3426 | Counts submodule bumps, resolves git via the platform resolver, avoids caching transient git failures, and applies the rule to import errors. |
| [Kanban: a stale wake never moves a done card out of done](mirror-3473-a-kanban-stale-wake-terminal-guard.md) | 3427 | Adds the terminal-hold guard so wakes cannot reopen done cards in producers, push, feed apply and divergence repair. |
| [Kanban: a stale wake never moves a done card out of done](mirror-3473-a-kanban-stale-wake-terminal-guard.md) | 3428 | Keeps a held reopen alive past other notes and lets divergence repair still send a live local reopen. |
| [Kanban: a stale wake never moves a done card out of done](mirror-3473-a-kanban-stale-wake-terminal-guard.md) | 3429 | Classifies the terminal guard's restores as not-from-running in the source-receipt site test. |
| [Kanban: a stale wake never moves a done card out of done](mirror-3473-a-kanban-stale-wake-terminal-guard.md) | 3430 | Joins a line wrap to keep the sync client under its line cap, with no behavior change. |
| [Kanban background loops: CI sweep, remote-notice claims, quota proof gate](mirror-3473-a-kanban-loop-hardening.md) | 3431 | Stops the remote-notice claim log from stranding a claim and downgrades the healthy open-chat skip log to DEBUG. |
| [Test fixture: session-context reset waits for an in-flight import](mirror-3473-a-test-session-context-reset-race.md) | 3432 | Makes the session-context reset fixture wait on an in-flight import and stubs incident threads in latch tests. |
| [Kanban background loops: CI sweep, remote-notice claims, quota proof gate](mirror-3473-a-kanban-loop-hardening.md) | 3433 | Stops quota recovery from benching accounts without a failed proof and tests the SubprocessError catch. |
| [Chat custody: lineage ledger reads every row; managed-worker orphan wording](mirror-3473-a-chat-custody-lineage.md) | 3434 | Corrects the keyless-orphan recovery comment and makes a test stub default explicit. |
| [Chat custody: lineage ledger reads every row; managed-worker orphan wording](mirror-3473-a-chat-custody-lineage.md) | 3435 | Reads every non-terminal ledger row per id and walks the same parent chain on the gateway as the legacy leg. |
| [Kanban placement: portable forward footer and registry clock-skew tolerance](mirror-3473-a-kanban-placement-portability-freshness.md) | 3436 | Spells the forwarded-card parent-handoff footer as a cross_task read call so cards stay portable. |
| [Lifecycle guard: scan files run as `python3 <file>`](mirror-3473-a-lifecycle-guard-python-interpreter.md) | 3437 | Scans files run through a Python interpreter for gateway lifecycle commands. |
| [Delta responses: held-start flush, Vary and ETag handling, one origin per scope](mirror-3473-a-delta-response-followups.md) | 3438 | Flushes a held start before pathsend, adds Vary and drops ETag on deltas, and keeps one origin per Desktop scope. |
| [Kanban placement: portable forward footer and registry clock-skew tolerance](mirror-3473-a-kanban-placement-portability-freshness.md) | 3439 | Allows 5 s of registry clock skew and applies the same freshness window to named machines. |
| [SessionDB: memoized schema init per settled store](mirror-3473-a-sessiondb-schema-init-memo.md) | 3440 | Memoizes the schema init per settled store and throttles the delegate-marker heal. |
| [Host loop lease: want-record republish, expiry and hand-off release](mirror-3473-b-host-loop-lease-handoff.md) | 3441 | Waiters republish and expire `.want` records, `release()` during a hand-off wait works, and holder metadata is fixed length. |
| [Kanban: parent_missing block clears itself](mirror-3473-b-kanban-parent-missing-self-clear.md) | 3442 | The readiness pass unblocks a child whose detector-made parent_missing block no longer applies. |
| [Session writes ride out a starting gateway](mirror-3473-b-session-writes-during-gateway-start.md) | 3443 | Owner discovery re-reads a starting gateway within a 24 s budget and otherwise answers a retryable 503 without drain advice. |
| [Session writes ride out a starting gateway](mirror-3473-b-session-writes-during-gateway-start.md) | 3444 | The standalone session-mutation test asserts the new starting detail and caps its budget. |
| [Evidence-based work state observations (execution observations v1)](execution-observations.md) | 3445 | A remote card whose adapter raised projects one adapter_error record (with its ask) in the session task list and the sidebar counts; a raising running-evidence adapter no longer escapes the counts. |
| [Kanban remote notices: legacy-poller skip and shared host index](mirror-3473-b-kanban-remote-notice-index.md) | 3446 | The legacy poller skips remote notice collection on gateway-owned chats. |
| [Kanban remote notices: legacy-poller skip and shared host index](mirror-3473-b-kanban-remote-notice-index.md) | 3447 | Publishes one per-host remote notice index db per replica state with a leased change-driven refresher. |
| [Move guard: one git deadline per tree scan](mirror-3473-b-move-guard-git-budget.md) | 3448 | A GitBudget draws every move-guard git call from one deadline and reads an unchecked state when spent. |
| [Kanban: only the installed core migrates a board; SchemaAhead alert](mirror-3473-b-kanban-board-schema-owner.md) | 3449 | Non-installed checkouts open boards read-only when a migration is needed, and a SchemaAhead give-up files a hub card. |
| [Kanban: only the installed core migrates a board; SchemaAhead alert](mirror-3473-b-kanban-board-schema-owner.md) | 3450 | A non-installed core at par keeps sync mode, and a closed alert card is not reused. |
| [Kanban: only the installed core migrates a board; SchemaAhead alert](mirror-3473-b-kanban-board-schema-owner.md) | 3451 | Routes the schema-owner module to the upgrade CI lane and pins UTF-8 on the alert's git probe. |
| [Kanban: only the installed core migrates a board; SchemaAhead alert](mirror-3473-b-kanban-board-schema-owner.md) | 3452 | on_give_up records the stop itself to keep the sync client under its line cap. |
| [Kanban: only the installed core migrates a board; SchemaAhead alert](mirror-3473-b-kanban-board-schema-owner.md) | 3453 | The at-par reference schema replays connect's own schema steps so a missing table is never judged at par. |
| [Kanban: only the installed core migrates a board; SchemaAhead alert](mirror-3473-b-kanban-board-schema-owner.md) | 3454 | on_give_up formats the board-refusal pairs itself to keep the sync client under its line cap. |
| [Kanban wake-check tick and CI sweep hardening](mirror-3473-b-kanban-wake-check-hardening.md) | 3455 | The wake-check tick logs any raising pool future and a CI sweep close failure keeps the sweep result. |
| [Test-runner seed opt-out parity and MCP refresh test fix](mirror-3473-b-test-and-script-hardening.md) | 3456 | run_tests.sh trims the account-seed opt-out value like pm, with the marker and env var documented. |
| [Kanban scratch archive: gzip level 1 and uv `.git` marker as data](mirror-3473-b-kanban-scratch-archive-gzip.md) | 3457 | Scratch archives use gzip level 1 and archive a non-gitdir `.git` file as data. |
| [Test-runner seed opt-out parity and MCP refresh test fix](mirror-3473-b-test-and-script-hardening.md) | 3458 | The MCP refresh thread-safety test reads the tools pair under the publish lock. |
| [Lifecycle guard: consumer heredoc output run by a shell is not data](mirror-3473-b-lifecycle-guard-output-sink.md) | 3459 | The guard detects heredoc consumer output run by a shell and fails closed on printed oversized scripts. |
| [Lifecycle guard: linear-time output-sink regex](mirror-3473-c-lifecycle-guard-sink-regex-linear.md) | 3460 | Replaces `\s*` with `[ \t]*` in the output-sink regex so masked heredoc newline runs scan in linear time, and pins fail-closed scan-cap behavior. |
| [One-shot iteration-budget checkpoint and cap logging](mirror-3473-c-oneshot-iteration-budget-warning.md) | 3461 | One-shot runs default to an 80% iteration-budget notice and cap hits log `iteration_cap_exhausted`. |
| [Code-execution RPC: accurate wire-bytes and oversize-result wording](mirror-3473-c-code-execution-rpc-wording.md) | 3462 | Scopes the surrogate-pair comment to file reads and makes the oversize-result side-effect clause conditional. |
| [Code-execution RPC: accurate wire-bytes and oversize-result wording](mirror-3473-c-code-execution-rpc-wording.md) | 3463 | Corrects the surrogate-pair note to say what changes remotely and drops a wording-only test assert. |
| [Desktop: pause CSS animations while the system is idle](mirror-3473-c-desktop-idle-animation-pause.md) | 3464 | Pauses CSS animations in every Desktop window after 300 s of system idle and resumes on input. |
| [Kanban scratch custody: pressure API fixes](mirror-3473-c-kanban-custody-pressure-nits.md) | 3465 | Malformed receipts yield no owner and pressure candidates skip unmanaged scratch trees. |
| [Kanban: placed create reports `chat_link: owner_written` only when the owner says so](mirror-3473-c-kanban-owner-written-chat-link.md) | 3466 | The owner's courier result reports whether it wrote the origin chat link, and the create note reads only that answer. |
| [Kanban: placed create reports `chat_link: owner_written` only when the owner says so](mirror-3473-c-kanban-owner-written-chat-link.md) | 3467 | Keeps the gateway id verbatim in the owner-written note by capitalizing only the fixed words. |
| [Kanban: placed create reports `chat_link: owner_written` only when the owner says so](mirror-3473-c-kanban-owner-written-chat-link.md) | 3468 | Docs-only: comments name `_owner_hub_links` and the owner_written note states the owner's core/sync requirement. |
| [Sidebar dot: "status unknown" label for unknown-only background work](sidebar-dot-unknown-label.md) | 3469 | A background dot held only by unknown or unfresh work evidence is labelled "Open work, current status unknown" instead of running. |
| [Update-pause cancel keeps the cancelled call's own result](mirror-3473-c-update-pause-cancel-keeps-result.md) | 3470 | Keeps a cancelled call's own text result (head and tail) below the interrupted stamp and rewords the stamp to check the outcome before re-running. |
| [Unloaded-notice sweeper and producer admission: host pass under a lease](mirror-3473-c-host-loop-lease-unloaded-sweeper.md) | 3471 | Runs the host-wide unloaded sweep and producer admission only in a lease holder, with residual per-process passes, wake slots and persisted fanout state. |
| [Kanban sync: fake release validation and links-timing docs](mirror-3473-c-kanban-sync-fake-and-links-docs.md) | 3472 | Test fake validates release failure_limit, block_kind and run_uid like the real release transition. |
| [Kanban sync: fake release validation and links-timing docs](mirror-3473-c-kanban-sync-fake-and-links-docs.md) | 3473 | Documents links-unchanged timing scope and the `_LINKS_DONE` pop, and uses public import names. |

## Mirror 2927–3423

Patches through `4b24631c6e`; verification in [mirror-3423-verification.md](mirror-3423-verification.md).

| Feature | Patches | Scope |
|---|---|---|
| [Kanban replica consistency tests and lag guard](mirror-3423-a-replica-consistency.md) | 2927 | Adds tests pinning last-good run evidence on a failed replica read and re-projection of expired runs in warm counts. |
| [Kanban pool placement and repin fixes](mirror-3423-a-pool-placement.md) | 2928 | Park pin asks the remote before calling URL-pushed work unpushed. |
| [Kanban pool placement and repin fixes](mirror-3423-a-pool-placement.md) | 2929 | Non-pool assignee refusal names the assignee, the pool profiles and the pool route. |
| [One-shot gateway chat: stable retry identities and closed refused redials](mirror-3423-a-gateway-chat-retry.md) | 2930 | Reuses request and input ids across retries of one run and closes refused redial sockets. |
| [CI review bot: retry failed review-status downloads](mirror-3423-a-ci-review-retry.md) | 2931 | Retries failed review-status downloads and marks a failed refresh while queuing one re-refresh. |
| [Process completion custody across gateway restarts](mirror-3423-a-process-completion-custody.md) | 2932 | Writes a delivery obligation with the result receipt so a restart re-admits an owed completion once. |
| [Process completion custody across gateway restarts](mirror-3423-a-process-completion-custody.md) | 2933 | Explicit kill settles the owed notice and settlement runs under the registry lock. |
| [Compression yields during a core-update pause](mirror-3423-a-update-pause-compression.md) | 2934 | Refunds update-pause yields and guards scripted preludes from yielding. |
| [Compression yields during a core-update pause](mirror-3423-a-update-pause-compression.md) | 2935 | Excludes the reserved preflight call from pause eligibility. |
| [Compression yields during a core-update pause](mirror-3423-a-update-pause-compression.md) | 2936 | Resets the compression boundary count before replayed preludes. |
| [Kanban pool placement and repin fixes](mirror-3423-a-pool-placement.md) | 2937 | Pool repin preserves ready, scheduled or todo status with ownership fences intact. |
| [Session lifecycle fixes: remote card outcomes, subagent sweep, deleted-chat resume](mirror-3423-a-session-lifecycle.md) | 2938 | Refreshes chat outcomes when remote cards change. |
| [Session lifecycle fixes: remote card outcomes, subagent sweep, deleted-chat resume](mirror-3423-a-session-lifecycle.md) | 2939 | Ended-parent subagent sweep treats a compressed delegator as ended only when its continuation tip is. |
| [Credential relay pull: isolated subprocess environment](mirror-3423-a-relay-pull-env.md) | 2940 | Isolates relay pull Python imports from the agent runtime. |
| [Credential relay pull: isolated subprocess environment](mirror-3423-a-relay-pull-env.md) | 2941 | Uses the shared subprocess environment factory for the relay pull. |
| [Session lifecycle fixes: remote card outcomes, subagent sweep, deleted-chat resume](mirror-3423-a-session-lifecycle.md) | 2942 | Resume treats deleted chats as terminal and retires their startup markers. |
| [Gateway exec spawn ownership and discovery test stability](mirror-3423-a-gateway-exec-stability.md) | 2943 | Raises the discovery fixture shutdown wait bound to avoid CPU-starvation flakes. |
| [Outcomes: turn-end refresh keeps declared waits and newer turns](mirror-3423-a-outcome-turn-end.md) | 2944 | Turn-end refresh keeps a declared wait through its own turn but not a newer one. |
| [Outcomes: turn-end refresh keeps declared waits and newer turns](mirror-3423-a-outcome-turn-end.md) | 2945 | Turn-end refresh names only its own turn's evidence instead of a snapshot. |
| [Outcomes: turn-end refresh keeps declared waits and newer turns](mirror-3423-a-outcome-turn-end.md) | 2946 | Freezes the concluding identity before readmission. |
| [Update checkpoint accounting, update-pause alarms and update docs](mirror-3423-a-update-checkpoint-alarms.md) | 2947 | Publishes disjoint checkpoint residual accounting for cut cron turns. |
| [Process completion custody across gateway restarts](mirror-3423-a-process-completion-custody.md) | 2948 | Bounds malformed completion receipts and keeps over-cap owed receipts retrievable. |
| [Gateway exec spawn ownership and discovery test stability](mirror-3423-a-gateway-exec-stability.md) | 2949 | Retains exec spawn ownership across cancellation. |
| [Gateway exec spawn ownership and discovery test stability](mirror-3423-a-gateway-exec-stability.md) | 2950 | Adds an exec command success control test. |
| [Hub admission: capacity class metadata on cards](mirror-3423-a-hub-admission-metadata.md) | 2951 | Persists admission class and frees metadata on tasks. |
| [Hub admission: capacity class metadata on cards](mirror-3423-a-hub-admission-metadata.md) | 2952 | Validates admission metadata edits atomically. |
| [Hub admission: capacity class metadata on cards](mirror-3423-a-hub-admission-metadata.md) | 2953 | Exposes admission metadata in CLI creation. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2954 | Adds the opt-in priority claim pool foundation with immutable claim receipts. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2955 | Records priority pool claim evidence and invalidates census on unknown receipts. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2956 | Extends the dispatch budget and captures capacity origins. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2957 | Prioritizes marked candidates within dispatch lanes. |
| [Hub admission: capacity class metadata on cards](mirror-3423-a-hub-admission-metadata.md) | 2958 | Refuses legacy courier placement that would drop admission metadata. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2959 | Reports a coherent admission policy and pool census in machine metrics. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2960 | Documents the Board pool admission and runtime support contract. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2961 | Exposes pool queues in queue notes and resets platform claim receipts. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2962 | Carries original chat custody when importing capacity cards. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2963 | Adds concurrent cross-board pool and own-grant echo receipt tests. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2964 | Keeps marked lane order across replica waiter ranking. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2965 | Adds guard tests for surviving mutants and drops a redundant SQL ordering prefix. |
| [Hub admission phase 2: score ordering, queue explain and capacity report](mirror-3423-a-hub-admission-score.md) | 2966 | Adds deterministic score ordering, queue explain output and a capacity effect report. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2967 | Ports the policy recheck test to the nonblocking CPU sampler seam. |
| [Hub admission phase 2: score ordering, queue explain and capacity report](mirror-3423-a-hub-admission-score.md) | 2968 | Moves the assignees listing out of the CLI facade. |
| [Hub admission phase 2: score ordering, queue explain and capacity report](mirror-3423-a-hub-admission-score.md) | 2969 | Adds tests killing score and report mutation survivors. |
| [Hub admission: capacity class metadata on cards](mirror-3423-a-hub-admission-metadata.md) | 2970 | Keeps foreign chat links out of pickup copies and keeps the courier create envelope JSON and retry-compatible. |
| [Hub admission: capacity class metadata on cards](mirror-3423-a-hub-admission-metadata.md) | 2971 | Updates the Board row-field snapshot for admission class and frees. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2972 | Ports the policy-recheck test to the sampler-thread seam. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2973 | Adds pool-aware dispatch and pickup budgets so normal cards make no claim attempts when regular is full. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2974 | Drops a no-op reservation filter and covers pickup with a pool budget and legacy rows. |
| [Hub admission: priority claim pools and capacity budgets](mirror-3423-a-hub-admission-pools.md) | 2975 | Keeps the dispatcher skip reason in the pool queue note. |
| [Hub admission: capacity class metadata on cards](mirror-3423-a-hub-admission-metadata.md) | 2976 | Refuses class/frees combined with requires. |
| [Hub admission: capacity class metadata on cards](mirror-3423-a-hub-admission-metadata.md) | 2977 | Moves the separate-transaction admission refusal into edit_preflight. |
| [Process completion custody across gateway restarts](mirror-3423-a-process-completion-custody.md) | 2978 | Does not reopen a failed admission whose result a Desktop client already delivered. |
| [Update checkpoint accounting, update-pause alarms and update docs](mirror-3423-a-update-checkpoint-alarms.md) | 2979 | Documents when to enable terminal.update_survivable_processes. |
| [Kanban: slow plugin guard no longer costs a worker its park/report](mirror-3423-a-guard-refusal.md) | 2980 | A slow pre-tool-call guard no longer refuses park/report calls or books a protocol violation. |
| [Kanban: slow plugin guard no longer costs a worker its park/report](mirror-3423-a-guard-refusal.md) | 2981 | Guard-refusal classification requires the result to be the refusal on a terminal call. |
| [Kanban: slow plugin guard no longer costs a worker its park/report](mirror-3423-a-guard-refusal.md) | 2982 | Passes the lockguard for the state read and counts refusals with an appended budget notice. |
| [Kanban: mandatory model pins on the worker pool](mirror-3423-a-mandatory-pin.md) | 2983 | Lets a mandatory model pin ride the worker pool with exact-model or refuse semantics. |
| [Kanban: mandatory model pins on the worker pool](mirror-3423-a-mandatory-pin.md) | 2984 | A mandatory pin leaves home only through the worker queue and every move keeps its label. |
| [Kanban: mandatory model pins on the worker pool](mirror-3423-a-mandatory-pin.md) | 2985 | Marking a placed card mandatory adds its label and a worker-first card refuses the mark. |
| [Kanban replica consistency tests and lag guard](mirror-3423-a-replica-consistency.md) | 2986 | Never replaces a worker copy from a replica lagging behind this gateway's push. |
| [Hub admission: retired hub-write needs](mirror-3423-a-hub-needs-retired.md) | 2987 | Hub admission stops admitting the board admin key and instance role needs. |
| [Update checkpoint accounting, update-pause alarms and update docs](mirror-3423-a-update-checkpoint-alarms.md) | 2988 | Update-pause alarm key names the host and links sibling hosts' open alarms. |
| [Update-pause sibling alarm: lead selection, refusal classes, ownership](mirror-3423-b-update-pause-sibling-alarm.md) | 2989 | Sibling lookup joins the lead alarm, refusal classes keep the busy cause head, own cards are adopted under any host name. |
| [Update-pause sibling alarm: lead selection, refusal classes, ownership](mirror-3423-b-update-pause-sibling-alarm.md) | 2990 | Routes sibling-module dependencies to the e2e_upgrade CI lane and adopts non-primary own keys only by created_on. |
| [Cron restart-cut reruns and one-shot admission holds](mirror-3423-b-cron-restart-rerun-and-oneshot-holds.md) | 2991 | Expires the pending guard after a skipped cut rerun so a later deferred fire gets its own rerun. |
| [Cron restart-cut reruns and one-shot admission holds](mirror-3423-b-cron-restart-rerun-and-oneshot-holds.md) | 2992 | Test fix giving sequential frozen-clock executions distinct claim times. |
| [Machine metrics: bounded token totals by model](mirror-3423-b-machine-metrics-token-models.md) | 2993 | Reports bounded token totals grouped by model with unknown and other buckets and a no-model compatibility retry. |
| [Kanban gate-only assignees](mirror-3423-b-kanban-gate-only-assignees.md) | 2994 | Refuses card creates onto a gate-only assignee configured in kanban.gate_only_assignees, naming the queue assignee instead. |
| [Kanban gate-only assignees](mirror-3423-b-kanban-gate-only-assignees.md) | 2995 | Triage decomposer drops gate-only profiles from its roster and files any gate-only pick under its queue. |
| [Hub admission: claim-time screening and evidence rules](mirror-3423-b-hub-admission-claim-screen.md) | 2996 | Makes hub refusal assignee remedies complete and conditional. |
| [Kanban: a chat's verified relay of the user's reply counts as human progress](mirror-3423-b-kanban-relayed-human-progress.md) | 2997 | Adds verified relayed_human comments that reset the no-progress budget, plus suspect-relay logging. |
| [Kanban: a chat's verified relay of the user's reply counts as human progress](mirror-3423-b-kanban-relayed-human-progress.md) | 2998 | Relay verification picks only typed user messages and the card holder refuses replayed or pre-ask relays. |
| [Kanban: a chat's verified relay of the user's reply counts as human progress](mirror-3423-b-kanban-relayed-human-progress.md) | 2999 | Relay skips every registered synthetic user row, a running loop tick row and the storeless kanban notice. |
| [Kanban: a chat's verified relay of the user's reply counts as human progress](mirror-3423-b-kanban-relayed-human-progress.md) | 3000 | A reply after an interrupted loop tick still counts and the heartbeat frame is treated as synthetic. |
| [Kanban: a chat's verified relay of the user's reply counts as human progress](mirror-3423-b-kanban-relayed-human-progress.md) | 3001 | Relay reads state.db past the lock guard, baselines its plugin import, and finds the tick row with the reply scan filters. |
| [Kanban sync, lock discovery and event index robustness](mirror-3423-b-kanban-sync-and-lock-robustness.md) | 3002 | Replica-lag acknowledgement parsing accepts parsed field dicts as well as JSON strings. |
| [Kanban ready-wait checkpoint selection evidence](mirror-3423-b-kanban-ready-wait-checkpoint-evidence.md) | 3003 | Records checkpoint selection reason and emitter evidence alongside ready-wait notices. |
| [Kanban ready-wait checkpoint selection evidence](mirror-3423-b-kanban-ready-wait-checkpoint-evidence.md) | 3004 | Resolves the diagnostic boot fingerprint through the canonical install root. |
| [Cron restart-cut reruns and one-shot admission holds](mirror-3423-b-cron-restart-rerun-and-oneshot-holds.md) | 3005 | Preserves one-shot jobs awaiting admission settlement during the due scan. |
| [Async delegation: wake an unloaded parent chat](mirror-3423-b-delegation-unloaded-parent-wake.md) | 3006 | Sweeper wakes a parent chat for a terminal delegation result that no live poller delivers. |
| [Async delegation: wake an unloaded parent chat](mirror-3423-b-delegation-unloaded-parent-wake.md) | 3007 | Never wakes an archived or moved parent chat and warns when the post-turn ack finds a lapsed claim. |
| [Async delegation: wake an unloaded parent chat](mirror-3423-b-delegation-unloaded-parent-wake.md) | 3008 | Runs dead-owner delegation recovery at most every five minutes per ledger. |
| [Async delegation: wake an unloaded parent chat](mirror-3423-b-delegation-unloaded-parent-wake.md) | 3009 | Reaches display metadata through server to satisfy the split-module import rule. |
| [Gateway test deflakes](mirror-3423-b-gateway-test-deflakes.md) | 3010 | Deflakes the API admission writer test with an event handshake instead of wall-clock gaps. |
| [Machine metrics: bounded token totals by model](mirror-3423-b-machine-metrics-token-models.md) | 3011 | Corrects the documented compatibility retry send budget; runtime behavior unchanged. |
| [Kanban sync, lock discovery and event index robustness](mirror-3423-b-kanban-sync-and-lock-robustness.md) | 3012 | Credits the sync thread's own repair-revision steps and counts repair rounds. |
| [Gateway test deflakes](mirror-3423-b-gateway-test-deflakes.md) | 3013 | Makes the gateway admission expiry test use a virtual clock instead of a wall-clock window. |
| [Kanban sync, lock discovery and event index robustness](mirror-3423-b-kanban-sync-and-lock-robustness.md) | 3014 | Indexes event-kind history lookups, including on reopen of an existing database. |
| [Kanban sync, lock discovery and event index robustness](mirror-3423-b-kanban-sync-and-lock-robustness.md) | 3015 | Bounds the root lease's fast claim retry to a 30 s window per contention episode. |
| [Remote notices: single-flight replica snapshots](mirror-3423-b-notice-replica-singleflight.md) | 3016 | Single-flights replica snapshots per lane outside the publication lock. |
| [Remote notices: single-flight replica snapshots](mirror-3423-b-notice-replica-singleflight.md) | 3017 | Tests shared-signature reuse and uncertain read boundaries. |
| [Remote notices: single-flight replica snapshots](mirror-3423-b-notice-replica-singleflight.md) | 3018 | Test that legacy replicas without sequence signatures never reuse an old memo. |
| [Remote notices: single-flight replica snapshots](mirror-3423-b-notice-replica-singleflight.md) | 3019 | Test that unknown fingerprints never reuse a shared snapshot. |
| [Kanban: ship review verdicts must come from the dispatched worker run](mirror-3423-b-ship-review-independence.md) | 3020 | Refuses a ship-review verdict completed outside the card's dispatched worker run in the tool, CLI and dashboard. |
| [Kanban: ship review verdicts must come from the dispatched worker run](mirror-3423-b-ship-review-independence.md) | 3021 | Baselines the dashboard's import of the review contract in the boundary lint. |
| [Gateway turn outcome: run-owned custody](mirror-3423-b-gateway-outcome-run-custody.md) | 3022 | Freezes run-owned outcome evidence across retry and handler displacement. |
| [Gateway turn outcome: run-owned custody](mirror-3423-b-gateway-outcome-run-custody.md) | 3023 | Keeps outcome scope cleanup in the topical helper. |
| [Gateway turn outcome: run-owned custody](mirror-3423-b-gateway-outcome-run-custody.md) | 3024 | Retains outcome custody on abandoned worker waits and preserves finished receipts. |
| [Gateway turn outcome: run-owned custody](mirror-3423-b-gateway-outcome-run-custody.md) | 3025 | Moves the gateway worker wait into a topical sibling module to keep the facade file cap. |
| [Gateway turn outcome: run-owned custody](mirror-3423-b-gateway-outcome-run-custody.md) | 3026 | Captures a queued follow-up's lease on worker abandonment, gated on this executor future. |
| [Gateway turn outcome: run-owned custody](mirror-3423-b-gateway-outcome-run-custody.md) | 3027 | Gives the host-sleep poll fixture a real executor future. |
| [Update-pause sibling alarm: lead selection, refusal classes, ownership](mirror-3423-b-update-pause-sibling-alarm.md) | 3028 | Respects gateway ownership in update-pause sibling notifications. |
| [Cron restart-cut reruns and one-shot admission holds](mirror-3423-b-cron-restart-rerun-and-oneshot-holds.md) | 3029 | Types shutdown-interrupted last_failure as kind lifecycle_interrupt. |
| [Background delegation stopped by gateway shutdown wakes its parent](mirror-3423-b-delegation-shutdown-stop-wakes-parent.md) | 3030 | Stamps shutdown-stopped background subagents so their parent wakes with a shutdown-not-user message. |
| [Background delegation stopped by gateway shutdown wakes its parent](mirror-3423-b-delegation-shutdown-stop-wakes-parent.md) | 3031 | Adds tests for shutdown warning exclusions and closed-chat logging; production code unchanged. |
| [Typed /loop tick rows](mirror-3423-b-loop-tick-typed-rows.md) | 3032 | Types a slash loop tick's submitted prompt as a loop_tick row in the TUI, gateway and Desktop history. |
| [Process recovery never adopts a held foreground command](mirror-3423-b-survivable-process-recovery-hold.md) | 3033 | Recovery skips held foreground commands and release drops the hold and its checkpoint entry together. |
| [Remote notices: single-flight replica snapshots](mirror-3423-b-notice-replica-singleflight.md) | 3034 | Pins lane-through-generation ordering with a test and drops the unused decode fallback. |
| [Kanban move refuses to leave a host holding unpushed work](mirror-3423-b-kanban-move-unpushed-guard.md) | 3035 | Refuses to move a card off a host that holds its dirty or unpushed trees unless forced. |
| [Update shutdown: spent cron grace is not renewed](mirror-3423-b-update-cron-grace-not-renewed.md) | 3036 | Stops shutdown from restoring the cron floor after a checkpoint cron cut has spent its restart grace. |
| [Machine metrics: bounded token totals by model](mirror-3423-b-machine-metrics-token-models.md) | 3037 | A timed-out bounded sync call keeps its worker's own schedstat. |
| [Kanban sync, lock discovery and event index robustness](mirror-3423-b-kanban-sync-and-lock-robustness.md) | 3038 | Verifies singleton sidecar owners through fdinfo before global lock discovery. |
| [Typed /loop tick rows](mirror-3423-b-loop-tick-typed-rows.md) | 3039 | Pins PRODUCER_CARD_KINDS to an explicit list that excludes loop_tick. |
| [Background delegation stopped by gateway shutdown wakes its parent](mirror-3423-b-delegation-shutdown-stop-wakes-parent.md) | 3040 | Keeps a requested stop's intent across shutdown and logs not-woken only for notes. |
| [Cron restart-cut reruns and one-shot admission holds](mirror-3423-b-cron-restart-rerun-and-oneshot-holds.md) | 3041 | Holds one-shots on uncheckable admission journals, naming the journal. |
| [Worker pool repins: live-registry checks and hub:read placement](mirror-3423-b-worker-pool-repin-placement.md) | 3042 | Refuses pool repins without an eligible machine in the live registry. |
| [Worker pool repins: live-registry checks and hub:read placement](mirror-3423-b-worker-pool-repin-placement.md) | 3043 | Refuses pool repins that would use a stale registry fallback. |
| [Hub admission: claim-time screening and evidence rules](mirror-3423-b-hub-admission-claim-screen.md) | 3044 | Rejects a producer's pin sentence as admission evidence and routes hub work through hub-admin in the move tool text. |
| [Process recovery never adopts a held foreground command](mirror-3423-b-survivable-process-recovery-hold.md) | 3045 | Documentation naming the commands terminal.update_survivable_processes covers. |
| [Worker pool repins: live-registry checks and hub:read placement](mirror-3423-b-worker-pool-repin-placement.md) | 3046 | Aligns hub:read pool eligibility with the Board attestation contract. |
| [Hub admission: claim-time screening and evidence rules](mirror-3423-b-hub-admission-claim-screen.md) | 3047 | Judges pre-gate hub-local cards at claim time, admitting or re-placing them and recording a claim admission event. |
| [Hub admission: claim-time screening and evidence rules](mirror-3423-b-hub-admission-claim-screen.md) | 3048 | Test coverage for change-while-judged, empty-label and policy candidates and detector edges. |
| [Hub admission: claim-time screening and evidence rules](mirror-3423-b-hub-admission-claim-screen.md) | 3049 | Test for creation-time verdicts in the detector and pins a same-second gate to remove flakiness. |
| [Hub admission: claim-time screening and evidence rules](mirror-3423-b-hub-admission-claim-screen.md) | 3050 | Fences claim admission against concurrent card and event changes. |
| [Proof-home isolation for restart-proof backends](mirror-3423-c-proof-home-guard.md) | 3051 | Adds the `--proof-home` selector, agent-origin refusal on production homes, tool-child provenance stamps and recovery refusals. |
| [Proof-home isolation for restart-proof backends](mirror-3423-c-proof-home-guard.md) | 3052 | Drops agent provenance stamps on Desktop launches (Python and Electron) while serve and dashboard keep them. |
| [Proof-home isolation for restart-proof backends](mirror-3423-c-proof-home-guard.md) | 3053 | Keys the agent proof refusal on Desktop backend ownership instead of the inherited Desktop env var. |
| [Kanban pool-move and pickup-stall placement fixes](mirror-3423-c-kanban-pool-move-placement.md) | 3054 | Refreshes URL-pushed remote tips without inherited stdin and with pinned failure handling. |
| [Kanban pool-move and pickup-stall placement fixes](mirror-3423-c-kanban-pool-move-placement.md) | 3055 | Prioritizes branch destinations within the remote refresh cap. |
| [Process registry release never rewrites without its lock](mirror-3423-c-process-release-lock.md) | 3056 | Skips the processes.json rewrite on release when the checkpoint lock was not taken and adds race tests. |
| [Process registry release never rewrites without its lock](mirror-3423-c-process-release-lock.md) | 3057 | Docstring fix clarifying what the checkpoint-lock return value means. |
| [input_origin for pre_llm_call plugins](mirror-3423-c-input-origin-pre-llm-call.md) | 3058 | Passes input_origin (agent, human or unknown) to pre_llm_call per client submit. |
| [Hub admission: pin-line self-assertion and hub-admin placement](mirror-3423-c-hub-admission-pin-spans.md) | 3059 | Denies admits quoting only from inside a pin line and gives relayed owner denies a specific reason. |
| [Kanban sync: repair window and batched provenance](mirror-3423-c-kanban-sync-repair-and-links.md) | 3060 | Tidies own-write credit naming and drops the unused revisions parameter from pull(). |
| [Kanban chat-tasks projection check](mirror-3423-c-kanban-chat-tasks-projection.md) | 3061 | Adds a chat-tasks projection check for origin cards and repoints binding guidance at it. |
| [Kanban chat-tasks projection check](mirror-3423-c-kanban-chat-tasks-projection.md) | 3062 | Honors profile-qualified identities in projection checks. |
| [Proof-home isolation for restart-proof backends](mirror-3423-c-proof-home-guard.md) | 3063 | Matches the equals form of the SSH token-file flag and pins the provenance key lists on both sides. |
| [Hub admission: pin-line self-assertion and hub-admin placement](mirror-3423-c-hub-admission-pin-spans.md) | 3064 | Stops an abbreviation dot from ending a wrapped pin's span and documents the whole-line deny. |
| [Worker compute containment and admission](mirror-3423-c-worker-compute-capacity.md) | 3065 | Starts worker compute containment and occupied-owner accounting. |
| [Worker compute containment and admission](mirror-3423-c-worker-compute-capacity.md) | 3066 | Verifies worker CPU budgets at managed interpreter launch. |
| [Worker compute containment and admission](mirror-3423-c-worker-compute-capacity.md) | 3067 | Gates worker claims on load and detached cgroup occupancy. |
| [Bounded worker test slots and CI wiring](mirror-3423-c-worker-test-budget.md) | 3068 | Adds bounded worker test slots with verified lock inheritance. |
| [Worker compute containment and admission](mirror-3423-c-worker-compute-capacity.md) | 3069 | Binds worker policy to the account root and test receipts to acquisitions. |
| [Worker compute containment and admission](mirror-3423-c-worker-compute-capacity.md) | 3070 | Serializes worker admission across installation roots with an account lock. |
| [Worker compute containment and admission](mirror-3423-c-worker-compute-capacity.md) | 3071 | Retires idle worker boards under admission fences and reports detached occupancy. |
| [Worker compute containment and admission](mirror-3423-c-worker-compute-capacity.md) | 3072 | Recovers interrupted idle board retirement and bounds pressure contention. |
| [Worker compute containment and admission](mirror-3423-c-worker-compute-capacity.md) | 3073 | Binds worker capacity to the execution DB identity and allows occupied-owner resumes. |
| [Worker compute containment and admission](mirror-3423-c-worker-compute-capacity.md) | 3074 | Adds an independent sustained worker-load observer with a durable alert outbox. |
| [Worker compute containment and admission](mirror-3423-c-worker-compute-capacity.md) | 3075 | Binds legacy worker ownership to a live cgroup identity. |
| [Worker compute containment and admission](mirror-3423-c-worker-compute-capacity.md) | 3076 | Satisfies code-health and cross-platform lint gates for the worker capacity modules. |
| [Bounded worker test slots and CI wiring](mirror-3423-c-worker-test-budget.md) | 3077 | Gives runner probe trees the worker test-budget imports. |
| [Bounded worker test slots and CI wiring](mirror-3423-c-worker-test-budget.md) | 3078 | Covers worker budget imports and policy isolation in CI fixtures and classification. |
| [Bounded worker test slots and CI wiring](mirror-3423-c-worker-test-budget.md) | 3079 | Routes worker test budget changes to the standalone macOS state CI workflow. |
| [Bounded worker test slots and CI wiring](mirror-3423-c-worker-test-budget.md) | 3080 | Aligns worker admission test doubles with the final-gate keywords and policy reads. |
| [input_origin for pre_llm_call plugins](mirror-3423-c-input-origin-pre-llm-call.md) | 3081 | Classifies room plumbing rather than hidden sessions as agent and forwards origin to compute-host turns. |
| [Proof-home isolation for restart-proof backends](mirror-3423-c-proof-home-guard.md) | 3082 | Disables argparse abbreviation on both serve parsers so abbreviated flags cannot bypass the guard. |
| [Test deflakes: admission trace, free-tier turn, scope inode](mirror-3423-c-test-flake-fixes.md) | 3083 | Deflakes the API admission test by swapping the writer trace only under the database lock. |
| [Process registry release never rewrites without its lock](mirror-3423-c-process-release-lock.md) | 3084 | Docstring fix saying the lock-less drop only tries to remove the entry. |
| [Kanban wait notices: pool re-place hints and skip advice](mirror-3423-c-kanban-wait-notice-pool-hints.md) | 3085 | Names pool workers that could run the card in chat-wait notices and ready hints. |
| [Kanban review independence and worker-shell completion](mirror-3423-c-kanban-review-independence.md) | 3086 | Lets a card's own proven worker shell complete its run through the CLI. |
| [Kanban overlap, respawn and priority/help guards](mirror-3423-c-kanban-card-lifecycle-guards.md) | 3087 | Matches duplicate card titles that name the same code file. |
| [Kanban overlap, respawn and priority/help guards](mirror-3423-c-kanban-card-lifecycle-guards.md) | 3088 | Grants one continuation to a PR owner requeued by a core update after publishing. |
| [Kanban pool-move and pickup-stall placement fixes](mirror-3423-c-kanban-pool-move-placement.md) | 3089 | Maps declared access requirements to the hyphenated capability labels at every pickup-stall need site. |
| [Per-invocation outcome receipts for queued follow-ups](mirror-3423-c-outcome-receipt-per-run.md) | 3090 | Gives each run-agent invocation its own outcome receipt so late predecessor writes cannot conclude a follow-up. |
| [input_origin for pre_llm_call plugins](mirror-3423-c-input-origin-pre-llm-call.md) | 3091 | Pins the room-plumbing rule to the resume rule and reads only three columns per submit. |
| [Hub admission: pin-line self-assertion and hub-admin placement](mirror-3423-c-hub-admission-pin-spans.md) | 3092 | Restricts span extension to letter-dot abbreviations so filenames, versions and URLs end the pin. |
| [Core update: pause long model requests and bound gateway exit](mirror-3423-c-core-update-exit-bounds.md) | 3093 | Cancels a long model request at a step boundary so a core update pause can land. |
| [Kanban review independence and worker-shell completion](mirror-3423-c-kanban-review-independence.md) | 3094 | Shares the live-ancestor check and tests the spawn-pid fallback. |
| [Kanban wait notices: pool re-place hints and skip advice](mirror-3423-c-kanban-wait-notice-pool-hints.md) | 3095 | Keeps the pool hint under the admission census and prints it after the queue clause. |
| [Kanban overlap, respawn and priority/help guards](mirror-3423-c-kanban-card-lifecycle-guards.md) | 3096 | Documents priority restore limits and pins mixed-state refusal. |
| [input_origin for pre_llm_call plugins](mirror-3423-c-input-origin-pre-llm-call.md) | 3097 | Makes the turn session-id helper in turn_outcome public. |
| [Test deflakes: admission trace, free-tier turn, scope inode](mirror-3423-c-test-flake-fixes.md) | 3098 | Makes the free-tier prompt-turn test wait for real threads instead of patching Thread. |
| [Test deflakes: admission trace, free-tier turn, scope inode](mirror-3423-c-test-flake-fixes.md) | 3099 | Derives the stale inode in the legacy-scope test from the recreated scope. |
| [Hub admission: pin-line self-assertion and hub-admin placement](mirror-3423-c-hub-admission-pin-spans.md) | 3100 | Keeps claim-gate-refused hub denies at home and treats hub-admin-wrapped actions as needing hub-write. |
| [Kanban pool-move and pickup-stall placement fixes](mirror-3423-c-kanban-pool-move-placement.md) | 3101 | Refuses a pickup-stall pool move for access the Board cannot place. |
| [Kanban wait notices: pool re-place hints and skip advice](mirror-3423-c-kanban-wait-notice-pool-hints.md) | 3102 | Keeps a recorded skip with the pool hint and defers unrecorded-skip advice to the re-place. |
| [Kanban sync: repair window and batched provenance](mirror-3423-c-kanban-sync-repair-and-links.md) | 3103 | Defers repair passes triggered by other processes' writes to one per 30-second window. |
| [Kanban review independence and worker-shell completion](mirror-3423-c-kanban-review-independence.md) | 3104 | Gates review verdicts whose own reviewed_pr names a core pull. |
| [Kanban pool-move and pickup-stall placement fixes](mirror-3423-c-kanban-pool-move-placement.md) | 3105 | Refuses a pool move when the stored requires is not a JSON list of strings. |
| [Kanban review independence and worker-shell completion](mirror-3423-c-kanban-review-independence.md) | 3106 | Docstring noting the reviewed_pr core-pull fallback in independence_refusal. |
| [Kanban sync: repair window and batched provenance](mirror-3423-c-kanban-sync-repair-and-links.md) | 3107 | Classifies chat-link and backfill sessions in per-home batches instead of per row. |
| [Kanban wait notices: pool re-place hints and skip advice](mirror-3423-c-kanban-wait-notice-pool-hints.md) | 3108 | Unifies skip advice across surfaces and drops the pool re-place for quota-wall skips. |
| [Kanban overlap, respawn and priority/help guards](mirror-3423-c-kanban-card-lifecycle-guards.md) | 3109 | Aligns hub-reason CLI help with the admission schema wording. |
| [Kanban sync: repair window and batched provenance](mirror-3423-c-kanban-sync-repair-and-links.md) | 3110 | Drops the unused decide() parameter and documents deferred link repairs with a window test. |
| [Core update: pause long model requests and bound gateway exit](mirror-3423-c-core-update-exit-bounds.md) | 3111 | Bounds the gateway post-stop tail to the core update checkpoint's exit promise. |
| [Terminal background spawns get foreground PATH](mirror-3423-c-terminal-background-path.md) | 3112 | Gives background terminal spawns the foreground PATH dirs and flags masked missing commands. |
| [Pool placement labels and profile parity](mirror-3423-d-pool-placement-labels.md) | 3113 | A mandatory-pin mark labels a pool card regardless of the process's sync mode. |
| [Worker inventory board registry hardening](mirror-3423-d-worker-inventory-boards.md) | 3114 | Tests never register in the real worker inventory and garbage-collected scratch boards retire. |
| [Pool placement labels and profile parity](mirror-3423-d-pool-placement-labels.md) | 3115 | A pickup-stall pool move reads the card's skills like the Board. |
| [Test deflakes and small review follow-ups](mirror-3423-d-test-flakes-and-small-followups.md) | 3116 | Deflakes the managed-worker group-close reap check by counting dead processes as gone. |
| [Kanban parks and requeued publishers](mirror-3423-d-park-wake-check-and-publisher.md) | 3117 | An earlier PR needs a recorded handoff to the requeued claim; schema probed once. |
| [Kanban parks and requeued publishers](mirror-3423-d-park-wake-check-and-publisher.md) | 3118 | A granted continuation's own requeue chain hands the publisher's PR on. |
| [Test deflakes and small review follow-ups](mirror-3423-d-test-flakes-and-small-followups.md) | 3119 | Pins the hub-reason CLI help to the tool schema description with a parity test. |
| [Test deflakes and small review follow-ups](mirror-3423-d-test-flakes-and-small-followups.md) | 3120 | Pins plain-card worker-shell completion and documents the pid coercion in descends_from_live_pid. |
| [Hub admission: CPU gate census and claim screen](mirror-3423-d-hub-admission-cpu-gate.md) | 3121 | Adds RED tests for the final gate census under thread contention. |
| [Hub admission: CPU gate census and claim screen](mirror-3423-d-hub-admission-cpu-gate.md) | 3122 | One-statement board census so the final gate fits its sample, with stage-named refusals. |
| [Hub admission: CPU gate census and claim screen](mirror-3423-d-hub-admission-cpu-gate.md) | 3123 | Re-exports the board fixture in the contention test without shadowing. |
| [Terminal masked-success hints](mirror-3423-d-terminal-hints.md) | 3124 | Process log of exited sessions carries exit fields and missing-command shapes anchor to line heads. |
| [Explicit card review requirements and review receipts](mirror-3423-d-review-required-cards.md) | 3125 | Adds the review_required card flag across CLI, tool, sync and the completion gate. |
| [Explicit card review requirements and review receipts](mirror-3423-d-review-required-cards.md) | 3126 | Retains the review guards inside worker subprocesses. |
| [Explicit card review requirements and review receipts](mirror-3423-d-review-required-cards.md) | 3127 | Routes review-required completion refusals through receipt_refusal to keep kanban.py under its line cap. |
| [Explicit card review requirements and review receipts](mirror-3423-d-review-required-cards.md) | 3128 | Folds review_required edit/create wiring to keep kanban.py at its line cap. |
| [Pool placement labels and profile parity](mirror-3423-d-pool-placement-labels.md) | 3129 | Pickup-stall repin needs profile:default for a card with no assignee. |
| [Worker inventory board registry hardening](mirror-3423-d-worker-inventory-boards.md) | 3130 | Guards stripped-env pytest children and retires collected records only under the fence. |
| [Kanban sync: retire moot lease_lost releases](mirror-3423-d-kanban-sync-lease-release.md) | 3131 | Retires a lease_lost release whose run the Board already ended. |
| [Update pause: continuations and synthesized turns](mirror-3423-d-update-pause-continuations.md) | 3132 | A synthesized turn over a paused marker pauses at its next step boundary. |
| [Test deflakes and small review follow-ups](mirror-3423-d-test-flakes-and-small-followups.md) | 3133 | descends_from_live_pid refuses non-integral float pids instead of truncating. |
| [Terminal masked-success hints](mirror-3423-d-terminal-hints.md) | 3134 | Missing-command hint keeps spaced command paths and flags env shebang errors. |
| [Kanban show and completion refusal wording](mirror-3423-d-kanban-status-messages.md) | 3135 | Sentence-cases skip advice after a full stop and offers pool re-place for an expired quota wall. |
| [Worker inventory board registry hardening](mirror-3423-d-worker-inventory-boards.md) | 3136 | Skips a record unlinked mid-listing and treats only FileNotFoundError as retiring. |
| [Kanban show and completion refusal wording](mirror-3423-d-kanban-status-messages.md) | 3137 | kanban_complete names triage and other no-run statuses instead of stale_run. |
| [Desktop stripe: red reasons and stale prompts](mirror-3423-d-desktop-stripe-red-reason.md) | 3138 | A dead runtime's prompt no longer keeps a chat red and every red row reports why. |
| [Kanban parks and requeued publishers](mirror-3423-d-park-wake-check-and-publisher.md) | 3139 | CI/deploy park premise includes the head SHAs its park names. |
| [Update pause: continuations and synthesized turns](mirror-3423-d-update-pause-continuations.md) | 3140 | A continuation yields to a pending pause before its first step boundary. |
| [Kanban parks and requeued publishers](mirror-3423-d-park-wake-check-and-publisher.md) | 3141 | A worker's re-park of a hub card relays a board-reading wake_check to the hub. |
| [Kanban show and completion refusal wording](mirror-3423-d-kanban-status-messages.md) | 3142 | An expired quota wall reads reset passed instead of starts on its own. |
| [Remote kanban notices: late delivery and single-flight](mirror-3423-d-remote-notice-delivery.md) | 3143 | Pollers of one replica snapshot share a single persist pass. |
| [Kanban placement markers and pool queue](mirror-3423-d-placement-markers.md) | 3144 | A required-check name is no longer treated as a macOS marker. |
| [Kanban placement markers and pool queue](mirror-3423-d-placement-markers.md) | 3145 | Moves the product-marker test cases to their own file for the line cap. |
| [Kanban show and completion refusal wording](mirror-3423-d-kanban-status-messages.md) | 3146 | Pins the statuses a no-run card completes from in the every-status sweep. |
| [Pool placement labels and profile parity](mirror-3423-d-pool-placement-labels.md) | 3147 | Pickup-stall alarm placement reads a NULL or empty assignee as profile:default. |
| [Kanban sync: retire moot lease_lost releases](mirror-3423-d-kanban-sync-lease-release.md) | 3148 | Fake board mirrors lease_run_uid with an end-to-end lease-held release veto test. |
| [Kanban sync: retire moot lease_lost releases](mirror-3423-d-kanban-sync-lease-release.md) | 3149 | Fake lease release clears the card's lease mirror after the run ended. |
| [Worker inventory board registry hardening](mirror-3423-d-worker-inventory-boards.md) | 3150 | Only ENOENT counts as missing in every inventory existence check via a shared helper. |
| [Hub admission: CPU gate census and claim screen](mirror-3423-d-hub-admission-cpu-gate.md) | 3151 | Hub claim screen defers edited cards and caps judge calls per tick. |
| [Hub admission: CPU gate census and claim screen](mirror-3423-d-hub-admission-cpu-gate.md) | 3152 | Claim screen judges in dispatch order so the budget never defers top priority. |
| [Remote kanban notices: late delivery and single-flight](mirror-3423-d-remote-notice-delivery.md) | 3153 | Observed remote notices are delivered late within 72 hours instead of settling stale. |
| [Desktop stripe: red reasons and stale prompts](mirror-3423-d-desktop-stripe-red-reason.md) | 3154 | Keeps turn-live prompts, re-reports a moved prompt and fixes as-of wording. |
| [Kanban consent stops and operator delivery receipts](mirror-3423-d-kanban-consent-stops.md) | 3155 | Carries approval stops in creation provenance and binds delivery claims to exact operator receipts. |
| [Worker inventory board registry hardening](mirror-3423-d-worker-inventory-boards.md) | 3156 | Pins ENOTDIR as not missing and keeps an unreadable board's record after failed removal. |
| [Kanban placement markers and pool queue](mirror-3423-d-placement-markers.md) | 3157 | A pool worker's built-in local-marker hit joins the pool queue. |
| [Hub admission: CPU gate census and claim screen](mirror-3423-d-hub-admission-cpu-gate.md) | 3158 | Final hub gate reads the sampler's fresh sample instead of measuring under the lock. |
| [Hub admission: CPU gate census and claim screen](mirror-3423-d-hub-admission-cpu-gate.md) | 3159 | Renames the slow-window test stand-in to describe what it widens. |
| [Hub admission: CPU gate census and claim screen](mirror-3423-d-hub-admission-cpu-gate.md) | 3160 | Keeps the stage-tagged refusals on the sampler-read final gate. |
| [Kanban sync: retire moot lease_lost releases](mirror-3423-d-kanban-sync-lease-release.md) | 3161 | Fake lease release is fenced on the holder's run and an ended run retries the card. |
| [Hub admission: CPU gate census and claim screen](mirror-3423-d-hub-admission-cpu-gate.md) | 3162 | Real previous_runs_on on a deny move and unjudged_claims counts created-payload verdicts. |
| [Test deflakes and small review follow-ups](mirror-3423-d-test-flakes-and-small-followups.md) | 3163 | Completion-recovery second reconcile starts fresh and last-admission-only marker is documented. |
| [Update pause: continuations and synthesized turns](mirror-3423-d-update-pause-continuations.md) | 3164 | A paused turn's continuation refused during start-up keeps an owner and re-arms a watcher. |
| [Update pause: continuations and synthesized turns](mirror-3423-d-update-pause-continuations.md) | 3165 | Resume note warns that earlier tool output may be stale. |
| [OOM-killed command takes the command, not the run](mirror-3423-d-worker-oom-policy.md) | 3166 | Kanban run scopes use OOMPolicy=continue and OOM kills are named. |
| [OOM-killed command takes the command, not the run](mirror-3423-d-worker-oom-policy.md) | 3167 | Allowlists the OOMPolicy probe's systemd-run lookup in the resolution guard. |
| [Pool placement labels and profile parity](mirror-3423-d-pool-placement-labels.md) | 3168 | One shared profile_label helper for the Board's profile rule. |
| [Explicit card review requirements and review receipts](mirror-3423-d-review-required-cards.md) | 3169 | review_required refusals name the operator rule and refuse before the goal judge. |
| [Desktop stripe: red reasons and stale prompts](mirror-3423-d-desktop-stripe-red-reason.md) | 3170 | Reconnect retires turn-live-only claims and only needs-input rows track prompt inputs. |
| [Test deflakes and small review follow-ups](mirror-3423-d-test-flakes-and-small-followups.md) | 3171 | Peer member gateway test boots on an API-server port it owns. |
| [Pool placement labels and profile parity](mirror-3423-d-pool-placement-labels.md) | 3172 | Denied cards keep the worker queue across a restart and self-check repins stranded worker-first cards. |
| [Explicit card review requirements and review receipts](mirror-3423-d-review-required-cards.md) | 3173 | Carries home review continuation receipts to replicas. |
| [Explicit card review requirements and review receipts](mirror-3423-d-review-required-cards.md) | 3174 | Fences portable receipts on origin and contradictory evidence. |
| [Kanban review-release authority: boundary tests and replica-only stamp](mirror-3423-e-kanban-review-release-tests.md) | 3175 | Tests that a home review receipt crosses the snapshot feed and pickup, including baseline-guard failure. |
| [Kanban review-release authority: boundary tests and replica-only stamp](mirror-3423-e-kanban-review-release-tests.md) | 3176 | Adds boundary suites for portable release authority and observed-PR hold. |
| [Kanban review-release authority: boundary tests and replica-only stamp](mirror-3423-e-kanban-review-release-tests.md) | 3177 | Adds mutation-killing release/observation guard tests and drops a duplicate producer parent fence. |
| [Kanban review-release authority: boundary tests and replica-only stamp](mirror-3423-e-kanban-review-release-tests.md) | 3178 | Drops a provably redundant missing-origin operand in the review-release consumer. |
| [Kanban review-release authority: boundary tests and replica-only stamp](mirror-3423-e-kanban-review-release-tests.md) | 3179 | Makes only a replica honour the home's review-release stamp. |
| [Kanban review-release authority: boundary tests and replica-only stamp](mirror-3423-e-kanban-review-release-tests.md) | 3180 | Makes the review-release consumer check home origin unconditionally. |
| [Kanban pool placement: park pin, repin fallback, local-marker queueing](mirror-3423-e-kanban-pool-placement.md) | 3181 | Park pin ignores a text-named shared checkout and treats untracked remote branches and single-branch clones correctly. |
| [Kanban hub admission: refusal reasons, claim-time denies, home routing](mirror-3423-e-kanban-hub-admission.md) | 3182 | Review nits: hub-admin lookbehind, normalized requires list on the home route, and reporting home denies. |
| [Update-pause auto-continue: refused kickoffs, stale refusals, yield before error accounting](mirror-3423-e-update-pause-continue.md) | 3183 | A refused auto-continue kickoff drops its marker inputs and caps its watcher re-arms. |
| [Desktop: single holdsTurnClaim predicate](mirror-3423-e-desktop-turn-claim-predicate.md) | 3184 | Adds one holdsTurnClaim predicate for busy, awaiting and turnLive state. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3185 | Hosted reads skip the host-home guard and the composed worker test advances generation 0 after the upstream merge. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3186 | Code-health ratchet moves: notify-identity helper, authority_cwd fixture and HX016 allowances, no behavior change. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3187 | Full-suite fixes after the merge across worker env/client, state schema and sidecar keys, and several tests. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3188 | Brings prompt_builder back under the file-line target. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3189 | Restores file-line targets in prompt_builder, kanban and kanban_tools after merging main. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3190 | Delegated children get the private-scratch note on fresh runs only; continue tests pin turns without it. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3191 | Drops an unused noqa in a delegate test. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3192 | Adapts main's new gateway tests to claimed admissions, resumes_parent and the private-scratch note. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3193 | Keeps the merged prompt builder within its line cap. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3194 | Aligns new fork test fixtures with upstream claim and completion contracts. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3195 | Unviewed-delegation tests: success resumes (upstream) while a requested stop stays a note (fork). |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3196 | Regenerates the serve handler ledger after the main merge. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3197 | Brings conversation_loop under 2000 lines after the main merge. |
| [Test hygiene: shared helpers, deflakes and pins](mirror-3423-e-test-hygiene.md) | 3198 | Moves placement ROUTE/decide test helpers into a shared helpers module. |
| [Test hygiene: shared helpers, deflakes and pins](mirror-3423-e-test-hygiene.md) | 3199 | Deflakes the hosted-room cancel test by observing the interrupt on receipt. |
| [CI review comment: retry budget, re-refresh queueing, budget pins](mirror-3423-e-ci-review-comment-budget.md) | 3200 | Gives the review-comment sweep a wall-clock retry budget and one re-refresh per head. |
| [CI review comment: retry budget, re-refresh queueing, budget pins](mirror-3423-e-ci-review-comment-budget.md) | 3201 | The re-refresh's own failed footer points at the hourly sweep. |
| [Test hygiene: shared helpers, deflakes and pins](mirror-3423-e-test-hygiene.md) | 3202 | Pins the run_heads sort so head order never changes a CI premise. |
| [Desktop: keep a slow-but-alive SSH backend (slow-link mode)](mirror-3423-e-desktop-slow-link.md) | 3203 | Keeps a slow-but-alive SSH backend with slow-link state, stretched timeouts, poll thinning and prefetch pause. |
| [Desktop: keep a slow-but-alive SSH backend (slow-link mode)](mirror-3423-e-desktop-slow-link.md) | 3204 | Follows noteRemoteAnswer into slow-link.ts in the warm-dispatch test and tests the answer ledger and first-request log. |
| [Desktop: single holdsTurnClaim predicate](mirror-3423-e-desktop-turn-claim-predicate.md) | 3205 | Routes holdsUpdateHeldPromptIdle through holdsTurnClaim. |
| [Test hygiene: shared helpers, deflakes and pins](mirror-3423-e-test-hygiene.md) | 3206 | Group peer suites set member config through the fixture and boot via _member. |
| [Kanban hub admission: refusal reasons, claim-time denies, home routing](mirror-3423-e-kanban-hub-admission.md) | 3207 | Gives each hub final-gate refusal branch its own reason and cuts census column reads to one row. |
| [Kanban sync: SQLite divergence prefilter, off-thread links pass, FakeHub lease release](mirror-3423-e-kanban-sync-perf-fakehub.md) | 3208 | Test FakeHub lease release always emits the run-outcome event and honors the outcome. |
| [Upstream sync d01ed2bfac: merge fixups and code-health ratchets](mirror-3423-e-upstream-sync-d01ed2bfac.md) | 3209 | Keeps the fork-local dike plugin catalog pin during upstream sync. |
| [Test and pm environments borrow the account's verified tool store](mirror-3423-e-tool-store-borrowing.md) | 3210 | run_tests.sh with a custom HERMES_HOME borrows the account tool store. |
| [Test and pm environments borrow the account's verified tool store](mirror-3423-e-tool-store-borrowing.md) | 3211 | A borrower declines an optional default the account store never installed. |
| [Desktop: bounded remote request deadlines](mirror-3423-e-desktop-request-deadlines.md) | 3212 | Adds a total per-attempt request deadline, a dedicated probe pool and a 20 s down-gate trial deadline. |
| [Kanban pool placement: park pin, repin fallback, local-marker queueing](mirror-3423-e-kanban-pool-placement.md) | 3213 | Worker-first self-check reserves when the board refuses its repin at the same revision. |
| [Kanban sync: SQLite divergence prefilter, off-thread links pass, FakeHub lease release](mirror-3423-e-kanban-sync-perf-fakehub.md) | 3214 | Divergence repair compares settled cards in SQLite and the chat-link pass runs off the round thread. |
| [Kanban hub admission: refusal reasons, claim-time denies, home routing](mirror-3423-e-kanban-hub-admission.md) | 3215 | A claim-time hub deny moves portable cards to the worker queue and self-check re-places denied cards. |
| [Kanban priority capture: replay open/terminal churn to the final head](mirror-3423-e-kanban-priority-feed-churn.md) | 3216 | Priority capture replays open and terminal churn to the final head. |
| [Kanban consent and delivery: verb-scoped negation, fail-closed provenance, stop lineage](mirror-3423-e-kanban-consent-delivery.md) | 3217 | Scopes claim negation to the verb and fails closed on unsynced creation provenance. |
| [Kanban consent and delivery: verb-scoped negation, fail-closed provenance, stop lineage](mirror-3423-e-kanban-consent-delivery.md) | 3218 | Stops follow compression lineage and worker comments and summary edits are fenced. |
| [Kanban tool surface: completion receipt schema, wake summary, review wording](mirror-3423-e-kanban-tool-surface.md) | 3219 | Moves _store_attachment into the attach-url sibling module for a line-count offset. |
| [Plugin `*_nowait` probes never wait on a held sweep](mirror-3423-e-plugin-nowait-probes.md) | 3220 | Plugin nowait probes serve known sets instead of waiting on a sweep another thread holds. |
| [Kanban hub admission: refusal reasons, claim-time denies, home routing](mirror-3423-e-kanban-hub-admission.md) | 3221 | A re-pickup prior-deny skip names its home reason and path-qualified hub-admin stays home. |
| [Desktop: bounded remote request deadlines](mirror-3423-e-desktop-request-deadlines.md) | 3222 | Adds an upload body budget, documents the trial-deadline trade-off and tests a late answer. |
| [Gateway update exit bound and always-on keep-alive drain](mirror-3423-e-gateway-exit-and-drain.md) | 3223 | Bounds asyncio.run's shutdown and the outer finally to a core update's exit target. |
| [Update-pause auto-continue: refused kickoffs, stale refusals, yield before error accounting](mirror-3423-e-update-pause-continue.md) | 3224 | Auto-continue kickoff clears a stale turn-owner refusal before its submit. |
| [Update-pause auto-continue: refused kickoffs, stale refusals, yield before error accounting](mirror-3423-e-update-pause-continue.md) | 3225 | Update-pause yields before API-error accounting and pins api-error, crossed-cancel and streamed-bubble paths. |
| [Gateway update exit bound and always-on keep-alive drain](mirror-3423-e-gateway-exit-and-drain.md) | 3226 | Drains keep-alive sockets before a superseded always-on backend retires. |
| [Kanban tool surface: completion receipt schema, wake summary, review wording](mirror-3423-e-kanban-tool-surface.md) | 3227 | kanban_complete advertises the metadata.completion_receipt v1 shape. |
| [Kanban pool placement: park pin, repin fallback, local-marker queueing](mirror-3423-e-kanban-pool-placement.md) | 3228 | Queues a local-marker card only when one live machine holds its whole label set. |
| [Test hygiene: shared helpers, deflakes and pins](mirror-3423-e-test-hygiene.md) | 3229 | The lockguard stranded-fd scan matches only the board's own sidecars. |
| [Test and pm environments borrow the account's verified tool store](mirror-3423-e-tool-store-borrowing.md) | 3230 | A fresh HERMES_HOME links the account's verified tools, uv cache and source snapshot. |
| [CI review comment: retry budget, re-refresh queueing, budget pins](mirror-3423-e-ci-review-comment-budget.md) | 3231 | Pins the budget check before each artifact's first download. |
| [Kanban tool surface: completion receipt schema, wake summary, review wording](mirror-3423-e-kanban-tool-surface.md) | 3232 | Kanban show states what wakes a scheduled card on the status line and in JSON. |
| [Kanban sync: SQLite divergence prefilter, off-thread links pass, FakeHub lease release](mirror-3423-e-kanban-sync-perf-fakehub.md) | 3233 | FakeHub lease release guards outcomes, merges diagnostic/extra and refuses spawn_failed. |
| [Plugin `*_nowait` probes never wait on a held sweep](mirror-3423-e-plugin-nowait-probes.md) | 3234 | Documents and logs the empty on-loop fallback of plugin nowait probes. |
| [Kanban tool surface: completion receipt schema, wake summary, review wording](mirror-3423-e-kanban-tool-surface.md) | 3235 | An in-review review_required card is never told it is not in review. |
| [Desktop: bounded remote request deadlines](mirror-3423-e-desktop-request-deadlines.md) | 3236 | Tests a mid-transfer upload stall and routes request sending through sendWithRequestDeadline. |
| [Kanban priority capture reads the inventory at one fixed head](mirror-3423-f-kanban-priority-capture-fixed-head.md) | 3237 | Priority capture refuses snapshot page heads that decrease. |
| [Lifecycle guard budget refusals name the file and the cap](mirror-3423-f-lifecycle-guard-budget-refusal-messages.md) | 3238 | Budget refusals name the file and which limit (bytes, lines, longest line) was hit. |
| [Kanban worker scope: capability checks and scratch-home fence](mirror-3423-f-kanban-worker-scope-and-capability-checks.md) | 3239 | Exempts the fenced root's auto-pruned scratch directory from the Kanban path fence so probe homes stop leaking. |
| [Core-update pause: carried turns and first-request follow-ups](mirror-3423-f-update-pause-carried-turns.md) | 3240 | Announces a carried turn's pause and names the paused notification turn in its continuation. |
| [Core-update pause: carried turns and first-request follow-ups](mirror-3423-f-update-pause-carried-turns.md) | 3241 | Only a turn without its own marker counts as carried; a retried own pause does not. |
| [Kanban operator delivery claims and completion-receipt schema](mirror-3423-f-kanban-operator-delivery-and-completion-receipts.md) | 3242 | Colon, dash and open parenthesis end the claim negation window, and only the unsynced caller's call is deferred. |
| [Kanban worker scope: capability checks and scratch-home fence](mirror-3423-f-kanban-worker-scope-and-capability-checks.md) | 3243 | Capability checks read the assignee policy inside a bound worker scope. |
| [Kanban worker-first self-check repin refusals](mirror-3423-f-kanban-selfcheck-repin-refusals.md) | 3244 | Denied-card self-check reads the screen's verdict and skips the history read for kept cards; documents portable_wake_probes. |
| [Model-scoped quota cooldown recovery and contradiction reporting](mirror-3423-f-quota-cooldown-recovery.md) | 3245 | Scoped cooldown recovery with generation-aware clear receipts that survives stale pool writers. |
| [Model-scoped quota cooldown recovery and contradiction reporting](mirror-3423-f-quota-cooldown-recovery.md) | 3246 | Wires quota recovery and owner-side contradiction reporting into auth, workers and dispatch. |
| [Model-scoped quota cooldown recovery and contradiction reporting](mirror-3423-f-quota-cooldown-recovery.md) | 3247 | Validates quota proof response bodies and bounds streaming recovery. |
| [Model-scoped quota cooldown recovery and contradiction reporting](mirror-3423-f-quota-cooldown-recovery.md) | 3248 | Adds socket-level tests proving quota recovery identity and transport boundaries. |
| [Model-scoped quota cooldown recovery and contradiction reporting](mirror-3423-f-quota-cooldown-recovery.md) | 3249 | Bounds quota proof DNS lifetime with a reaped resolver. |
| [Model-scoped quota cooldown recovery and contradiction reporting](mirror-3423-f-quota-cooldown-recovery.md) | 3250 | Keeps account status on an unchanged-pair flush, annotates the contradiction DB, and widens DNS test slack. |
| [Kanban worker-first self-check repin refusals](mirror-3423-f-kanban-selfcheck-repin-refusals.md) | 3251 | A transient Board error no longer disables the self-check repin. |
| [Kanban operator delivery claims and completion-receipt schema](mirror-3423-f-kanban-operator-delivery-and-completion-receipts.md) | 3252 | The completion_receipt schema lists evidence.operator_delivery and states the timezone and byte-cap rules. |
| [macOS state lane CI routing](mirror-3423-f-macos-state-lane-ci-routing.md) | 3253 | Stops running the macOS hermes_state lane on main pushes and adds a scheduled backstop. |
| [Gateway event-loop stalls and kanban scratch GC](mirror-3423-f-gateway-loop-stalls-and-scratch-gc.md) | 3254 | Removes per-request stat and realpath work from the event loop and moves scratch GC off the board lock. |
| [Package manager: borrowing homes and tool-store pins](mirror-3423-f-pm-borrowing-home-environment.md) | 3255 | A borrowing home compiles bytecode eagerly when PYTHONDONTWRITEBYTECODE is set. |
| [Desktop JSON fetch seam and probe pool tests](mirror-3423-f-desktop-json-fetch-seam.md) | 3256 | Moves fetchJson and fetchPublicJson into json-fetch.ts so tests drive them directly. |
| [Kanban parked waits: CI runs that cannot start and show park display](mirror-3423-f-kanban-ci-wait-and-park-display.md) | 3257 | Refuses a CI-wait park on a run with no runner and wakes a parked card whose run stops starting. |
| [Kanban parked waits: CI runs that cannot start and show park display](mirror-3423-f-kanban-ci-wait-and-park-display.md) | 3258 | CI run progress resolves gh through the platform resolver. |
| [Gateway post-tail exit bound: exit code, diagnostics, hand-back](mirror-3423-f-gateway-post-tail-exit-bound.md) | 3259 | The post-tail bound uses the unwinding exit code, writes an exit-diag record and wakes a parked funnel on hand-back. |
| [Kanban operator delivery claims and completion-receipt schema](mirror-3423-f-kanban-operator-delivery-and-completion-receipts.md) | 3260 | An operator completing an in-review card with a run id is told to omit it. |
| [execute_code sandbox terminal() returns complete output](mirror-3423-f-execute-code-terminal-full-output.md) | 3261 | Sandbox terminal() returns the complete spilled output with truncated false instead of the chat window. |
| [execute_code sandbox terminal() returns complete output](mirror-3423-f-execute-code-terminal-full-output.md) | 3262 | Pending-approval terminal results keep their exact shape. |
| [execute_code sandbox terminal() returns complete output](mirror-3423-f-execute-code-terminal-full-output.md) | 3263 | Reports truncated true for a windowed output with no readable spill. |
| [Profile config scope inside a bound launch scope](mirror-3423-f-profile-config-scope-bound-launch.md) | 3264 | profile_config_scope binds the launch scope inside a bound scope instead of raising. |
| [Kanban parked waits: CI runs that cannot start and show park display](mirror-3423-f-kanban-ci-wait-and-park-display.md) | 3265 | kanban show reads the park through the dispatcher's lookup and says a legacy human park will be blocked. |
| [Kanban PR-owner holds, handoffs and review release](mirror-3423-f-kanban-pr-owner-holds-and-handoffs.md) | 3266 | Review release skips held cards in prepare() and adds a real-feed resume test. |
| [Kanban PR-owner holds, handoffs and review release](mirror-3423-f-kanban-pr-owner-holds-and-handoffs.md) | 3267 | Notes that review_release stamp authority depends on task_events.origin_gateway. |
| [Package manager: borrowing homes and tool-store pins](mirror-3423-f-pm-borrowing-home-environment.md) | 3268 | Documents the sticky borrower decline and makes run_tests.sh read-only for the store. |
| [Package manager: borrowing homes and tool-store pins](mirror-3423-f-pm-borrowing-home-environment.md) | 3269 | Documents that the decline clears only on a same-target pm install. |
| [Kanban replica freshness, ready-wait notices and sync diagnostics](mirror-3423-f-kanban-replica-freshness-and-sync-diagnostics.md) | 3270 | The chat-link pass logs how long it took. |
| [Kanban worker scope: capability checks and scratch-home fence](mirror-3423-f-kanban-worker-scope-and-capability-checks.md) | 3271 | The capsule requirement warning names the policy exception. |
| [Kanban worker scope: capability checks and scratch-home fence](mirror-3423-f-kanban-worker-scope-and-capability-checks.md) | 3272 | Capability checks the own profile under its launch secret scope. |
| [Kanban replica freshness, ready-wait notices and sync diagnostics](mirror-3423-f-kanban-replica-freshness-and-sync-diagnostics.md) | 3273 | Fake hub and service release routes share diagnostic and evidence helpers. |
| [Package manager: borrowing homes and tool-store pins](mirror-3423-f-pm-borrowing-home-environment.md) | 3274 | A borrowing home runs the owner's committed environment. |
| [Kanban hub CPU alert threshold and OOM attribution](mirror-3423-f-kanban-hub-cpu-alert-and-oom-attribution.md) | 3275 | The hub CPU alert uses the pre-lease advisory's 10 s staleness limit. |
| [Kanban hub CPU alert threshold and OOM attribution](mirror-3423-f-kanban-hub-cpu-alert-and-oom-attribution.md) | 3276 | OOM victim lookup after a restart, bounded journal read and honest exit-137 attribution. |
| [Kanban PR-owner holds, handoffs and review release](mirror-3423-f-kanban-pr-owner-holds-and-handoffs.md) | 3277 | Hub resume accepts a runner-only active_pr hold after any latest transition. |
| [Kanban PR-owner holds, handoffs and review release](mirror-3423-f-kanban-pr-owner-holds-and-handoffs.md) | 3278 | Releases a PR owner whose same-second park or awaited exact-head review handed it off. |
| [Kanban worker-first self-check repin refusals](mirror-3423-f-kanban-selfcheck-repin-refusals.md) | 3279 | Remembers uncoded persistent 4xx and 501 self-check repin refusals. |
| [Core-update pause: carried turns and first-request follow-ups](mirror-3423-f-update-pause-carried-turns.md) | 3280 | Documents first-request pause follow-ups without a behavior change. |
| [Kanban operator delivery claims and completion-receipt schema](mirror-3423-f-kanban-operator-delivery-and-completion-receipts.md) | 3281 | The completion_receipt schema advertises the strict evidence.operator_delivery shape from shared constants. |
| [Kanban replica freshness, ready-wait notices and sync diagnostics](mirror-3423-f-kanban-replica-freshness-and-sync-diagnostics.md) | 3282 | Backfill looks provenance verdicts up before its write transaction and prefetch skips foreign refs. |
| [Kanban replica freshness, ready-wait notices and sync diagnostics](mirror-3423-f-kanban-replica-freshness-and-sync-diagnostics.md) | 3283 | Ready-wait notices check the live board against its pulled replica. |
| [Kanban replica freshness, ready-wait notices and sync diagnostics](mirror-3423-f-kanban-replica-freshness-and-sync-diagnostics.md) | 3284 | Replica freshness compares only primary-mode boards and keeps live rows with unpushed changes. |
| [Desktop JSON fetch seam and probe pool tests](mirror-3423-f-desktop-json-fetch-seam.md) | 3285 | json-fetch tests derive the probe pool size and share one loopback server helper. |
| [Profile config scope inside a bound launch scope](mirror-3423-f-profile-config-scope-bound-launch.md) | 3286 | Pins TurnScope preservation when profile_config_scope re-enters the bound launch home. |
| [macOS state lane CI routing](mirror-3423-f-macos-state-lane-ci-routing.md) | 3287 | Starts the macOS state lane for hermes_state's load-time imports. |
| [macOS state lane CI routing](mirror-3423-f-macos-state-lane-ci-routing.md) | 3288 | The import scanner reads only bare TYPE_CHECKING guards as type-only and walks match-case bodies. |
| [Gateway event-loop stalls and kanban scratch GC](mirror-3423-f-gateway-loop-stalls-and-scratch-gc.md) | 3289 | Kanban gc sweeps stale gc-rm trash, logs a SELECT abort, and resets one home-key memo. |
| [Lifecycle guard budget refusals name the file and the cap](mirror-3423-f-lifecycle-guard-budget-refusal-messages.md) | 3290 | The budget refusal reports the file's own line count. |
| [Lifecycle guard budget refusals name the file and the cap](mirror-3423-f-lifecycle-guard-budget-refusal-messages.md) | 3291 | A cap-line file read after earlier text reports what was left of the budget. |
| [Gateway post-tail exit bound: exit code, diagnostics, hand-back](mirror-3423-f-gateway-post-tail-exit-bound.md) | 3292 | The post-tail watcher hands the exit back on any BaseException and shares one SystemExit code mapping. |
| [Kanban priority capture reads the inventory at one fixed head](mirror-3423-f-kanban-priority-capture-fixed-head.md) | 3293 | Capture reads the inventory at one fixed head so mid-window creations join it. |
| [Kanban source receipt on the status-leaving wire op (spike R1)](mirror-3423-f-kanban-source-receipt-spike.md) | 3294 | Adds spike tests for source_receipt on the status-leaving wire op. |
| [Kanban source receipt on the status-leaving wire op (spike R1)](mirror-3423-f-kanban-source-receipt-spike.md) | 3295 | Adds the pending receipt table and trigger pair. |
| [Kanban source receipt on the status-leaving wire op (spike R1)](mirror-3423-f-kanban-source-receipt-spike.md) | 3296 | Replaces it with a one-statement pending receipt and purges it in _end_run. |
| [Kanban source receipt on the status-leaving wire op (spike R1)](mirror-3423-f-kanban-source-receipt-spike.md) | 3297 | Allows virtual wire fields through _wire_fields and keeps them on an unseen full row. |
| [Kanban source receipt on the status-leaving wire op (spike R1)](mirror-3423-f-kanban-source-receipt-spike.md) | 3298 | An old Board's forbidden_field on a virtual wire field drops it without an omission. |
| [Kanban workspace_source v1 (inert core PR-1)](mirror-3423-g-kanban-workspace-source-v1.md) | 3299 | Trims kanban_db.py to fit its health line budget. |
| [Kanban workspace_source v1 (inert core PR-1)](mirror-3423-g-kanban-workspace-source-v1.md) | 3300 | Wraps every voluntary ending and escalate in with_source_receipt, adds an AST guard and the multi-field strip-and-resend path. |
| [Kanban workspace_source v1 (inert core PR-1)](mirror-3423-g-kanban-workspace-source-v1.md) | 3301 | Adds the inert workspace_source core: manifest, clone proof and attestation, grant snapshot, routing, resolver with typed refusals, field fence. |
| [Kanban workspace_source v1 (inert core PR-1)](mirror-3423-g-kanban-workspace-source-v1.md) | 3302 | Health fixes: lint annotations, utf-8 git decoding, live checkout via project_root_str. |
| [Kanban workspace_source v1 (inert core PR-1)](mirror-3423-g-kanban-workspace-source-v1.md) | 3303 | Pins the Board row-field list to the merged Board change. |
| [Kanban workspace_source v1 (inert core PR-1)](mirror-3423-g-kanban-workspace-source-v1.md) | 3304 | Releases unused source grants without retaining custody and adds the refusal-block module. |
| [Kanban workspace_source v1 (inert core PR-1)](mirror-3423-g-kanban-workspace-source-v1.md) | 3305 | Review follow-ups for source refusals: orphan follow-up retention, feed-safety on follow-up failure, subprocess guards. |
| [Kanban review follow-ups: hold test name, GC logging, priority feed rows](mirror-3423-g-kanban-review-nit-followups.md) | 3306 | Renames the local observed-hold test to the hold-after-latest-boundary rule. |
| [Own-profile capability check honors the launch TERMINAL_* env](mirror-3423-g-kanban-own-profile-launch-terminal-env.md) | 3307 | Own-profile capability check applies the launch TERMINAL_* overlay. |
| [Borrowing launch hands its verdict across the store-Python relaunch](mirror-3423-g-venv-sync-borrowed-launch-handoff.md) | 3308 | A served borrowing launch hands its verdict across the relaunch so probes are not repeated. |
| [kanban_complete receipt schema: text pattern and size budget](mirror-3423-g-kanban-completion-receipt-schema.md) | 3309 | Receipt text fields advertise the writer's nonblank no-control pattern via one shared TEXT_PATTERN. |
| [CI review comments: refresh budget and budget-skip marking](mirror-3423-g-ci-review-budget-skip.md) | 3310 | Lowers the max refresh delay and marks budget-skipped targeted PRs as refresh failed. |
| [Chat-wait lag warning once per stall per board](mirror-3423-g-kanban-chat-wait-lag-warning.md) | 3311 | Keys the lag-warned state per board so a lagging board warns once per stall. |
| [Hub claim screen: named deferrals and origin-aware skip wording](mirror-3423-g-kanban-hub-claim-screen-deferrals.md) | 3312 | Names deferred cards in tick diagnostics and re-reads each card's row at its turn. |
| [CI-run progress check reads GitHub through the credential broker prefix](mirror-3423-g-kanban-ci-run-progress-broker.md) | 3313 | CI-run progress reads GitHub through the configured broker prefix. |
| [Update-pause: carried pause stamp taken back; doc nits](mirror-3423-g-update-pause-carried-turn-stamp.md) | 3314 | Takes back a carried pause's stamp when the pause does not stand. |
| [Kanban review follow-ups: hold test name, GC logging, priority feed rows](mirror-3423-g-kanban-review-nit-followups.md) | 3315 | Scratch GC logs each swept detached tree and fixes a comment. |
| [Kanban review follow-ups: hold test name, GC logging, priority feed rows](mirror-3423-g-kanban-review-nit-followups.md) | 3316 | Priority feed keeps terminal arrivals in capture rows and documents fixed-head limits. |
| [Hub claim screen: named deferrals and origin-aware skip wording](mirror-3423-g-kanban-hub-claim-screen-deferrals.md) | 3317 | Hub admission tests share a support module and the no-JSON1 fallback is documented. |
| [Kanban session provenance: reads_local_store shares scoped_sources routing](mirror-3423-g-kanban-provenance-local-store-routing.md) | 3318 | reads_local_store shares scoped_sources' routing through one helper. |
| [Kanban: say so when a write is queued locally while Board sync is stopped](mirror-3423-g-kanban-queued-local-board-delivery.md) | 3319 | Writes kept local by a stopped Board sync report queued_local instead of promising live-run delivery. |
| [Kanban: say so when a write is queued locally while Board sync is stopped](mirror-3423-g-kanban-queued-local-board-delivery.md) | 3320 | A create placed elsewhere while sync is stopped says the chat link waits. |
| [Kanban worker: fail-closed resolver exit and owner capsule preadmission proof](mirror-3423-g-kanban-worker-owner-refusals.md) | 3321 | A resolver child that exits early fails closed instead of raising BrokenPipeError. |
| [Kanban sync links pass serialization and fake-board fidelity](mirror-3423-g-kanban-sync-links-pass-and-fake-board.md) | 3322 | Test fake Board release refuses non-object extra, counts numbers like JSON.stringify, refuses spawn_failed on ops. |
| [CI review comments: refresh budget and budget-skip marking](mirror-3423-g-ci-review-budget-skip.md) | 3323 | Tests the CI-triggered budget-skip marker and words the skip log line per mode. |
| [Kanban: a run the holder ends frees its Board lease while the outbox is stalled](mirror-3423-g-kanban-run-end-lease-release.md) | 3324 | Run-ending transitions queue a direct lease release when the outbox is stalled. |
| [kanban_complete receipt schema: text pattern and size budget](mirror-3423-g-kanban-completion-receipt-schema.md) | 3325 | Shortens TEXT_PATTERN and documents the schema cost. |
| [Kanban sync links pass serialization and fake-board fidelity](mirror-3423-g-kanban-sync-links-pass-and-fake-board.md) | 3326 | Serializes links passes across loop restarts with a process-wide lock and adds a run-ref prefilter test. |
| [Kanban worker: fail-closed resolver exit and owner capsule preadmission proof](mirror-3423-g-kanban-worker-owner-refusals.md) | 3327 | An owner's pre-session capsule refusal books a preadmission proof and requeues. |
| [Own-profile capability check honors the launch TERMINAL_* env](mirror-3423-g-kanban-own-profile-launch-terminal-env.md) | 3328 | launch_terminal_env reads the live env until multiplexing and never freezes early. |
| [Chat-wait lag warning once per stall per board](mirror-3423-g-kanban-chat-wait-lag-warning.md) | 3329 | A sweep with nothing due ends the board's lag stall. |
| [Hub claim screen: named deferrals and origin-aware skip wording](mirror-3423-g-kanban-hub-claim-screen-deferrals.md) | 3330 | Sibling hub admission suites import the shared support module instead of another test module. |
| [Kanban operator delivery: claim classifier and unproven-caller refusal](mirror-3423-g-kanban-operator-claim-classifier.md) | 3331 | Spaced hyphen ends the claim window, be-posted is never a claim, deferrals take no tick slot. |
| [Kanban operator delivery: claim classifier and unproven-caller refusal](mirror-3423-g-kanban-operator-claim-classifier.md) | 3332 | Refuses an unproven caller before deferring it and extends dash and be/being handling. |
| [Desktop: session writes ride across a hub reconnect](mirror-3423-g-desktop-session-write-reconnect.md) | 3333 | Archive, pin and unread writes retry across a hub reconnect and replay with the same identity. |
| [Kanban placement: whole-label-set liveness and promotion keep reasons](mirror-3423-g-kanban-placement-label-liveness.md) | 3334 | A no-marker card queues only when one live machine holds queue and coordination labels. |
| [Kanban placement: whole-label-set liveness and promotion keep reasons](mirror-3423-g-kanban-placement-label-liveness.md) | 3335 | Annotates label_machines as str or Iterable of str. |
| [Hub claim screen: named deferrals and origin-aware skip wording](mirror-3423-g-kanban-hub-claim-screen-deferrals.md) | 3336 | Claim deferrals carry their origin and skip wording reads it. |
| [Kanban: a tui notice and its chat's local wake deliver each event once](mirror-3423-g-tui-notice-wake-custody.md) | 3337 | Delivers each tui notice and local wake event once and lets the gateway claim follow the wake-sibling rules. |
| [Kanban: a tui notice and its chat's local wake deliver each event once](mirror-3423-g-tui-notice-wake-custody.md) | 3338 | A settled held claim stays resolved after recovery and the poller sees lineage-root ownership. |
| [Kanban: a tui notice and its chat's local wake deliver each event once](mirror-3423-g-tui-notice-wake-custody.md) | 3339 | A root local wake settles and floors tui rows on compression tips too. |
| [Kanban: a tui notice and its chat's local wake deliver each event once](mirror-3423-g-tui-notice-wake-custody.md) | 3340 | Moves the left-open notice suffix to kanban_notice_text and shares a lineage helper. |
| [Kanban: a tui notice and its chat's local wake deliver each event once](mirror-3423-g-tui-notice-wake-custody.md) | 3341 | Legacy hand-back uses the gateway claim's predicate and probes owner state once per chat per poll. |
| [Kanban session provenance: reads_local_store shares scoped_sources routing](mirror-3423-g-kanban-provenance-local-store-routing.md) | 3342 | Resolves local_gateway=None through one shared resolver. |
| [Turn finalizer: a failed turn with a tool tail keeps its reason](mirror-3423-g-turn-finalizer-failed-tool-tail.md) | 3343 | A failed turn with a tool tail keeps its failure reason instead of pending_tool_result. |
| [CI-run progress check reads GitHub through the credential broker prefix](mirror-3423-g-kanban-ci-run-progress-broker.md) | 3344 | Binds the dispatching home in the CI-run sweep, warns per home and fixes gh-missing wording. |
| [Kanban: say so when a write is queued locally while Board sync is stopped](mirror-3423-g-kanban-queued-local-board-delivery.md) | 3345 | CLI writes kept local by the stopped sync print a queued-locally notice. |
| [Kanban sync links pass serialization and fake-board fidelity](mirror-3423-g-kanban-sync-links-pass-and-fake-board.md) | 3346 | Warns once per hold when another runner's pass holds the pass lock. |
| [Update-pause: carried pause stamp taken back; doc nits](mirror-3423-g-update-pause-carried-turn-stamp.md) | 3347 | Comment and docstring nits only. |
| [execute_code: cap the spill a remote file-RPC terminal result inlines](mirror-3423-g-execute-code-remote-spill-cap.md) | 3348 | Caps the spill a remote file-RPC terminal result inlines and documents raw spill output. |
| [execute_code: cap the spill a remote file-RPC terminal result inlines](mirror-3423-g-execute-code-remote-spill-cap.md) | 3349 | Bounds the encoded JSON result rather than just the raw spill. |
| [Chat-wait lag warning once per stall per board](mirror-3423-g-kanban-chat-wait-lag-warning.md) | 3350 | A lag stall ends only when the lag closes, not when nothing is due. |
| [kanban_complete receipt schema: text pattern and size budget](mirror-3423-g-kanban-completion-receipt-schema.md) | 3351 | Budgets schema bytes instead of pattern length and corrects the ref cost note. |
| [Kanban: a run the holder ends frees its Board lease while the outbox is stalled](mirror-3423-g-kanban-run-end-lease-release.md) | 3352 | Pins all holder run-end lease releases in tests and documents the stalled window. |
| [Kanban missing-parent detection judged only from a fresh replica](mirror-3423-g-kanban-missing-parent-freshness.md) | 3353 | Judges missing parents and unclaimed-ready notices only from a replica copy at the feed head. |
| [Kanban missing-parent detection judged only from a fresh replica](mirror-3423-g-kanban-missing-parent-freshness.md) | 3354 | Scopes a missing-parent streak to its parents and ends it at an unblock or link. |
| [Kanban missing-parent detection judged only from a fresh replica](mirror-3423-g-kanban-missing-parent-freshness.md) | 3355 | A detached board judges missing parents itself and only a link to a missing parent restarts the window. |
| [Kanban missing-parent detection judged only from a fresh replica](mirror-3423-g-kanban-missing-parent-freshness.md) | 3356 | Reports a stalled replica's deferral once per stale stamp at INFO. |
| [Kanban missing-parent detection judged only from a fresh replica](mirror-3423-g-kanban-missing-parent-freshness.md) | 3357 | A fresh pull vouches only once the mirror is refreshed from it. |
| [Kanban missing-parent detection judged only from a fresh replica](mirror-3423-g-kanban-missing-parent-freshness.md) | 3358 | Repeats the deferral line when the deferred count changes. |
| [Kanban placement: whole-label-set liveness and promotion keep reasons](mirror-3423-g-kanban-placement-label-liveness.md) | 3359 | Placement recovery promotion applies the hub claim screen's keep reasons. |
| [Own-profile capability check honors the launch TERMINAL_* env](mirror-3423-g-kanban-own-profile-launch-terminal-env.md) | 3360 | profile_config_scope reuses launch_terminal_env and stale frozen wording is dropped. |
| [Kanban sync links pass: lag, hold and failure diagnostics](mirror-3423-h-kanban-sync-links-pass-diagnostics.md) | 3361 | Pins that a nothing-due chat-wait sweep of an unwarned board skips the lag hold. |
| [Kanban move unpushed-work guard follow-ups](mirror-3423-h-kanban-move-unpushed-guard.md) | 3362 | Move guard scans the move's checkpoint, skips other cards' trees, bounds git time and guards the queue repin. |
| [Kanban move unpushed-work guard follow-ups](mirror-3423-h-kanban-move-unpushed-guard.md) | 3363 | Queue-repin follow-up tests stub the Board registry that pool eligibility reads. |
| [Kanban move unpushed-work guard follow-ups](mirror-3423-h-kanban-move-unpushed-guard.md) | 3364 | Pool-queue unpushed test stub accepts the timeout keyword of deadline-bounded git calls. |
| [Lifecycle guard: heredoc body masking and nested-shell proofs](mirror-3423-h-lifecycle-guard-heredoc-masking.md) | 3365 | Masks data-consumer heredoc bodies behind wrappers, in functions and nested, and treats call-syntax paths as mentions. |
| [Lifecycle guard: heredoc body masking and nested-shell proofs](mirror-3423-h-lifecycle-guard-heredoc-masking.md) | 3366 | Closes four fail-open shapes with a wrapper option allowlist and strict nested-owner rules. |
| [Lifecycle guard: heredoc body masking and nested-shell proofs](mirror-3423-h-lifecycle-guard-heredoc-masking.md) | 3367 | Requires proven variable bindings and intact stdin before nested heredoc masking. |
| [Lifecycle guard: heredoc body masking and nested-shell proofs](mirror-3423-h-lifecycle-guard-heredoc-masking.md) | 3368 | Preserves shell prefix and startup provenance and treats reserved environment assignments as non-transparent. |
| [Lifecycle guard: heredoc body masking and nested-shell proofs](mirror-3423-h-lifecycle-guard-heredoc-masking.md) | 3369 | Refuses masking for startup-reading shells and keyword-led subshells with possible aliases. |
| [Lifecycle guard: heredoc body masking and nested-shell proofs](mirror-3423-h-lifecycle-guard-heredoc-masking.md) | 3370 | Keeps the startup-file execution proof test hermetic under a PATH-resetting system profile. |
| [Lifecycle guard: heredoc body masking and nested-shell proofs](mirror-3423-h-lifecycle-guard-heredoc-masking.md) | 3371 | Gates the startup-file execution proof test on the startup file having been sourced. |
| [Lifecycle guard: heredoc body masking and nested-shell proofs](mirror-3423-h-lifecycle-guard-heredoc-masking.md) | 3372 | Validates every stdin-shell option after -s instead of returning at the first one. |
| [Lifecycle guard: heredoc body masking and nested-shell proofs](mirror-3423-h-lifecycle-guard-heredoc-masking.md) | 3373 | Adds a generated shape corpus checked against real bash for nested-heredoc masking. |
| [Lifecycle guard: heredoc body masking and nested-shell proofs](mirror-3423-h-lifecycle-guard-heredoc-masking.md) | 3374 | Makes session-snapshot aliases visible to the guard and runs startup-file proofs on CI. |
| [Update pause: carried-turn stamp handling](mirror-3423-h-update-pause-carried-turn-stamp.md) | 3375 | Keeps a carried turn's pause stamp when a backend exit cuts it short and pins job-stop take-back. |
| [Kanban chat task lists while a chat's own Board sync is down](mirror-3423-h-kanban-chat-links-owner-published.md) | 3376 | Anchors the stopped-sync queued create note on an exact head constant instead of rstrip. |
| [Desktop Kanban drawer: foreground reads on a slow link](mirror-3423-h-desktop-kanban-drawer-foreground-reads.md) | 3377 | Logs the slow-link entry line before any answer and tags first drawer reads as foreground. |
| [Kanban and terminal OOM attribution follow-ups](mirror-3423-h-kanban-oom-attribution-followups.md) | 3378 | Skips the kernel-log read on a clean exit and notes OOM kills in a command's own scope on rc 0. |
| [TUI gateway Kanban notice custody and sweep logging](mirror-3423-h-tui-kanban-notice-custody.md) | 3379 | Legacy hand-back and gateway claim read the ledger by one lineage rule, and the restore gap is pinned. |
| [Kanban park and release guards: run-end lease and awaited-review head](mirror-3423-h-kanban-park-release-guards.md) | 3380 | Tests a real blocked park and a savepoint-undone run end against the run-end lease release. |
| [Kanban sync links pass: lag, hold and failure diagnostics](mirror-3423-h-kanban-sync-links-pass-diagnostics.md) | 3381 | Repeats the held-lock warning past REWARN_S and pins the per-hold reset. |
| [Failed tool-tail turns close with the typed failed-turn row](mirror-3423-h-failed-tool-tail-turn-boundary.md) | 3382 | Stamps the failed-tool-tail close row as the typed failed-turn boundary with failure metadata. |
| [Kanban chat task lists while a chat's own Board sync is down](mirror-3423-h-kanban-chat-links-owner-published.md) | 3383 | The card owner publishes hub-subscription chat links so a chat's task list does not wait on its own sync. |
| [Managed worker adopts only its own session's orphaned jobs](mirror-3423-h-managed-worker-session-scoped-recovery.md) | 3384 | Managed worker startup recovery adopts only orphaned jobs of its own route and session. |
| [Kanban operator claim parsing: be/being, dashes, adverbs](mirror-3423-h-kanban-operator-claim-parsing.md) | 3385 | be/being govern only across adverbs, spaced or glued hyphen runs are dashes, and the replica caller check runs once per call. |
| [execute_code remote file-RPC result bounds and encoding](mirror-3423-h-execute-code-rpc-wire-encoding.md) | 3386 | Bounds every remote file-RPC result by shipped bytes and ships lone surrogates as escapes. |
| [Kanban hub placement, admission and recovery follow-ups](mirror-3423-h-kanban-placement-admission-followups.md) | 3387 | Placement recovery threads the board into hub_keep_reason and exposes public card_fields with a fail-closed test. |
| [Kanban hub placement, admission and recovery follow-ups](mirror-3423-h-kanban-placement-admission-followups.md) | 3388 | The hub CPU advisory names an unreadable pool limit as its own reason code. |
| [Aux client close safety against abandoned provider daemons](mirror-3423-h-aux-client-tls-fd-safety.md) | 3389 | Never closes a per-call aux client under its abandoned provider daemon's TLS read. |
| [Aux client close safety against abandoned provider daemons](mirror-3423-h-aux-client-tls-fd-safety.md) | 3390 | Defers the abandoned client's close before the shutdown, scopes native-adapter streams and enables the tripwire via config. |
| [Launch overlay documentation: live env until multiplexing](mirror-3423-h-launch-overlay-docs.md) | 3391 | Corrects launch-overlay docstrings and comments to say the env is live until multiplexing is active. |
| [Terminal guard: sanctioned-route hint when refusing hermes-sync](mirror-3423-h-hermes-sync-refusal-hint.md) | 3392 | Refusing hermes-sync in the gateway now names the sanctioned deploy route. |
| [Desktop Kanban drawer: foreground reads on a slow link](mirror-3423-h-desktop-kanban-drawer-foreground-reads.md) | 3393 | A retried first drawer read stays foreground and a warm-cache re-open still sends its read. |
| [Kanban quota recovery: OAuth proof wire and failed-proof cooldown](mirror-3423-h-kanban-quota-recovery-proof-wire.md) | 3394 | Quota recovery proof uses the OAuth client's wire headers, adds a failed-proof cooldown and guards the drain and resolver timeout. |
| [macOS WAL-holder scan: serve an expired clean verdict while refreshing](mirror-3423-h-darwin-wal-holder-stale-verdict.md) | 3395 | Serves an expired same-key clean WAL-holder verdict while one pass refreshes it. |
| [Kanban chat task lists while a chat's own Board sync is down](mirror-3423-h-kanban-chat-links-owner-published.md) | 3396 | A card placed from a chat carries the chat's created link to the Board from its owner. |
| [Package manager account-seed opt-out and per-lock seed marker](mirror-3423-h-pm-account-seed-opt-out.md) | 3397 | Adds the HERMES_PM_ACCOUNT_SEED=0 opt-out, a per-lock seed marker and one hash per seeded install. |
| [TUI gateway Kanban notice custody and sweep logging](mirror-3423-h-tui-kanban-notice-custody.md) | 3398 | Remote-notice sweep skips and live-poller claims log once per reason and the busy non-owner hand-back is pinned. |
| [Desktop boot waits out a remote's own update](mirror-3423-h-desktop-remote-update-boot-wait.md) | 3399 | Boot polls past a remote's own update refusal instead of parking on the could-not-start overlay. |
| [Code skew ignores tests, docs and CI-only commits](mirror-3423-h-code-skew-restart-ignore-paths.md) | 3400 | A checkout move over tests, docs and CI commits only is no skew and triggers no restart. |
| [Delta responses for large reads over the Desktop's SSH tunnel](mirror-3423-h-desktop-delta-responses.md) | 3401 | Adds zstd delta encoding for large JSON reads between the gateway and the Desktop's tunnel client. |
| [Managed worker adopts only its own session's orphaned jobs](mirror-3423-h-managed-worker-session-scoped-recovery.md) | 3402 | Documents the recovery scope and passes a recover stub into the _execute test helper. |
| [execute_code remote file-RPC result bounds and encoding](mirror-3423-h-execute-code-rpc-wire-encoding.md) | 3403 | Shipped files use strict UTF-8 and the oversize-result error says the call ran. |
| [Kanban move unpushed-work guard follow-ups](mirror-3423-h-kanban-move-unpushed-guard.md) | 3404 | Reports unchecked cause on spent budget or timeout and shares the repin leaving rule. |
| [Host loop lease: one flock-held owner per Hermes home](mirror-3423-h-host-loop-lease.md) | 3405 | Adds a flock-held lease giving host-wide background loops one owner per Hermes home, with doctor output. |
| [Kanban and terminal OOM attribution follow-ups](mirror-3423-h-kanban-oom-attribution-followups.md) | 3406 | Reads the worker log once on reclaim and uses firm OOM wording only for kill exits. |
| [Kanban sync links pass: lag, hold and failure diagnostics](mirror-3423-h-kanban-sync-links-pass-diagnostics.md) | 3407 | Names the acquire window in the held-elsewhere warning and makes the clock patch fail loudly. |
| [Kanban operator claim parsing: be/being, dashes, adverbs](mirror-3423-h-kanban-operator-claim-parsing.md) | 3408 | Hyphen prefixes stay under be/being, common -ly non-adverbs do not govern, and glued hyphen runs are dashes. |
| [Kanban hub placement, admission and recovery follow-ups](mirror-3423-h-kanban-placement-admission-followups.md) | 3409 | Pins the available_mode board hop and makes card_fields raise LookupError. |
| [Kanban hub placement, admission and recovery follow-ups](mirror-3423-h-kanban-placement-admission-followups.md) | 3410 | Wording-only fix limiting hub_cpu_pool_limit_unknown to the priority-pool settings read. |
| [Update pause: carried-turn stamp handling](mirror-3423-h-update-pause-carried-turn-stamp.md) | 3411 | A carried turn the exit did not interrupt takes its stamp back, with one user-stop constant. |
| [Kanban sync links pass: lag, hold and failure diagnostics](mirror-3423-h-kanban-sync-links-pass-diagnostics.md) | 3412 | Times failed boards and names slow-but-unchanged boards in the links pass. |
| [Kanban hub placement, admission and recovery follow-ups](mirror-3423-h-kanban-placement-admission-followups.md) | 3413 | The deny route queues only when one fresh machine holds the whole label set. |
| [Kanban sync links pass: lag, hold and failure diagnostics](mirror-3423-h-kanban-sync-links-pass-diagnostics.md) | 3414 | Test fake board fences and validates spawn_failed release before refusing it. |
| [Terminal guard: sanctioned-route hint when refusing hermes-sync](mirror-3423-h-hermes-sync-refusal-hint.md) | 3415 | Ties the refusal hint to an executed hermes-sync and the root sync log. |
| [Aux client close safety against abandoned provider daemons](mirror-3423-h-aux-client-tls-fd-safety.md) | 3416 | Gates the abandoned daemon's client close on the owner's shutdown sweep and documents the debug_ssl_close option. |
| [Scratch custody pressure API for worker disk reclaim](mirror-3423-h-kanban-scratch-custody-pressure-api.md) | 3417 | Adds a pressure API so disk reclaim can see and safely remove evicted-owner trees. |
| [Kanban missing-parent repair card may unblock its child](mirror-3423-h-kanban-missing-parent-repair-unblock.md) | 3418 | Lets a missing-parent repair card unblock its own parent_missing child via the tool and operator call. |
| [Kanban missing-parent repair card may unblock its child](mirror-3423-h-kanban-missing-parent-repair-unblock.md) | 3419 | The repair grant refuses a re-held child and a stale repair run. |
| [Kanban missing-parent repair card may unblock its child](mirror-3423-h-kanban-missing-parent-repair-unblock.md) | 3420 | Test pinning that the repair grant needs the child to still be blocked. |
| [Kanban park and release guards: run-end lease and awaited-review head](mirror-3423-h-kanban-park-release-guards.md) | 3421 | Awaited-review release re-reads the PR head, uses one gh read, one parked-run definition and strict wait-check parsing. |
| [Evidence-based work state observations (execution observations v1)](execution-observations.md) | 3422 | A parked remote card projects one record (status-derived last_known_state, owner, ask) in the session task list and in the sidebar counts / session-idle-check path. |
| [Desktop Kanban drawer: foreground reads on a slow link](mirror-3423-h-desktop-kanban-drawer-foreground-reads.md) | 3423 | A drawer kind whose reads keep failing stops sending foreground refetches. |

## Mirror follow-ups 2910–2926

| Feature | Patches | Scope |
|---|---|---|
| [`--up-to` refusal metric class](mirror-2926-followups.md) | 2910 | A refused `hermes update --up-to` is counted as `failure_class = up_to_refused` instead of `aborted_before_apply`. |
| [Always-on rollover warms every unarchived board](mirror-2926-followups.md) | 2911 | The roller also warms each unarchived Kanban board the successor lists before switching traffic. |
| [`--up-to` refusal metric class](mirror-2926-followups.md) | 2912 | Test-only: a pre-receipt `up_to_refused` stop keeps its class, creates no receipt and validates against the v4 schema. |
| [Test-only hygiene: node-deps gate, takeover import, clarify wait](mirror-2926-followups.md) | 2913 | Test-only: the stopped-owner node-deps test now proves the pnpm shim was still gated when stopped. |
| [Worker-queue placement for courier and origin-bound creates](mirror-2926-followups.md) | 2914 | Hub-denied courier creates and origin-bound creates join the worker queue, and `kanban move --pool` repins any ready worker-first card. |
| [Test-only hygiene: node-deps gate, takeover import, clarify wait](mirror-2926-followups.md) | 2915 | Test-only: drop a redundant local `import os` in the foreign-namespace takeover test. |
| [Priority migration tolerates the sync clamp](mirror-2926-followups.md) | 2916 | Priority migrate/restore treat a cap-clamped replica value as holding an above-cap manifest original; guarded restore caps instead of refusing. |
| [Update checkpoint census agrees with the checkpoint](mirror-2926-followups.md) | 2917 | `active-work` carries the checkpoint's own verdict, refusals name unmapped units, and a refused pause announces no restart. |
| [Cron aborted-drain latch and shared shutdown predicate](mirror-2926-followups.md) | 2918 | An aborted restart-wait drain keeps the run's shutdown latch (safe-side rerun) and the fallback shares the owner-draining predicate. |
| [Test-only hygiene: node-deps gate, takeover import, clarify wait](mirror-2926-followups.md) | 2919 | Test-only: the clarify timeout test waits for the withdrawal frame and asserts its exact payload. |
| [macOS state CI lane on main only for state-surface pushes](mirror-2926-followups.md) | 2920 | The standalone macOS state lane runs on main pushes only for the classifier's state surface, with a four-hourly scheduled backstop. |
| [macOS state CI lane on main only for state-surface pushes](mirror-2926-followups.md) | 2921 | Corrects the paths-filter limit comment and asserts the filter patterns use only `*` and `**`. |
| [Desktop update pause withdraws prepared approvals](mirror-2926-followups.md) | 2922 | A Desktop turn pausing for update withdraws unanswered approval requests (neither approving nor denying) so it can reach its boundary. |
| [Lifecycle-scan depth exhaustion reported as an incomplete scan](mirror-2926-followups.md) | 2923 | Hitting the referenced-script depth limit on executed text now says the scan was incomplete rather than implying a detected gateway command. |
| [Desktop unread-dot rationale comments](mirror-2926-followups.md) | 2924 | Comment-only: explains why profile and bot unread dots stay plain and what an outcome tone would require. |
| [Desktop unread-dot rationale comments](mirror-2926-followups.md) | 2925 | Comment-only: qualifies the rationale for all-profiles scope. |
| [Owner-terminal ask lint](owner-terminal-ask-lint-2131.md) | 2926 | Reject article-free and possessive owner-chat relays; preserve attested canonical owner-terminal actions. |

## Mirror follow-ups 2727–2909

| Feature | Patches | Scope |
|---|---|---|
| [Exact-head CI review comment refresh](mirror-2909-a-ci-review-refresh.md) | 2727 | ci(review-comment): refresh the PR's bot comment when CI completes on its wt/ head |
| [Card references, creator subscriptions and restored origin custody](mirror-2909-a-references-and-origin-delivery.md) | 2728 | fix(kanban): phantom-ref scan resolves short-form ids by prefix and skips ids inside paths/branches |
| [Hub-probed atomic publication of worker labels](mirror-2909-a-operator-label-publish.md) | 2729 | feat(kanban): operator call label_publish — hub-probed, one atomic Board labels_add [t_01a1cefefcad176a] |
| [Hub-probed atomic publication of worker labels](mirror-2909-a-operator-label-publish.md) | 2730 | fix(kanban): label_publish label cap tracks the Board's 512, not the stale 32 [t_01a1cefefcad176a] |
| [Ask-probe grace, legacy loss and delivery-time stale-ask refresh](mirror-2909-a-ask-probe-custody.md) | 2731 | Buffered tui Kanban block notices re-read a stale ask at delivery |
| [Secret-safe wake checks and live-claim park protection](mirror-2909-a-park-safety.md) | 2732 | CLI park --wake-check refuses an inline secret like the tool path (wake_check_secret) |
| [Race-resistant test fixtures and module-local platform seams](mirror-2909-a-test-fixture-isolation.md) | 2733 | test(conftest): required-extras probe runs from tmp_path, not inside tests/ |
| [Quota evidence, explicit release and safe alternate routes](mirror-2909-a-quota-auth-and-routing.md) | 2734 | test: a revoked-401 requeue (exit 75) carries no sibling's weekly quota reset |
| [Feed-credited repair windows and bounded replica projection memory](mirror-2909-a-sync-replica-efficiency.md) | 2735 | fix(kanban-sync): a pull that only applied feed rows defers the full repair pass like a replica-cursor move [daveey] |
| [Card references, creator subscriptions and restored origin custody](mirror-2909-a-references-and-origin-delivery.md) | 2736 | fix(kanban): a worker's child of a picked-up creator copy gets its owner chat's sub |
| [Card references, creator subscriptions and restored origin custody](mirror-2909-a-references-and-origin-delivery.md) | 2737 | fix(kanban): owner-sub fallback only for a foreign creator copy; an unsubscribed local chat stays unsubscribed |
| [Worker pickup budgets, truthful stall alarms and placement remedies](mirror-2909-a-pickup-and-placement.md) | 2738 | Worker pickup skips a card the local dispatcher would hold on its quota wall |
| [Worker pickup budgets, truthful stall alarms and placement remedies](mirror-2909-a-pickup-and-placement.md) | 2739 | Pickup records a quota-walled card as quota_hold, expected until its deadline |
| [Ask-probe grace, legacy loss and delivery-time stale-ask refresh](mirror-2909-a-ask-probe-custody.md) | 2740 | fix(kanban): tolerate transient ask probe failures and report lost detection [daveey] |
| [Ask-probe grace, legacy loss and delivery-time stale-ask refresh](mirror-2909-a-ask-probe-custody.md) | 2741 | fix(kanban): backfill legacy probe loss and verify sync fences [daveey] |
| [Ask-probe grace, legacy loss and delivery-time stale-ask refresh](mirror-2909-a-ask-probe-custody.md) | 2742 | fix(kanban): notify legacy probe losses exactly once [daveey] |
| [Ask-probe grace, legacy loss and delivery-time stale-ask refresh](mirror-2909-a-ask-probe-custody.md) | 2743 | fix(kanban): deduplicate probe loss by portable event identity [daveey] |
| [Ask-probe grace, legacy loss and delivery-time stale-ask refresh](mirror-2909-a-ask-probe-custody.md) | 2744 | test(kanban): kill status and replay identity mutants [daveey] |
| [Durable notices through lease contention, reaping and update handoff](mirror-2909-a-notice-lifecycle.md) | 2745 | refactor(gateway): move four kanban notice suffixes into kanban_notice_text |
| [Answered-turn compression, failure outcomes and child scratch isolation](mirror-2909-a-turns-and-child-scratch.md) | 2746 | fix(compression): an answered request is not "in flight" during a later notice turn |
| [Worker pickup budgets, truthful stall alarms and placement remedies](mirror-2909-a-pickup-and-placement.md) | 2747 | fix(kanban): capacity note names the machine behind machine-scoped restrictions |
| [Safe clean-WAL verdict reuse and exact bounded recency pages](mirror-2909-a-state-read-efficiency.md) | 2748 | perf(state): keep the clean deleted-WAL holder verdict across unrelated directory churn |
| [Answered-turn compression, failure outcomes and child scratch isolation](mirror-2909-a-turns-and-child-scratch.md) | 2749 | Non-git subagents get a private scratch dir and a SHARED-cwd prompt block |
| [Worker pickup budgets, truthful stall alarms and placement remedies](mirror-2909-a-pickup-and-placement.md) | 2750 | fix(kanban): explicit placement='auto' beats a context skill's local marker; remedy names 'auto' |
| [Quota evidence, explicit release and safe alternate routes](mirror-2909-a-quota-auth-and-routing.md) | 2751 | fix(quota): an auth-benched OAuth account is not quota evidence; release false episode walls |
| [Quota evidence, explicit release and safe alternate routes](mirror-2909-a-quota-auth-and-routing.md) | 2752 | fix(quota): a disproved hinted wall keeps its doubling backoff |
| [Quota evidence, explicit release and safe alternate routes](mirror-2909-a-quota-auth-and-routing.md) | 2753 | fix(quota): a reap never mirrors a disproved hint; prune false provider-wide walls |
| [Cron drain observation, once-only accounting and restart-cut reruns](mirror-2909-a-cron-restart-accounting.md) | 2754 | fix(gateway): a restart's drain still answers an external cron firer's status/recover/cancel |
| [Race-resistant test fixtures and module-local platform seams](mirror-2909-a-test-fixture-isolation.md) | 2755 | test(browser): fake_daemon awaits the stand-in's visible argv before ownership checks |
| [Quota evidence, explicit release and safe alternate routes](mirror-2909-a-quota-auth-and-routing.md) | 2756 | pool auth wall: bench_token_fp counts only for the bench it was stamped with |
| [Durable notices through lease contention, reaping and update handoff](mirror-2909-a-notice-lifecycle.md) | 2757 | fix(tui): a notice held for a refused turn keeps its session from the reapers |
| [Durable notices through lease contention, reaping and update handoff](mirror-2909-a-notice-lifecycle.md) | 2758 | refactor(tui): move the HUD surface note helpers to session_notifications_surface (file-size cap) |
| [Worker pickup budgets, truthful stall alarms and placement remedies](mirror-2909-a-pickup-and-placement.md) | 2759 | Pickup-stall detector: a brief worker-first reservation is not a pinned-card stall, and one card gets one host-wide repair |
| [Quota evidence, explicit release and safe alternate routes](mirror-2909-a-quota-auth-and-routing.md) | 2760 | fix(kanban): `kanban quota-release` ends a stale quota episode; the release is an episode boundary |
| [Race-resistant test fixtures and module-local platform seams](mirror-2909-a-test-fixture-isolation.md) | 2761 | test(dashboard): publish kill-grace pid files by rename so a starved child never hands out '' |
| [Worker pickup budgets, truthful stall alarms and placement remedies](mirror-2909-a-pickup-and-placement.md) | 2762 | fix(kanban): a card the Board shows held by a respawn guard never takes the worker pickup budget |
| [Worker pickup budgets, truthful stall alarms and placement remedies](mirror-2909-a-pickup-and-placement.md) | 2763 | fix(kanban): record a Board guard hold as guard_held, not the registry 'hold' |
| [Durable notices through lease contention, reaping and update handoff](mirror-2909-a-notice-lifecycle.md) | 2764 | test(kanban-wake): a claimed wake whose last card tail runs on mid-line is left unchanged |
| [Quota evidence, explicit release and safe alternate routes](mirror-2909-a-quota-auth-and-routing.md) | 2765 | kanban quota episode: a strong-tier class route takes its class's free provider instead of waiting out one provider's wall |
| [Cron drain observation, once-only accounting and restart-cut reruns](mirror-2909-a-cron-restart-accounting.md) | 2766 | fix(cron): one execution books one outcome when a dead worker and its run both report it [daveey] |
| [Durable notices through lease contention, reaping and update handoff](mirror-2909-a-notice-lifecycle.md) | 2767 | fix(gateway): a completion note waits out another turn's lease; recover failed completion admissions |
| [Safe clean-WAL verdict reuse and exact bounded recency pages](mirror-2909-a-state-read-efficiency.md) | 2768 | perf(state): recency pages probe messages only for rows that can reach the page |
| [Worker pickup budgets, truthful stall alarms and placement remedies](mirror-2909-a-pickup-and-placement.md) | 2769 | fix(kanban): mixed-skill marker remedy gives the device remedy; document auto_overridable_skill_markers |
| [Race-resistant test fixtures and module-local platform seams](mirror-2909-a-test-fixture-isolation.md) | 2770 | fix(tests): lockguard darwin + repair snapshot tests patch module seams, not shared os/time [daveey] |
| [Answered-turn compression, failure outcomes and child scratch isolation](mirror-2909-a-turns-and-child-scratch.md) | 2771 | outcome: a chat ending on a failed_turn row is unresolved{turn_failed}, not judged prose |
| [Review completion contracts and execution-neutral Board grants](mirror-2909-a-review-checkpoints.md) | 2772 | test(kanban): a home operator resume releases a worker's kept and fresh copies |
| [Worker pickup budgets, truthful stall alarms and placement remedies](mirror-2909-a-pickup-and-placement.md) | 2773 | hub admission: pickup's judge binds the owner secret scope in the gateway dispatcher; log each unavailable branch |
| [Ask-probe grace, legacy loss and delivery-time stale-ask refresh](mirror-2909-a-ask-probe-custody.md) | 2774 | test(kanban): cover stale-ask mutants M3/M3b (since_line + F8 guards) and M6 (tui unkeyed submit) |
| [Review completion contracts and execution-neutral Board grants](mirror-2909-a-review-checkpoints.md) | 2775 | fix(kanban): the same-second checkpoint and cooldown readers skip unused Board grants [daveey] |
| [Quota evidence, explicit release and safe alternate routes](mirror-2909-a-quota-auth-and-routing.md) | 2776 | fix(kanban): route review_unpinned and non-fresh pins off a known quota wall before spawn |
| [Review completion contracts and execution-neutral Board grants](mirror-2909-a-review-checkpoints.md) | 2777 | kanban_complete: gate a core-PR review even when its brief asked for `pr`, not `reviewed_pr` |
| [Secret-safe wake checks and live-claim park protection](mirror-2909-a-park-safety.md) | 2778 | fix(kanban): pin wake_check secret refusal to force=True; keep its text intact under the redactor |
| [Worker pickup budgets, truthful stall alarms and placement remedies](mirror-2909-a-pickup-and-placement.md) | 2779 | fix(kanban): a pickup-stall alarm may re-place a listed card onto the pool queue |
| [Card references, creator subscriptions and restored origin custody](mirror-2909-a-references-and-origin-delivery.md) | 2780 | fix(kanban): a courier card restored from the Board publishes its exact origin marker again |
| [Durable notices through lease contention, reaping and update handoff](mirror-2909-a-notice-lifecycle.md) | 2781 | Retry the remote-notice ledger's busy WAL switch instead of raising |
| [Secret-safe wake checks and live-claim park protection](mirror-2909-a-park-safety.md) | 2782 | Refuse an outside kanban_schedule/kanban_block on a card a live worker owns |
| [Cron drain observation, once-only accounting and restart-cut reruns](mirror-2909-a-cron-restart-accounting.md) | 2783 | fix(cron): a run cut by the owner's restart is rerun once, never booked failed |
| [Feed-credited repair windows and bounded replica projection memory](mirror-2909-a-sync-replica-efficiency.md) | 2784 | Bound the hub-replica remote-links read's heap: packed rows, slim records, paged reads |
| [Feed-credited repair windows and bounded replica projection memory](mirror-2909-a-sync-replica-efficiency.md) | 2785 | Replica slim rows keep whole-record release evidence; small deltas read in one seq-indexed query; real memory bounds (review of #1452) |
| [Durable notices through lease contention, reaping and update handoff](mirror-2909-a-notice-lifecycle.md) | 2786 | Every headless cold resume leaves a paused turn to the restarting backend; the window covers the hand-off |
| [Feed-credited repair windows and bounded replica projection memory](mirror-2909-a-sync-replica-efficiency.md) | 2787 | test(kanban-sync): reject upload receipt with right sha256/size but foreign blob_key |
| [Browse reconcile budget armed before macOS durability pragmas](mirror-2909-b-browse-reconcile-budget-macos.md) | 2788 | The browse catch-up arms its time budget before the macOS fsync/synchronous pragmas, and BEGIN runs inside the rollback try. |
| [CI review comment: footer, writer-race state line, Docker trigger](mirror-2909-b-ci-review-comment-state-line.md) | 2789 | The PR bot comment records a hidden per-run state line so an older run never overwrites a newer snapshot; Docker completion also refreshes it. |
| [Quota: revoked-401 churn is not a quota wall; `quota-release --probe`](mirror-2909-b-quota-auth-churn-probe-release.md) | 2790, 2791 | A pool emptied by 401/revoked rows books a retry with no quota episode; `kanban quota-release --probe` releases only on live headroom. |
| [Gateway dispatcher tick read budget](mirror-2909-b-dispatcher-tick-read-budget.md) | 2792 | A dispatcher tick reads profile names only and opens each other board about once. |
| [Gateway: a unit's `gateway run` child behind a wrapper is a service gateway](mirror-2909-b-gateway-service-child-pids.md) | 2793, 2810 | Service PID discovery includes the gateway child of a wrapper MainPID but stops at the first gateway, not its whole subtree. |
| [Kanban notices: review-contract refusal cause and last checkpoint in ready-wait](mirror-2909-b-kanban-card-notice-text.md) | 2794, 2797 | The review-contract refusal names its cause on the core-pull path; ready-wait notices carry the card's last checkpoint. |
| [Test and CI flake hardening](mirror-2909-b-test-flake-hardening.md) | 2795, 2801, 2811, 2813, 2814 | Deflake fixes: monotonic-seam test, owned-subprocess cleanup, browser daemon owner re-check, admission-lock test, portable deflake guidance. |
| [Update: named checkpoint refusals, duplicate gateways, deferral streak floor](mirror-2909-b-update-checkpoint-refusal-cause.md) | 2796, 2804 | Refusals of `checkpoint-for-update` carry a reason, duplicate `gateway run` PIDs are not paused, and deferral cards need a 15-minute streak. |
| [Take over: ACK deadline pins and desktop reset-save refusal](mirror-2909-b-session-takeover-ack-deadline.md) | 2798, 2841 | Tests pin that an unprovable-liveness writer is refused at the ACK deadline; desktop keeps the 4090 refusal if the reset save fails. |
| [Kanban walls: multiplexed pool reads and review-reroute longest wall](mirror-2909-b-quota-wall-pool-reads-and-reroute.md) | 2799, 2809 | Wall readers bind the home secret scope in a multiplexing dispatcher; a short own wall no longer hides a longer known wall. |
| [Supervisor requests: survive requester completion, expire at 24 h](mirror-2909-b-supervisor-requests-lifecycle.md) | 2800, 2833 | A filed supervisor request still runs after its requester completes; never-accepted requests older than 24 h are rejected at accept. |
| [Notice guarantee: busy close-out wakes are deferred, not final](mirror-2909-b-notice-guarantee-busy-wakes.md) | 2802, 2803, 2842, 2843 | A close-out wake refused as busy (admission held by an update) is retried with backoff, legacy final-busy rows are re-offered, and two backends wake once. |
| [Update pause: successor ownership follows the startup sweep](mirror-2909-b-update-pause-successor-yield.md) | 2805 | A paused turn the startup sweep will not take is owed to no successor; automation replay rechecks span the yield window. |
| [Quota episodes: map stored run ids to this replica](mirror-2909-b-quota-episode-local-run-ids.md) | 2806, 2838 | Release and outstanding/unhinted readers resolve a stored episode's foreign run id through the replica's own run id. |
| [CI upstream sync gate: bounded GitHub reads on the canonical slug](mirror-2909-b-ci-upstream-sync-gate-quota.md) | 2807 | The upstream sync gate reads jobs once per actionable head, caches verdicts on disk, and calls the API on the canonical repo slug. |
| [Kanban links: `remote_links_and_origin_rows` forwards `stale_ok`](mirror-2909-b-kanban-links-stale-ok.md) | 2808 | Display routes can read the warm stale links entry without waiting on the global links lock. |
| [Kanban CLI: schedule/block from outside refuses a live worker's card](mirror-2909-b-kanban-cli-outside-park-live-claim.md) | 2812 | `hermes kanban schedule|block` by a non-owner refuses a card with a live claimed run. |
| [Kanban placement: backticked names are not host markers](mirror-2909-b-kanban-placement-quoted-names.md) | 2815 | Job, workflow and repo names in backticks are excluded from local-placement marker scans. |
| [Kanban `edit --requires` (orchestrator-only capability correction)](mirror-2909-b-kanban-requires-edit.md) | 2816, 2831 | `hermes kanban edit <id> --requires ...` audits a replacement of declared capabilities on idle open cards; workers cannot use it. |
| [Kanban placement recovery: promotion and stale-pin recovery via the Board](mirror-2909-b-kanban-placement-recovery.md) | 2817, 2818, 2819, 2820, 2821, 2822, 2823, 2824, 2825, 2826, 2827, 2828, 2829, 2830 | Worker nodes ask the Board hub to promote legacy local cards and recover stale machine pins, with fresh-evidence, custody, backoff and fairness guards. |
| [Kanban line-cap headroom: hub route and flag-parser moves](mirror-2909-b-kanban-cap-headroom-moves.md) | 2832 | Pure code moves so `kanban.py` and `kanban_sync_client.py` stay under the 2000-line file cap. |
| [Session outcome: silent unterminated subagent is lost, not done](mirror-2909-b-outcome-lost-subagent.md) | 2834, 2835 | A delegate child that never ended and went silent makes the chat outcome `unresolved{subagent_lost}` instead of a false green done. |
| [Core update: preserve gateway-hosted oneshot/lane chat turns](mirror-2909-b-update-pausable-gateway-chat.md) | 2836 | Finite local gateway chat turns park at a durable update boundary (mid-tool cancel after a grace), and one-shot `hermes -z` redials the restarted owner. |
| [Cron: a drain deferral spends no restart rerun](mirror-2909-b-cron-deferral-no-rerun.md) | 2837 | Deferral markers carry `deferred=true` and do not count as a restart rerun. |
| [Relay-minted revoked 401: re-pull and adopt the pushed token before rotating](mirror-2909-b-relay-oauth-revoked-repull.md) | 2839, 2840 | A revoked 401 on a relay-managed OAuth row triggers a host pull and waits for the pushed token instead of rotating or giving up. |
| [Notifier wakes held during core drain; claim waits split](mirror-2909-b-notifier-core-drain-hold.md) | 2844, 2845, 2846, 2847, 2848 | Kanban notifier batches and queued wakes are deferred while a core update drains; fixtures, checkpoint precedence, a CC refactor and wake-fold tagging follow. |
| [Cron owner shutdown survives the drain ending](mirror-2909-c-cron-shutdown-latches.md) | 2849 | An agent cron run stopped by its owner gateway’s restart is reported as a shutdown interruption, not a lost-ownership failure. |
| [Deterministic partial-transfer pause regression](mirror-2909-c-download-pause-fixture.md) | 2850 | Test-only: a browsed local-model download pause/resume test observes a genuinely incomplete PM range transfer even when progress callbacks are coalesced on a throttled runner. |
| [Ordered IDs for legacy recovery-cursor coverage](mirror-2909-c-recovery-cursor-fixture.md) | 2851 | Test-only: the legacy-row cursor test no longer assumes randomly suffixed same-millisecond task IDs sort in creation order. |
| [Independent contention for admission timeout legs](mirror-2909-c-admission-timeout-fixture.md) | 2852 | Test-only: both admission `reserve(timeout=...)` and `extend(timeout=...)` must actually wait out a held lock, so either function ignoring the timeout fails the test. |
| [Retiring failed-result observers stop only their own unit](mirror-2909-c-failed-observer-retirement.md) | 2853 | A failed-result observer with unusable context or an import failure retires with exit 78 and queues a best-effort stop of its own systemd user unit. |
| [Retiring failed-result observers stop only their own unit](mirror-2909-c-failed-observer-retirement.md) | 2854 | A failed-result observer with unusable context or an import failure retires with exit 78 and queues a best-effort stop of its own systemd user unit. |
| [Unread session dots use the stored outcome tone](mirror-2909-c-unread-outcome-tone.md) | 2855 | An unread session row keeps its filled/new-content dot, but waiting/running/unresolved/decision/muted outcomes use their stripe’s tone rather than success emerald. |
| [Finite gateway chats wait through update refusals](mirror-2909-c-oneshot-update-wait.md) | 2856 | Finite gateway chats can wait through a core-update owner replacement rather than immediately lose an unadmitted prompt. |
| [Bounded always-on dashboard boot prewarm gate](mirror-2909-c-boot-prewarm-gate.md) | 2857 | The always-on dashboard waits for its boot prewarms before lifespan startup completes and the socket binds, subject to a shared wait cap. |
| [Native macOS memory tests participate in the required CI gate](mirror-2909-c-native-memory-ci-gate.md) | 2858 | When CI selects the Darwin native-memory lane, “All required checks pass (v2)” requires its success. |
| [Provider-wall reset and profile-scope regression guards](mirror-2909-c-provider-wall-guards.md) | 2859 | Test-only: pin the later of file/live-pool reset deadlines and preserve launch-home credential scope on dispatcher reads, without replacing an already routed turn’s scope. |
| [Unread session dots use the stored outcome tone](mirror-2909-c-unread-outcome-tone.md) | 2860 | An unread session row keeps its filled/new-content dot, but waiting/running/unresolved/decision/muted outcomes use their stripe’s tone rather than success emerald. |
| [Terminal delegations close their own child session lineages](mirror-2909-c-delegation-child-lifecycle.md) | 2861 | Recovery of a dead-owner delegation and stall force-finalization close still-open child rows belonging to that unit. |
| [One local Kanban wake carries its TUI sibling’s notices](mirror-2909-c-local-notice-wake-dedup.md) | 2862 | A waking local subscription and a TUI subscription for the same card/chat no longer intentionally start separate turns for the same delivered event. |
| [One local Kanban wake carries its TUI sibling’s notices](mirror-2909-c-local-notice-wake-dedup.md) | 2863 | A waking local subscription and a TUI subscription for the same card/chat no longer intentionally start separate turns for the same delivered event. |
| [One local Kanban wake carries its TUI sibling’s notices](mirror-2909-c-local-notice-wake-dedup.md) | 2864 | A waking local subscription and a TUI subscription for the same card/chat no longer intentionally start separate turns for the same delivered event. |
| [One local Kanban wake carries its TUI sibling’s notices](mirror-2909-c-local-notice-wake-dedup.md) | 2865 | A waking local subscription and a TUI subscription for the same card/chat no longer intentionally start separate turns for the same delivered event. |
| [One local Kanban wake carries its TUI sibling’s notices](mirror-2909-c-local-notice-wake-dedup.md) | 2866 | A waking local subscription and a TUI subscription for the same card/chat no longer intentionally start separate turns for the same delivered event. |
| [One local Kanban wake carries its TUI sibling’s notices](mirror-2909-c-local-notice-wake-dedup.md) | 2867 | A waking local subscription and a TUI subscription for the same card/chat no longer intentionally start separate turns for the same delivered event. |
| [Stopped build-owner fixture blocks without a fork](mirror-2909-c-node-lock-fixture.md) | 2868 | Test-only: a stopped-owner build-lock regression waits in a shell builtin read instead of forking sleep repeatedly, so group SIGSTOP cannot strand the fixture shell in an uninterruptible vfork wait. |
| [Gateway launchd jobs use Interactive process type](mirror-2909-c-launchd-interactive.md) | 2869 | The generated macOS gateway plist declares `ProcessType=Interactive`, aligning scheduling intent with the Desktop-facing backend rather than leaving launchd’s default Standard process type. |
| [Parked PR checkpoint restatements preserve the handoff](mirror-2909-c-pr-park-provenance.md) | 2870 | An orderly parked worker can restate a previously published PR in its own checkpoint note without re-arming `active_pr`. |
| [Core-update pauses respect gateway and compression boundaries](mirror-2909-c-update-pause-boundaries.md) | 2871 | Update checkpoints accept a wrapper unit’s actual gateway child, local automation wakes can park when their result contract permits it, and eligible long compressions yield before commit so the pause reaches a safe step boundary. |
| [Core-update pauses respect gateway and compression boundaries](mirror-2909-c-update-pause-boundaries.md) | 2872 | Update checkpoints accept a wrapper unit’s actual gateway child, local automation wakes can park when their result contract permits it, and eligible long compressions yield before commit so the pause reaches a safe step boundary. |
| [Core-update pauses respect gateway and compression boundaries](mirror-2909-c-update-pause-boundaries.md) | 2873 | Update checkpoints accept a wrapper unit’s actual gateway child, local automation wakes can park when their result contract permits it, and eligible long compressions yield before commit so the pause reaches a safe step boundary. |
| [Core-update pauses respect gateway and compression boundaries](mirror-2909-c-update-pause-boundaries.md) | 2874 | Update checkpoints accept a wrapper unit’s actual gateway child, local automation wakes can park when their result contract permits it, and eligible long compressions yield before commit so the pause reaches a safe step boundary. |
| [Core-update pauses respect gateway and compression boundaries](mirror-2909-c-update-pause-boundaries.md) | 2875 | Update checkpoints accept a wrapper unit’s actual gateway child, local automation wakes can park when their result contract permits it, and eligible long compressions yield before commit so the pause reaches a safe step boundary. |
| [Provider-wall reset and profile-scope regression guards](mirror-2909-c-provider-wall-guards.md) | 2876 | Test-only: pin the later of file/live-pool reset deadlines and preserve launch-home credential scope on dispatcher reads, without replacing an already routed turn’s scope. |
| [Native macOS memory tests participate in the required CI gate](mirror-2909-c-native-memory-ci-gate.md) | 2877 | When CI selects the Darwin native-memory lane, “All required checks pass (v2)” requires its success. |
| [Gateway daemon tests verify their own listener and settled receipt](mirror-2909-c-api-daemon-fixtures.md) | 2878 | Test-only: daemon-backed API tests verify that the identified daemon owns its listener before sending requests, and gateway result tests wait for the receipt contract to settle rather than treating a terminal SQL row as a published result. |
| [Update main only as far as a selected first-parent commit](mirror-2909-c-update-up-to.md) | 2879 | `hermes update --yes --up-to <sha>` can apply an already prepared mainline build while newer main commits are still building. |
| [CPU metrics survive sampler gaps and root lease takeover](mirror-2909-c-metrics-stall-recovery.md) | 2880 | While the root CPU gate is on, the anchored metrics sender waits briefly for a fresh sampler window and avoids erasing a recent CPU triple with a CPU-less report. |
| [Warm Linux always-on dashboard rollover and custody](mirror-2909-c-always-on-rollover.md) | 2881 | Linux always-on dashboards use a stable loopback TCP proxy plus warmed blue/green backend units, rather than killing the serving backend before its replacement boots. |
| [Warm Linux always-on dashboard rollover and custody](mirror-2909-c-always-on-rollover.md) | 2882 | Linux always-on dashboards use a stable loopback TCP proxy plus warmed blue/green backend units, rather than killing the serving backend before its replacement boots. |
| [Warm Linux always-on dashboard rollover and custody](mirror-2909-c-always-on-rollover.md) | 2883 | Linux always-on dashboards use a stable loopback TCP proxy plus warmed blue/green backend units, rather than killing the serving backend before its replacement boots. |
| [Warm Linux always-on dashboard rollover and custody](mirror-2909-c-always-on-rollover.md) | 2884 | Linux always-on dashboards use a stable loopback TCP proxy plus warmed blue/green backend units, rather than killing the serving backend before its replacement boots. |
| [Cron owner shutdown survives the drain ending](mirror-2909-c-cron-shutdown-latches.md) | 2885 | An agent cron run stopped by its owner gateway’s restart is reported as a shutdown interruption, not a lost-ownership failure. |
| [One local Kanban wake carries its TUI sibling’s notices](mirror-2909-c-local-notice-wake-dedup.md) | 2886 | A waking local subscription and a TUI subscription for the same card/chat no longer intentionally start separate turns for the same delivered event. |
| [One local Kanban wake carries its TUI sibling’s notices](mirror-2909-c-local-notice-wake-dedup.md) | 2887 | A waking local subscription and a TUI subscription for the same card/chat no longer intentionally start separate turns for the same delivered event. |
| [Deleted launch directories degrade prompt context, not project authority](mirror-2909-c-deleted-cwd.md) | 2888 | A deleted process working directory no longer crashes context discovery or compaction’s system-prompt rebuild. |
| [Deleted launch directories degrade prompt context, not project authority](mirror-2909-c-deleted-cwd.md) | 2889 | A deleted process working directory no longer crashes context discovery or compaction’s system-prompt rebuild. |
| [Terminal delegations close their own child session lineages](mirror-2909-c-delegation-child-lifecycle.md) | 2890 | Recovery of a dead-owner delegation and stall force-finalization close still-open child rows belonging to that unit. |
| [Busy gateway chats admit remote notices to their FIFO](mirror-2909-c-remote-notice-fifo.md) | 2891 | A busy gateway-owned local chat admits remote Kanban notices into its ordered FIFO instead of relying on a Desktop sweep finding it idle. |
| [Busy gateway chats admit remote notices to their FIFO](mirror-2909-c-remote-notice-fifo.md) | 2892 | A busy gateway-owned local chat admits remote Kanban notices into its ordered FIFO instead of relying on a Desktop sweep finding it idle. |
| [Busy gateway chats admit remote notices to their FIFO](mirror-2909-c-remote-notice-fifo.md) | 2893 | A busy gateway-owned local chat admits remote Kanban notices into its ordered FIFO instead of relying on a Desktop sweep finding it idle. |
| [Bounded always-on dashboard boot prewarm gate](mirror-2909-c-boot-prewarm-gate.md) | 2894 | The always-on dashboard waits for its boot prewarms before lifespan startup completes and the socket binds, subject to a shared wait cap. |
| [Bounded always-on dashboard boot prewarm gate](mirror-2909-c-boot-prewarm-gate.md) | 2895 | The always-on dashboard waits for its boot prewarms before lifespan startup completes and the socket binds, subject to a shared wait cap. |
| [Bounded always-on dashboard boot prewarm gate](mirror-2909-c-boot-prewarm-gate.md) | 2896 | The always-on dashboard waits for its boot prewarms before lifespan startup completes and the socket binds, subject to a shared wait cap. |
| [Gateway daemon tests verify their own listener and settled receipt](mirror-2909-c-api-daemon-fixtures.md) | 2897 | Test-only: daemon-backed API tests verify that the identified daemon owns its listener before sending requests, and gateway result tests wait for the receipt contract to settle rather than treating a terminal SQL row as a published result. |
| [Classify origin-ledger sessions once per exact profile](mirror-2909-c-ledger-link-bulk.md) | 2898 | Origin-ledger chat-link projection avoids per-row profile discovery and state.db classification: eligible session references are bulk-classified once per exact origin profile, with no new cross-call cache. |
| [Outcome judging binds an unscoped chat owner](mirror-2909-c-outcome-owner-scope.md) | 2899 | On a multiplexed gateway, outcome refresh/derivation invoked from an unscoped sweeper or handler judges under the owning state.db home’s secret scope. |
| [Parked PR checkpoint restatements preserve the handoff](mirror-2909-c-pr-park-provenance.md) | 2900 | An orderly parked worker can restate a previously published PR in its own checkpoint note without re-arming `active_pr`. |
| [Priority migration validates origin coverage before staging](mirror-2909-c-priority-origins.md) | 2901 | Priority migration checks its explicit origin-to-host map against one canonical capture before consuming backup paths or attachments. |
| [Finite gateway chats wait through update refusals](mirror-2909-c-oneshot-update-wait.md) | 2902 | Finite gateway chats can wait through a core-update owner replacement rather than immediately lose an unadmitted prompt. |
| [Cron owner shutdown survives the drain ending](mirror-2909-c-cron-shutdown-latches.md) | 2903 | An agent cron run stopped by its owner gateway’s restart is reported as a shutdown interruption, not a lost-ownership failure. |
| [Busy gateway chats admit remote notices to their FIFO](mirror-2909-c-remote-notice-fifo.md) | 2904 | A busy gateway-owned local chat admits remote Kanban notices into its ordered FIFO instead of relying on a Desktop sweep finding it idle. |
| [Core-update pauses respect gateway and compression boundaries](mirror-2909-c-update-pause-boundaries.md) | 2905 | Update checkpoints accept a wrapper unit’s actual gateway child, local automation wakes can park when their result contract permits it, and eligible long compressions yield before commit so the pause reaches a safe step boundary. |
| [Core-update pauses respect gateway and compression boundaries](mirror-2909-c-update-pause-boundaries.md) | 2906 | Update checkpoints accept a wrapper unit’s actual gateway child, local automation wakes can park when their result contract permits it, and eligible long compressions yield before commit so the pause reaches a safe step boundary. |
| [Gateway daemon tests verify their own listener and settled receipt](mirror-2909-c-api-daemon-fixtures.md) | 2907 | Test-only: daemon-backed API tests verify that the identified daemon owns its listener before sending requests, and gateway result tests wait for the receipt contract to settle rather than treating a terminal SQL row as a published result. |
| [Executor-dated remote execution observations](execution-observations.md) | 2908 | Remote running cards use executor-dated current-run liveness, portable run identity and time-only expiry. |
| [Executor-dated remote execution observations](execution-observations.md) | 2909 | Read current-run evidence outside memoized link rows so new heartbeat and run rollover remain visible on quiet replicas. |

## Mirror follow-ups 2679–2726

| Feature | Patches | Scope |
|---|---|---|
| [Evidence-based work state observations (execution observations v1)](execution-observations.md) | 2679–2697 | One versioned pure projection maps qualified card, turn-lease, process, subagent and completion-receipt evidence to work states with source-owned time-only expiry and an explicit unknown for legacy producers, exposed as optional `execution_observations` on activity/session-tasks/detail/process/subagent responses and Desktop row state. |
| [One worker queue (`pool:worker`) for portable cards](kanban-worker-pool-queue-2678.md) | 2698 | Follow-up: the carried-input exemption fails closed on a stale machine registry snapshot. |
| [Test stability fix: update fixture git never forks a detached repack](test-stability-2726.md) | 2699 | Test-only: fixture git in the interrupted-pull update test carries the updater's no-detach flags so a background repack can't race the next clone. |
| [Configurable errors/gateway/gui log rotation; INFO trace for dropped process completions](log-rotation-config-2726.md) | 2700, 2701 | `logging.<stem>_max_size_mb` and `_backup_count` size errors/gateway/gui logs, and dropped process completions now leave one INFO line; 2701 is a behavior-neutral refactor of the unowned-event drop path. |
| [Offline SQLite file access reserves a path instead of holding the registry lock](sqlite-offline-path-reservations-2726.md) | 2702–2707 | Offline file access reserves only its target path (with -wal/-shm) instead of holding the registry lock, unpredictable URIs and openers fail closed, and the state.db writer names its target. |
| [Evidence-checked hub admission judge](kanban-hub-admission-2678.md) | 2708, 2720, 2721 | Follow-ups: blocked decision cards skip the judge, the hub judges a foreign hub-bound card at pickup (dispatch refuses an unjudged copy), and create reports a denied card's pool placement. |
| [Pickup-stall detector: hold windows and time pinned to this machine](kanban-pickup-stall-hold-window-2678.md) | 2709 | Follow-up: a pickup-stall alarm's run may re-place the cards it lists off the stalled machine. |
| [Owner sync repair never reopens a card over its ended run](kanban-sync-heal-ended-run-2678.md) | 2710–2712 | Follow-ups: the last lifecycle move decides the park (bad rows skipped, one warning), a park lands where its own event recorded it, and assignment routing records the todo it lands. |
| [A Board-restored Kanban card gets its hub delivery subscriptions back](kanban-board-restore-subs-2726.md) | 2713, 2714 | Sync apply records a card it restores from the Board and rebuilds its hub delivery subscriptions from the card's chat links, with the newest restore time winning. |
| [skill_manage batch: a nameless op inherits the one skill its siblings name](skill-manage-batch-name-inherit-2726.md) | 2715 | A batch op with no `name` inherits the single skill its sibling ops name, is flagged `name_inherited`, and is staged with the resolved name. |
| [Kanban quota episodes and clock-held stuck warning](kanban-quota-episodes.md) | 2716, 2717 | Follow-ups: a wall another gateway recorded holds only while this gateway's own pool has no room, and a lifted wall still checks the base route walled elsewhere. |
| [Hub metrics reports on an anchored sender thread](kanban-hub-metrics-sender-thread-2726.md) | 2718 | Hub CPU/metrics reports go out from an anchored `metrics-sender` thread with bounded sends, independent of sync round length. |
| [Respawn guard: supported reopen reaches dispatch](kanban-respawn-guard-hold.md) | 2719 | Follow-up: a re-review linked after the owner's checkpoint that approves the PR's current head releases `active_pr` once. |
| [kanban_complete refuses a ship review card's completion that ship cannot read](kanban-ship-review-completion-gate-2726.md) | 2722 | On a card that states the ship review contract, a review completion without exact verdict, `reviewed_sha` and `reviewed_pr` is refused as `review_contract` so the reviewer can fix it in the same run. |
| [Terminal refusals say nothing ran; scratch hint says it is shared](terminal-refusal-wording-2726.md) | 2723 | Foreground-command refusals state that nothing in the command ran, and the scratch-dir prompt hint says the directory is shared across sessions. |
| [A cut-short rg diagnostic no longer swallows the first search match](search-rg-diagnostic-first-match-2726.md) | 2724 | The native search lane drops an unterminated stderr tail when `rg` was killed at the result bound, so the first match is no longer lost. |
| [Pool 401 revoked: adopt pushed replacements; a recoverable 401 requeues instead of failing](pool-401-revoked-adopt-2726.md) | 2725 | A 401 on a pooled account adopts a pushed replacement from `auth.json`, and a 401 whose siblings are only quota-limited or re-minted exits 75 (requeue) instead of 78. |
| [Kanban wakes queued behind a busy turn fold into one turn](kanban-wake-fold-2678.md) | 2726 | Follow-up: any queued wake is relabelled from live card state when the drain claims it. |

## Mirror follow-ups 2564–2678

| Feature | Patches | Scope |
|---|---|---|
| [Orphan FTS shadow repair re-probes under the write lock](fts-orphan-shadow-write-lock-2678.md) | 2564 | Orphan FTS5 shadow tables are re-probed and dropped under one `BEGIN IMMEDIATE`, so concurrent openers of a fresh `state.db` no longer drop a live family. |
| [Kanban DB recovery: stale-index lookups key on inserted rows](kanban-db-recover-stale-index-2678.md) | 2565, 2588 | Recovery point-looks-up every listed rowid missing from the rebuilt table, so a garbage scan row can't hide a stale index entry; 2565 is a test-helper fix that damages the whole leaf page. |
| [Split per-PR review resumes a parked owner](kanban-split-review-resume-2678.md) | 2566, 2567, 2649 | A multi-PR review park resumes its owner when per-PR approvals or change requests from checkpoint-named parents together cover every holding PR. |
| [Desktop named in a card body no longer pins the card to its origin](kanban-placement-desktop-surface-2678.md) | 2568–2571 | Placement pins on Desktop-surface phrases only from the card title, while a body that tells the worker to operate the live app still pins; 2571 is test-only. |
| [Machine metrics: tokens and sessions throughput counters](machine-throughput-metrics-2678.md) | 2572 | Machine metrics report four cumulative token and session counters summed over every `state.db`, with a 60 s cache and a retry/back-off for Boards that reject them. |
| [Test stability fixes: resize redraw, pack sprawl, boards poll](test-stability-2678.md) | 2573, 2622, 2629, 2644 | Test-only deflakes for the resize-back redraw test (two races), the pack-sprawl fixture and the boards-poll inode assertion. |
| [Desktop sidebar: one batched primary read for a concrete local profile](desktop-sidebar-batched-read-2678.md) | 2574 | The Desktop sidebar makes one batched primary read for a concrete local profile, with owner tagging and a per-request fallback to the three-slice path. |
| [One user row per hold-queued prompt.submit](tui-hold-queued-submit-row-2678.md) | 2575, 2576 | A prompt queued behind the core-update hold is adopted in place as the turn's single user row, except across a session rotation. |
| [Headless wake turns release their writer lease and are reaped after a grace](headless-wake-lease-reap-2678.md) | 2577 | A headless wake turn's session gets a grace timer, then is closed and its active-session writer lease released unless live background work protects it. |
| [Kanban read-path performance: memoised reads, read-only probes, incremental refresh](kanban-read-path-perf-2678.md) | 2578, 2581–2584, 2589, 2592, 2593, 2595, 2656, 2657 | Eleven independent read-path optimizations (cached board reads, incremental replica and board refresh, read-only notice probes, activity-key and links reuse, `GET /boards` counts reuse, a list-function move) that cut lock hold and CPU without changing responses. |
| [A pulled comment keeps its sending gateway](kanban-comment-origin-gateway-2678.md) | 2579, 2580 | Comments pulled through the Board feed (and worker-node imports) store their sending gateway so `wait-comment --from-gateway` can match them; 2580 is test-only. |
| [One worker queue (`pool:worker`) for portable cards](kanban-worker-pool-queue-2678.md) | 2585–2587, 2636, 2637, 2662, 2678 | Portable cards queue for any pool worker instead of one machine, covering park pinning of unpushed state, carried inputs under pool routing, eligible-member attestation and direct `create_task` queuing. |
| [Rotation-mode history-refusal retry sends the current message once](history-refusal-compact-retry-2563.md) | 2590 | Under legacy rotation-mode compression, adopting the durable parent drops the verbatim tail's persisted copy, and the history-refusal retry ends with the current message exactly once. |
| [Review independence reads the model that ran; repeated review refusals block visibly](kanban-review-independence-ran-model-2678.md) | 2591 | The review model is chosen distinct from the model the implementer actually ran, and a third identical review refusal trips the card to a triage block. |
| [Batch clarify results name why they were cancelled](clarify-batch-cancel-reason-2678.md) | 2594 | Batch clarify results that did not end in `submitted` carry a `reason` label (interrupted, taken_over, shutdown, session_closed, timeout, client_empty, client_error, no_session) in both registries and the stored tool result. |
| [Owner sync repair never reopens a card over its ended run](kanban-sync-heal-ended-run-2678.md) | 2596, 2625 | The divergence repair no longer re-sends a stale live copy over an ended run, and a new heal moves a `running` card with an ended run to done, its original park, or todo, once per run. |
| [Kanban wakes queued behind a busy turn fold into one turn](kanban-wake-fold-2678.md) | 2597–2600, 2655 | Kanban wakes waiting at the FIFO tail fold into one batch rebuilt from live card state, with a refused fold (>200 cards) queuing alone and relabel reads resolving pin-first. |
| [Wait-notice slot counts per board and unused-grant ready clocks](kanban-wait-notice-counts-2678.md) | 2601, 2635 | Wait-notice running counts are per connection board, read-only, unknown on failure and exclude pool-leased cards; unused Board grants no longer reset the ready clock. |
| [Core-update requeue and unused Board grants no longer spend a PR owner's handoff](kanban-core-update-requeue-holds-2678.md) | 2602, 2603, 2676 | A restart-safe core-update requeue or an unused Board grant no longer consumes a PR owner's handoff, review continuation or hold release. |
| [Evidence-checked hub admission judge](kanban-hub-admission-2678.md) | 2604–2620, 2646–2648 | Hub-bound card placements (create, pin, move, courier landing, capability owner) need an evidence-quoting, fail-closed model judge, with denied cards routed to the worker pool, plus a no-model-call deny rule for comment-relay cards. |
| [Hub CPU sampler keeps rolling 1 s snapshots so every reported triple fits the Board's window](hub-cpu-advisory-sampler-thread-2563.md) | 2621 | The advisory sampler reads one `/proc/stat` snapshot per second and a report carries a triple only when it ended 0–1500 ms before the take, instead of one sample every 5 s attached up to 10 s old. |
| [Ghost expiry keeps a remote chat link until the owner verifies the completion](kanban-ghost-expiry-remote-link-2678.md) | 2623 | A retained remote chat link expires as a ghost only when the owner's exact-profile row is attested terminal and verified; open or unverified rows keep it. |
| [Pickup-stall detector: hold windows and time pinned to this machine](kanban-pickup-stall-hold-window-2678.md) | 2624, 2639 | Holds inside a stated release window (or a core rollout hold) file no "Pickup held" card, and the stall clock starts when the card was pinned to this machine. |
| [Dispatcher skip reasons on ready cards](kanban-dispatch-skip-reasons-2678.md) | 2626, 2660 | Records a durable `dispatch_skipped` reason per ready episode, holds fresh pins on a live quota wall, and names the reason in ready-wait notices instead of priority advice. |
| [Fresh pinned card on a live quota wall reroutes in class; storm alert fixes](kanban-quota-wall-pin-reroute-2678.md) | 2627, 2663 | A fresh pin on a live quota wall reroutes to a same-class peer on its first run; `require_model: true` becomes a deliberate marker, and the storm alert counts per host with UTC wall times. |
| [Kanban failed-result observer: bounded restarts, retire on deterministic failure, tempdir released](kanban-failed-result-observer-restarts-2678.md) | 2628, 2643 | The failed-result observer exits 78 on import or context failure so systemd retires it, other crashes stop at a 10-per-300 s start limit, and the ready tempdir is released on retire and after every stop. |
| [Operator `kanban resume` and portable review evidence for the active_pr guard](kanban-operator-resume-2678.md) | 2630–2632 | Adds `hermes kanban resume <id> --reason` and lets an owner's authenticated review park release the guard from Board-replica review evidence. |
| [Kanban wake_check stored verbatim; redactor keeps identifier keys](kanban-probe-verbatim-redactor-keys-2678.md) | 2633, 2634 | wake_check probes are stored byte-identical and a probe redaction would change is refused as `wake_check_secret`; the redactor no longer masks idempotency/cache/sort/partition/dedupe key names, while `primary_key` stays masked. |
| [Refuse a move made for "ship custody"](kanban-move-ship-custody-2678.md) | 2638 | A move whose reason claims ship or landing custody is refused with `ship_custody_portable` unless `host_reason` is given. |
| [Core update: upstream sync before the pause, and cron-turn accounting at checkpoints](update-core-pause-fixes-2678.md) | 2640, 2661 | `hermes update` fetches upstream and builds the fork merge before pausing gateways, and a `cron_cut` checkpoint no longer refuses because an agent cron job's turn is counted twice. |
| [Placed create: keep the idempotency key, report a rejection](kanban-placed-create-results-2678.md) | 2641, 2642 | A placed create keeps the creator's idempotency key and refuses a conflict with `origin_idempotency_conflict`; a rejected create reports `created: false` instead of "IS created". |
| [Phantom-ref checks consult the host's Board replica](kanban-phantom-ref-replica-2678.md) | 2645 | The `created_cards` completion gate and the summary phantom-id scan look ids up read-only in every Board replica before calling an id phantom. |
| [A headless wake leaves a turn paused for an update to the restarting backend](headless-wake-paused-turn-2678.md) | 2650–2653 | A headless wake returns `busy` instead of resuming an unloaded chat whose turn a core update just paused, and the paused-turn lookup skips invalid profile directories. |
| [Session list filter: plain columns first, indexed pinned back-fill](session-list-filter-clause-order-2678.md) | 2654 | Session list filters test plain columns before the JSON lineage clauses, and a new partial index `idx_sessions_pinned` serves the pinned back-fill. |
| [Any worker can reroute a ready card stranded on impossible runs_on](kanban-stranded-reroute-2678.md) | 2658, 2659 | Any scoped worker, or the card's creator, can reroute a ready unclaimed card whose `runs_on` no enrolled machine carries; the diagnostic and asks name that route, and ask lint refuses board-edit asks. |
| [Gateway tick: read once per pass, not once per card](gateway-tick-read-budget-2678.md) | 2664, 2665 | Divergence repair, TUI owner claim, wait-notice posting and `gateway_names` read once per pass or tick instead of once per card, with repair holding one replica chunk at a time. |
| [Queued Kanban block notice: re-read the card before delivering its ask](kanban-stale-block-ask-refresh-2678.md) | 2666–2675 | A queued block wake re-reads its card at delivery and replaces a stale "Next action for the user" ask with a "Since then:" line, failing closed on anything it can't verify. |
| [Another gateway's Desktop local-hash chat owns the cards its gateway links to it](kanban-desktop-owner-provenance-2409a.md) | 2677 | A foreign gateway's Desktop `local-<sha256>` chat counts as an owner only with an unreleased `created` chat link to that exact gateway/profile/session on the card or an ancestor; dependency children inherit its sub, and the chat's own gateway releases a hub-written link to its worker hash. |

## Mirror follow-ups 2492–2563

| Feature | Patches | Scope |
|---|---|---|
| [Plugin activation reaches every chat-hosting serve backend](plugin-activation-every-backend-2563.md) | 2492 | Every serve backend publishes a per-process discovery entry so plugin activation reaches backends that lost the host role record. |
| [Tool calls and tool lists wait out a plugin force-reload](tool-registry-reload-barrier-2563.md) | 2493, 2531 | A reload barrier makes tool_call, registry dispatch and tool-list builds wait (bounded, never on an event loop) for a plugin force-reload instead of seeing a half-empty registry. |
| [Git-workspace custody at Kanban handoff](kanban-workspace-publication-custody-2563.md) | 2494–2496 | Voluntary handoffs of a running card require published Git state or an explicit retention reason, with server-derived custody receipts that survive checkpoint rollover and are released by publication. |
| [Re-park escalation: natural waits and the probe fire cap](kanban-repark-fire-cap-2563.md) | 2497, 2528, 2538 | A re-park after the same wake probe fired no longer escalates, a six-fire cap escalates a broken probe, the worker-facing message names the fire count, and escalation owners avoid profiles no pool worker can run. |
| [Placement forwarder: a rejected hub request is terminal](kanban-placement-rejected-terminal-2563.md) | 2498 | A hub request the target rejected is never clock-parked: forwards send `distinct_from`, a self-overlap is re-forwarded once, a real overlap blocks the card, and any other rejection runs it locally. |
| [tool_call recovers a tool name placed inside an entry's arguments](tool-call-entry-name-recovery-2563.md) | 2499, 2500 | Lifts a tool name nested in or beside an entry's arguments when unambiguous, and gives an actionable error for nameless entries. |
| [Failed-result observer launches with a pinned import path](kanban-failed-result-observer-import-2563.md) | 2501 | The failed-result observer's systemd and subprocess launches pin the checkout and dependency site-packages on `PYTHONPATH`, ending the missing-dependency crash loop. |
| [Cross-board ranking of shared hub worker slots](kanban-cross-board-hub-slots-2563.md) | 2502, 2503 | Ranks ready cards across boards under the shared host slot cap with a 2-hour starvation guard, and counts other boards' cards in the queue line. |
| [Session writes ride out owner-discovery stalls; Desktop Retry](desktop-session-write-retry-2563.md) | 2504, 2505 | Re-reads a missed owner-discovery deadline within a 6 s budget, adds Desktop Retry for archive and rename with a pinned owner and fenced rename, and shares the tile-close helper. |
| [Pickup-stall alarms never pin to the stalled machine](kanban-pickup-stall-alarm-placement-2563.md) | 2506 | Pickup-stall alarms are placed on another eligible machine or the hub lane, stranded open alarms are re-pinned, and the body carries the stalled machine's evidence. |
| [Hub CPU advisory sampled on its own thread, with a stale-advisory card](hub-cpu-advisory-sampler-thread-2563.md) | 2507 | Moves the hub CPU advisory sampling onto a dedicated thread so slow sync rounds can't void it, and files one card per stale-advisory incident. |
| [macOS state gate delegates to a superseding duplicate run](macos-ci-superseded-gate-2563.md) | 2508–2511 | The required macOS state CI gate passes a cancelled selected lane on non-release pull-request runs when a newer duplicate run carries the verdict, with the needed Actions read permissions. |
| [Priority migration tolerates churn and coordinates all origins](kanban-priority-migration-churn-2563.md) | 2512, 2513 | Adds the one-command `migrate` coordinator and `publish`, tolerates done and arrived cards during migration, and restores every attempted origin on any apply exception. |
| [Placement coordination ignores possessive prose](kanban-coordination-possessive-prose-2563.md) | 2514 | Stops possessive prose such as `cross_task's` from being parsed as a coordination invocation. |
| [Unloaded sweep re-renders only undelivered events of a claimed range](kanban-unloaded-partial-claim-rerender-2563.md) | 2515 | Drops already-delivered events from a claimed unloaded-sweep range, and stops one overflowing record from aborting the whole flush. |
| [A pinned route's quota wall shows its reset and Choose a model](desktop-pinned-route-quota-wall-2563.md) | 2516 | A pre-turn `route_refused` carries the backend's sentence as its card body, with a Choose a model action and a reopened-chat boundary copy. |
| [Review resume reads a parent run from the Board replica](kanban-review-resume-replica-parent-2563.md) | 2517 | Lets an exact-head review approval release the owner when the review parent's run exists only in the fresh Board replica. |
| [Recommendation nudge on needs-input blocks](recommendation-nudge.md) | 2518–2523 | After a successful `kanban_block` with an explicit `(recommended)` choice, adds a conditional, non-granting advisory `warning` (with a boundary/named-person veto and a code-signal gate), plus worker prompt guidance. |
| [Placement diagnostics explain structural rejection at create](kanban-placement-create-diagnostics-2563.md) | 2524, 2525 | Reports why a card was not enrolled for worker-first placement, in tool and CLI create output, without changing placement decisions. |
| [Session list responses are JSON-encoded in the pool thread](web-session-list-pool-encoding-2563.md) | 2526, 2527 | Session list, sidebar and project tree responses are encoded in the worker thread, with a distinct Response object per coalesced request. |
| [Review model falls back when this host's registry refuses it](kanban-review-model-registry-fallback-2563.md) | 2529 | A handoff `review_model` the host's routing registry refuses falls back to a registered reviewer distinct from the implementer, or refuses admission before spawn. |
| [Decomposer assigns enrolled cards only to runnable profiles](kanban-decomposer-runnable-assignee-2563.md) | 2530 | The auto-decomposer keeps an enrolled worker-first card off profiles no pool worker runs, and marks them children-only in the roster prompt. |
| [TUI notice owner reads the launch authority as a property](kanban-tui-owner-launch-property-2563.md) | 2532 | Fixes the TUI notice owner calling the `SessionAuthorities.launch` property, which stopped registry-backed gateways claiming TUI notices. |
| [Scoped operator call on one foreign card](kanban-operator-call-2563.md) | 2533, 2534 | A `kanban_supervisor_request` operator op lets a worker comment on, move or unblock one foreign card via its home gateway, with provenance, rate limits and a call log. |
| [Capacity advisory test pins](test-stability-2563.md) | 2535 | Test-only: four mutation-killing tests for capacity evidence and metrics. |
| [Worker pickup keeps a copy the local dispatcher holds](kanban-pickup-held-copy-2563.md) | 2536 | `evict()` keeps a ready/review copy with a recorded, still-confirmed respawn-guard hold, so pickup stops re-importing it and spending the budget every tick. |
| [ci-flaky-cards: only persistent unavailability turns the cron red](ci-flaky-cards-persistent-unavailability-2563.md) | 2537 | The flaky-card poller treats superseded cancelled runs and carried-over re-run jobs as non-failures, retries fresh missing artifacts, and exits 1 only after unreadable data persists across two polls past a grace window. |
| [A refusal caused by earlier history compacts once and retries](history-refusal-compact-retry-2563.md) | 2539, 2540 | A first-call safety refusal triggered by earlier history compacts it once and resends the same message, keeping the caller's system prompt. |
| [pm client reports a transport failure when its worker dies before the request](pm-worker-early-death-transport-2563.md) | 2541 | A broken pipe from a worker that exited before the request now yields the worker-exited `InstallError` instead of a raw `BrokenPipeError`. |
| [Opted-in pre-swap stop: update over busy gateways within one deadline](update-preswap-busy-stop-2563.md) | 2542–2553 | An opt-in Linux-only `hermes update` pre-swap stop drains and stops busy gateways within one fail-closed 30 s deadline backed by strict gateway discovery. |
| [One failure per run: Board release, deferred neutral effects, one final notice](kanban-failure-once.md) | 2554–2563 | A failed run is finalized once per run through a Board release, with neutral effects deferred or retired when the lease alarm wins, one canonical final notice, and failed-result diagnostics carried on the release. |

## Mirror follow-ups 2410–2491

| Feature | Patches | Scope |
|---|---|---|
| [Failed worker-result reconciliation](kanban-failed-result-reconciliation-2491.md) | 2410–2430 | Close runs whose worker recorded a failed result and then died, via a board-only death proof, a pre-armed independent observer, causal failure receipts, atomic crash accounting and verified runtime termination, even when the dispatcher is stalled. |
| [Scoped stale linked-card projections](kanban-stale-link-scopes-2491.md) | 2431 | Warm stale_ok linked-card reads are served from a bounded scope cache without blocking; failed cold or ledger-fallback reads raise instead of returning empty. |
| [Test-suite deflakes and hardening](test-stability-2491.md) | 2432, 2434, 2441, 2442, 2443, 2449, 2450, 2477 | Test-only fixes for races, leaked threads and surviving mutants. |
| [Person-facing browser launches use a clean agent environment](clean-agent-env-person-browsers-2491.md) | 2433 | Remaining in-process webbrowser.open callers open through top_level_env helpers so the browser does not inherit agent markers. |
| [Unread Kanban cards never derive a chat outcome of done](outcome-unread-kanban-cards-2491.md) | 2435–2438 | A failed Kanban card read yields unresolved/card_read_failed and cannot be overridden by a declared done. |
| [Worker pickup: local ordering and full event history](kanban-worker-pickup-order-history-2491.md) | 2439, 2459, 2478 | Pickup budget follows the executor's local priority graph, copies full task_events history, and refills truncated older copies (`hermes kanban sync refill-history`). |
| [Internal link endpoint validation](kanban-link-endpoint-validation-2491.md) | 2440 | Internal task-graph link writes refuse unknown parent or child ids inside the caller's transaction. |
| [Dependency evidence preserved across checkpointed queued moves](kanban-move-parent-evidence-2491.md) | 2444, 2445 | A queued move forwards its checkpoint and mirrors foreign parent run results so dependency and review checks still work after the move. |
| [Worker attachment bytes through verified Board storage](kanban-attachment-board-storage-2491.md) | 2446–2448 | Attachment bytes move through Board storage with digest verification, retries and an absolute transfer deadline. |
| [Premise-probe warning for human asks](kanban-ask-premise-probe-warning-2491.md) | 2451, 2452 | A human block without resolved_check returns an advisory warning when the ask names a checkable premise; 2452 adds a public contributor attribution file. |
| [Bounded replica reads and GIL budget in serve](serve-replica-read-gil-budget-2491.md) | 2453 | Replica task scans use one aggregated SQLite result, and serve and the dashboard apply the gateway's GIL switch interval. |
| [Host manager-loss waiver for owner-death repeats](kanban-host-manager-loss-waiver-2491.md) | 2454, 2461 | A root-attested host manager SIGKILL waives one owner_death_repeat charge per card; the reader fails closed on bad receipts. |
| [Metadata-only ask amendments no longer re-send a blocked notice](kanban-ask-amendment-notice-2491.md) | 2455 | A metadata-only ask amendment is silent for the editing chat and shows other subscribers one "ask updated" line. |
| [CLI create judges placement on the resolved assignee](kanban-cli-create-resolved-assignee-2491.md) | 2456 | `hermes kanban create` passes the resolved assignee into placement policy and audit. |
| [Recreate dedicated macOS CI checkouts at job start](macos-ci-checkout-recreate-2491.md) | 2457 | The macOS state workflow recreates the runner checkout behind safety checks and bounds its retained size. |
| [Notify-subscribe caller resolution](kanban-notify-subscribe-target-2491.md) | 2458 | notify-subscribe resolves the caller's stored chat before warning about a different chat. |
| [Renamed GitHub repo aliases in respawn-guard PR matching](kanban-pr-repo-aliases-2491.md) | 2460 | PR URLs of a renamed repo canonicalize to one pull for review resume, hold release and the PR-state cache. |
| [Pinned-machine core-update drain notes](kanban-pinned-machine-drain-notes-2491.md) | 2462, 2463 | Create, queue and chat-wait notes say a pinned target machine is draining for a core update instead of promising a start. |
| [Skip unchanged partition-repair scans](kanban-sync-partition-repair-skip-2491.md) | 2464, 2465 | A file-fenced, cursor-eligible memo skips unchanged partition repair scans in the sync round. |
| [Re-adopted Desktop jobs: completion wake and late-orphan retry](process-readopt-desktop-wake-2491.md) | 2466–2468 | Re-adopted file-backed Desktop jobs deliver one completion through the Desktop wake sink; adoption retries after an overlapping backend exits. |
| [Darwin fd scanner for the state lock guard](lockguard-darwin-fd-scan-2491.md) | 2469–2471 | A verified Darwin descriptor scanner for the WAL lock guard, with learned dev_t/NODEV conversion and directory-scan fallback. |
| [Native gateway turns park at durable core-update boundaries](gateway-native-update-parking-2491.md) | 2472, 2473 | Eligible gateway turns stop at a completed-tool boundary during an update pause and resume as one authorized event afterwards. |
| [Desktop boot-snapshot rows expire once their owner answers](desktop-boot-snapshot-expiry-2491.md) | 2474 | The sidebar drops an owner's launch-time boot rows once that owner answers a live session list. |
| [Gateway delivers legacy tui Kanban notices through the local FIFO](kanban-tui-legacy-notice-fifo-2491.md) | 2475 | The gateway claims legacy Desktop tui subscriptions for chats it owns and delivers them as FIFO Kanban wakes. |
| [Take over waits out a live but slow writer](session-takeover-slow-writer-2491.md) | 2476 | session.take_over waits for a provably live local writer, and Desktop can resend a prompt the server definitely refused. |
| [Late remote Kanban notices are batched](kanban-remote-late-notice-batch-2491.md) | 2479–2481 | Late remote notices are grouped per chat with all-keys custody checks and preserved withdrawal ledger state. |
| [Profile stop uses the process protocol for retained managed handles](gateway-managed-handle-stop-2491.md) | 2482 | A managed-worker handle retained only by adoption gets the worker's own stop control instead of an in-process interrupt. |
| [foreign_card refusal names the hub-comment route](kanban-foreign-card-hub-comment-2491.md) | 2483, 2484 | The refusal for hub-owned cards names the comment-only helper when it is resolvable on the host. |
| [Kanban capacity evidence and execution observations v1](kanban-capacity-evidence.md) | 2485–2491 | Advisory v1 explanations of why ready cards are not running while capacity looks unused, plus receipt-based ready→claim→start metrics; admission unchanged. |

## Mirror follow-ups 2254–2409

| Feature | Patches | Scope |
|---|---|---|
| [Regression fixture isolation and deterministic cleanup](regression-fixture-isolation-2409a.md) | 2254 2262 2273 2284 2285 2326 2328 | Conformance test packages refuse bare unittest discovery, preserving the test runner's isolation contract without changing plugin registration. |
| [Upstream sync landing PR lookup and runner evidence](upstream-sync-landing-evidence-2409a.md) | 2255 2270 2272 | Reuse an open main-target landing PR by head ref even when the origin slug's owner is stale after a repository transfer. |
| [Gateway refusal detail and launchd test fence](gateway-refusal-and-launchd-test-fence-2409a.md) | 2256 2300 2301 | A restart-gap timeout preserves the original service_identity_unverified refusal and its diagnostic detail. |
| [Kanban coordination portability and fresh capability evidence](kanban-coordination-attestation-2409a.md) | 2257 2258 2259 2260 2261 | Coordination reads can be worker-portable only when every configured destination has fresh operator capability attestations and is active, unheld and pickup-capable. |
| [Update pause publication respects a late user Stop](update-pause-publication-2409a.md) | 2263 2264 | Decide whether an update pause still stands at terminal message publication, after payload construction can yield to a user Stop. |
| [Native Darwin memory metrics without periodic subprocesses](darwin-memory-no-fork-2409a.md) | 2265 2266 2267 | Read fresh Darwin memory counters through libSystem rather than spawning periodic vm_stat/sysctl subprocesses from the gateway. |
| [Kanban tool blocks retain agent caller attribution](kanban-tool-block-actor-2409a.md) | 2268 | Store and render tool-originated block notices as agent actions, even when no caller identity is available. |
| [Kanban transitive, bounded and capacity-aware priority](kanban-bounded-priority-2409a.md) | 2269 2286 2329 2330 | Compute effective priority from the maximum active downstream waiter across the dependency chain without rewriting stored parent priorities; terminal intermediates stop inheritance. |
| [Held-machine pins and lossless courier placement](kanban-held-pin-courier-2409a.md) | 2274 2275 | Refuse creation pins naming a remote pickup machine on Board hold, with created=false, machine_held and unheld alternatives. |
| [Authoritative creation-time placement audit](kanban-creation-placement-audit-2409a.md) | 2276 2287 | Every created event snapshots the final insertion-time placement_policy, including an explicit null, after caller diagnostic fields are expanded. |
| [Metadata-only flaky-test Actions artifacts](flaky-metadata-artifacts-2409a.md) | 2277 2298 | Export canonical pass-on-retry test identities and bounded run provenance, not raw failure output, parameter values or logs. |
| [SQLite plain-file open guard and forensic copy fence](sqlite-plain-open-guard-2409a.md) | 2278 2279 2280 | Add scripts/check --only sqlite-sidecars and lint wiring to reject unmarked production plain-file calls on recognizable SQLite paths. |
| [Bounded live Kanban notice custody](kanban-notice-custody-bound-2409a.md) | 2281 2282 2283 | Bound undelivered live-process notice custody independently of whether chat admission is open. |
| [Disable and re-enable credential accounts](auth-disable-credentials-2409a.md) | 2288 2289 2290 2291 2292 2293 2294 2295 2296 2297 | hermes auth disable PROVIDER TARGET retains a credential but excludes it from use; enable reverses the active-store disable. Targets are an index, entry ID or exact label. |
| [Remote notice routing projection reuse](remote-notice-routing-projection-2409a.md) | 2299 | Reuse per-row routing projections across unrelated replica changes and share them across chat polls. |
| [SessionDB path normalization and quarantine locking](sessiondb-path-and-quarantine-2409a.md) | 2302 2303 | Accept string and os.PathLike filesystem inputs consistently by normalizing to Path before SessionDB initialization and isolation checks. |
| [Scoped Desktop creation provenance for Kanban ownership](kanban-desktop-owner-provenance-2409a.md) | 2304 | Retain interactive Desktop/CLI/TUI/ACP/GUI owners whose local transport IDs resemble headless receipts. |
| [Board wire compatibility, field custody and stall diagnostics](board-wire-compatibility-custody-2409a.md) | 2305 2306 2307 2308 2309 2310 2311 2312 2313 2314 2315 2316 2317 2318 | Guard exported Board wire columns and skip reconciliation chat links whose target card exists neither locally nor on the service. |
| [Desktop alphabetical projects and row Archive placement](desktop-sidebar-project-policy-2409a.md) | 2319 2320 2321 | Put the hover Archive button at the row's right edge, after the session-actions kebab. |
| [Pickup-stall evidence grace and grouped target refresh](kanban-pickup-evidence-grace-2409a.md) | 2322 2323 | Do not fabricate a pickup refusal when the detector runs before pickup has recorded its first skip after a drain. |
| [Retain shell initialization policy on config read failures](shell-init-config-read-retention-2409a.md) | 2324 2325 | A transient config read failure retains the last successfully resolved shell-init files and auto_source_bashrc setting for that exact config path. |
| [Fresh-card routing around known provider walls](kanban-first-spawn-provider-wall-2409a.md) | 2327 | Before a fresh unpinned card's first spawn, try an eligible alternate through the assignee's routing class when its base provider already has an active known wall. |
| [Preserve an upstream contributor author alias](contributor-author-alias-2409a.md) | 2271 | Map the preserved upstream tile-pin author's commit email to the contributor handle used by the repository's contributor tooling. |
| [Kanban priority bounds and guarded global normalization](kanban-priority-bounds-and-global-migration-2409b.md) | 2331, 2389–2396 | Reopen bounds, mixed-origin priority plan/stage/apply/restore/verify CLI, feed-validated capture. |
| [Test deflakes and small refactors in the 2331–2409 range](test-stability-and-refactors-2409b.md) | 2332, 2355–2359, 2362–2363, 2376–2377, 2399–2400, 2402 | Test-only hardening plus one value-helper extraction and one Termux cleanup fix. |
| [Desktop instant archive with soft-hide](desktop-instant-archive-2409b.md) | 2333–2334 | Archiving hides the sidebar row immediately and restores it on cancel/failure without cross-owner or pin regressions. |
| [Qualified historical terminal carry recovery](kanban-terminal-carry-recovery-2409b.md) | 2335–2339 | Operator API that releases a retained shepherd obligation from hash-pinned owner-database completion evidence. |
| [Desktop Kanban status meter and blocked-owner sidebar reason](desktop-kanban-status-signals-2409b.md) | 2340–2341, 2404 | Status-bar bar of open cards by state; sidebar red reason names blocked owners. |
| [Guarded instant-update checkpoints](guarded-update-checkpoint-2409b.md) | 2342–2347, 2352–2353 | Update pause negotiates a bounded checkpoint with the gateway for known managed work, and recognizes fence-parked launchd launchers. |
| [Advertised core-update drain holds expire while the writer lives](core-update-drain-hold-expiry-2409b.md) | 2348–2349 | A drain hold lease expires on time even if its writer process is alive. |
| [Creator-chat backfill reuses one guarded replica reader](kanban-creator-backfill-replica-reader-2409b.md) | 2350 | Backfill opens the Board replica at most once per pass instead of once per card. |
| [Hourly CI review comments and flaky-poll deadline](ci-review-batch-and-flaky-budget-2409b.md) | 2351, 2381–2385 | Review comments are batched hourly; the flaky Kanban poller checkpoints and stops at a deadline. |
| [Compact routed requests before context attestations reject them](route-context-overflow-compaction-2409b.md) | 2354 | Request-size route refusals trigger compaction, using the route's attested budget. |
| [Bounded async delegation orphan replay and legacy local completion routes](delegation-orphan-replay-and-legacy-local-routes-2409b.md) | 2360–2361 | Dead-owner completions keep their offer leases; raw legacy Desktop keys resolve through the owning authority. |
| [Dashboard comment writer provenance for local agent processes](kanban-http-comment-provenance-2409b.md) | 2364–2365, 2386–2387 | Bare dashboard comments from a local agent process are recorded as agent, not operator. |
| [Archive complete scratch trees before guarded garbage collection](kanban-scratch-archive-gc-2409b.md) | 2366–2368 | Scratch workspaces are archived with verified, fsynced payloads before removal. |
| [Root-disk capacity fence for new worker card claims](kanban-disk-admission-2409b.md) | 2369 | Opt-in brake that refuses new Kanban claims when installation-root free space is low. |
| [Attested remote-access placement for ssh and hub-read cards](kanban-declared-remote-access-placement-2409b.md) | 2370–2375, 2379 | Cards declaring remote access are placed only on workers that attest it. |
| [Cross-gateway priority donation preserved after pickup](kanban-pickup-priority-donation-2409b.md) | 2378 | Dispatch ranks with priorities donated by remote waiters. |
| [Dependency handoffs across unused grants and evicted history](kanban-dependency-handoff-history-2409b.md) | 2380, 2401 | Unused Board grants don't consume dependency handoffs; evicted parents need verified history. |
| [Class vision scan ignores tool schemas; non-retryable local errors log a traceback](routing-vision-scan-tool-schema-2409b.md) | 2388 | Fixes a crash on every turn of class-routed chats whose tool schemas have a property named type. |
| [Agent clients trust an exported SSL_CERT_FILE on macOS](ssl-cert-file-macos-trust-2409b.md) | 2397–2398 | The agent HTTP client honors a private-CA SSL_CERT_FILE on top of the platform store. |
| [Explicit withdrawal receipts across gateway replicas](kanban-remote-withdrawal-receipts-2409b.md) | 2403 | Archived owner cards deliver explicit, content-free withdrawal receipts to remote origins. |
| [Child model provenance recorded at reservation](delegation-child-model-provenance-2409b.md) | 2405–2407 | Delegated child sessions record where their model came from, degrading safely against older owners. |
| [Split-phase registry CPU sampling and drop diagnostic](split-phase-cpu-sampling-2409b.md) | 2408–2409 | The sync reporter samples CPU without blocking and exposes dropped samples. |

## Mirror follow-ups 2157–2253

| Feature | Patches | Scope |
|---|---|---|
| [Session scan admission and lock-safe stamps](session-scan-admission-and-lock-safe-stamps-2215.md) | 2157–2158, 2178 | Queue HTTP scan bursts before worker admission, bypass scans for fresh cache hits, and preserve SQLite WAL locks during stamp reads. |
| [No-progress dispatch budget](kanban-no-progress-budget-2215.md) | 2159 | Bound repeated waiting runs; re-park behind a usable condition or create episode-specific agent repair ownership. |
| [Atomic archive choices](kanban-atomic-archive-choice-2215.md) | 2160–2161 | Commit archive precondition, choice comment and receipt atomically; re-export the core implementation. |
| [Mid-tool update pause](update-pause-mid-tool-cancel-2215.md) | 2162, 2176 | Cancel resumable tool waits after a grace period and preserve surviving foreground job handoff results. |
| [Check-hook interpreter selection](check-hook-interpreter-selection-2215.md) | 2163 | Use the launcher or a supported PATH Python when no checkout virtualenv exists. |
| [Review result entry-path coverage](kanban-review-result-entry-coverage-2215.md) | 2164–2166 | Test result-only review artifacts and worker-session stamps across worker and non-worker tool callers. |
| [Bounded cron restart rerun](cron-restart-rerun-2215.md) | 2167–2168 | Rerun eligible interrupted attempts once at startup while preserving schedules, operator edits and provider recovery boundaries. |
| [Update-survivable terminal jobs](terminal-update-survival-2215.md) | 2169–2175 | Optional scoped persistence, foreground handoff, Stop precedence, output handling and integration-test synchronization. |
| [Missing-parent repair ownership](kanban-missing-parent-repair-2215.md) | 2177 | Admit agent-owned investigation atomically with an expired missing-parent block; retain required dependencies. |
| [Desktop "+ Create project" files the row at once](desktop-create-project-instant-2215.md) | 2179 | Create-project moves the row immediately via a provisional node and rolls back on failure. |
| [CI Linux jobs on Blacksmith runners](ci-blacksmith-runner-routing-2215.md) | 2180–2183 | Linux CI moves from self-hosted lanes to Blacksmith (4/2 vCPU), with bubblewrap and userns fixes. |
| [detect-changes falls back to the merge commit git diff](ci-detect-changes-git-fallback-2215.md) | 2189 | detect-changes uses the merge commit's git diff when the compare API is rate-limited. |
| [Kanban commit keys on coarse-timestamp filesystems, and CI test flakes](kanban-commit-key-coarse-stat-and-ci-flake-fixes-2215.md) | 2185–2188 | Kanban activity keys survive coarse mtimes; serialized probes; port-race test fix. |
| [Desktop remembers a backend that refuses submission_id](desktop-prompt-submission-identity-memo-2215.md) | 2184 | Desktop skips the refused submission_id round trip on legacy backends. |
| [Kanban placement treats an explicit running initial status as default](kanban-portable-running-initial-status-2215.md) | 2190 | Explicit running initial status no longer blocks worker-first placement. |
| [Kanban block notices name the calling agent](kanban-cli-block-actor-attribution-2215.md) | 2191 | CLI block notices name the calling agent instead of `user`. |
| [Durable retry of a failed notice close-out wake](notice-closeout-durable-retry-2215.md) | 2192–2193 | Failed close-out wakes retry durably after the quota reset, including long resets. |
| [Desktop transcript cache SQLite on a worker thread](desktop-transcript-cache-worker-thread-2215.md) | 2194–2196 | Transcript cache SQLite runs off the Desktop main thread; client never throws. |
| [Far quota wall reroutes a non-mandatory model pin](kanban-pin-quota-reroute-2215.md) | 2197 | A far quota wall moves a non-mandatory pin to a same-class peer for one run. |
| [A user Stop beats the core-update pause](update-pause-user-stop-wins-2215.md) | 2198 | A user Stop wins over the core-update pause so stopped turns are not resumed. |
| [Kanban final admission gate excludes the claim's own grant echo](kanban-admission-own-grant-echo-2215.md) | 2199 | Final cap gate excludes only the claim's own unused Board grant echo. |
| [Managed-run restart continuation](kanban-restart-continuation-2215.md) | 2200–2204 | Keep the run, claim and session across clean owner restarts; bound waits, repair interrupted tool results, and requeue pre-bootstrap or unsafe stops uncounted. |
| [Tool worker gate extraction](tool-worker-gates-2215.md) | 2205 | Move authorization/start-order and interrupt helpers into a re-exported module without changing executor imports or logger identity. |
| [Verified carried inputs](kanban-carried-evidence-2215.md) | 2206–2208 | Exempt only hash-pinned files covered by fresh carriage receipts; accept UTF-8 BOM and retain fail-closed placement fixtures. |
| [Stable Desktop system-cache layout](desktop-system-cache-2215.md) | 2209–2210 | Replace the early-round spare-slot heuristic with a pinned full-system marker for attended Desktop, retaining tools and rolling endpoints. |
| [Replica shutdown checkpoints](kanban-sync-shutdown-2215.md) | 2211 | Stop feed/snapshot pagination at cooperative shutdown boundaries without applying a page fetched after stopping. |
| [Pooled command discovery](commands-catalog-rpc-pool-2215.md) | 2212 | Run commands.catalog on the RPC pool rather than blocking the socket's ordered request lane. |
| [Durable failed-build model picks](agentless-model-intent-2215.md) | 2213–2214 | Commit ordered intent under the stored session key before retry, bound build diagnostics, and exercise real profile intent storage. |
| [Exact-question early clarify answers](clarify-early-answer.md) | 2215 | Require matching toolCallId and full displayed-question digest for decision-bearing early answers; preserve legacy withdrawals and provider-owned full-question coverage. |
| [Dispatch core-update hold](kanban-dispatch-update-hold-2253.md) | 2216 | Shared tick and claim-time hold gates; CLI/dashboard coverage and CLI reporting. |
| [Independent review routing](kanban-independent-review-routing-2253.md) | 2217–2218 | Review handoff overrides, distinct reviewer selection, current-run receipts and quota controls. |
| [Writer provenance and WAL scanning](state-writer-provenance-wal-scan-2253.md) | 2219–2220, 2230 | Value-free slow-write attribution and bounded GIL-held Linux descriptor scans. |
| [Refused-send notices](kanban-refused-send-notice-only-2253.md) | 2221 | Notice delivery without waiting ownership, child inheritance or shepherd links. |
| [Pinned pickup repair](kanban-pinned-pickup-repair-2253.md) | 2222 | Bounded local skip evidence and deduplicated agent-owned stall repairs. |
| [Board-qualified producer waits](kanban-board-qualified-waits-2253.md) | 2223–2225 | Read-only producer-board lookup, bounded cycle checks and qualified wake guidance. |
| [Published-worktree observation budget](kanban-published-worktree-budget-2253.md) | 2226–2229 | Historical empty entry, shared creation probe budget, cold-import safety and CLI line-budget cleanup. |
| [Durable CI audit receipts](kanban-ci-audit-receipts-2253.md) | 2231–2232 | Persist CI timestamps/URLs in acceptance evidence; contribution attribution metadata. |
| [Pinned-checkout update recovery](update-pinned-checkout-recovery-2253.md) | 2233–2238 | Preserve foreign code roots across pause/restart/recovery/migration; repair repeat deferrals. |
| [Protected checkpoint floor](checkpoint-protected-floor-health-2253.md) | 2239–2243 | Successful-floor receipts, independent health thresholds, busy-lock deferral and strict receipt validation. |
| [Completion receipts](completion-receipts-2253.md) | 2244–2253 | Add bounded, additive `completion_receipt` v1 so workflow done is distinct from artifact disposition; admit pending owners at the canonical writer, keep historical provenance apart from current state, and project it through remote inventories and the Desktop outcome tooltip. |

## Mirror follow-ups 2132–2156

| Feature | Patches | Scope |
|---|---|---|
| [Tile-open outcomes and cache evidence](desktop-tile-open-outcomes-2156.md) | 2132–2133 | Distinguish failed tile opens, suppress phantom sidebar abandonment, and warm tile transcript cache. |
| [Ready-wait restart floor](kanban-ready-wait-restart-floor-2156.md) | 2134 | Alerts count from the last run's end and name clock holds. |
| [Immediate contributed @ completions](desktop-instant-at-completions-2156.md) | 2135 | Show in-memory mention rows before asynchronous path completion. |
| [Comment provenance](kanban-comment-writer-provenance-2156.md) | 2136, 2139, 2149 | Agent versus human writers across dashboard, spawn context and CLI. |
| [Owner opens during restart gaps](desktop-owner-resume-restart-gap-2156.md) | 2137 | Dispatch explicit owner routes and safely observe returning control owners. |
| [Restart-resume grants](kanban-restart-resume-unused-grants-2156.md) | 2138 | Skip unused Board lease grants. |
| [Courier create identity](kanban-courier-create-identity-2156.md) | 2140, 2141 | Carry creation key and distinctness; separate transport key. |
| [Request withdrawal receipts](kanban-request-withdrawal-receipts-2156.md) | 2142–2146 | Durable receipt when a pending human request is archived. |
| [One-second main-loop diagnostics](desktop-one-second-loop-lag-2156.md) | 2147 | Record one-second Electron main-loop delays without logging sub-second jitter. |
| [Owner-scoped rename suggestions](gateway-owner-scoped-title-suggestions-2156.md) | 2148, 2151 | Scope legacy and authority title suggestions to the correct profile and owner. |
| [Foreign parent reads](kanban-show-foreign-replica-parents-2156.md) | 2150 | `kanban_show` falls back to the Board replica. |
| [Run machine](kanban-linked-card-run-machine-2156.md) | 2152 | Display which machine runs a linked card. |
| [Updater holds and move targets](kanban-updater-hold-and-move-targets-2156.md) | 2153, 2154 | Per-card hold diagnostics; capability-checked moves. |
| [Decision-stripe explanations](desktop-decision-stripe-tooltip-2156.md) | 2155 | Keep the question explanation while linked background work runs. |
| [External-draft test barrier](tui-external-draft-test-barrier-2156.md) | 2156 | Await React's external-draft commit before advancing the deferred-burst timer. |

## Mirror follow-ups 2074–2131

| Feature | Patches | Scope |
|---|---|---|
| [Killed-subagent notices](killed-subagent-notice-retry-2131.md) | 2074 | Retry busy-parent notices. |
| [Consumer contracts and test isolation](ci-consumer-contracts-and-test-isolation-2131.md) | 2075, 2087, 2089–2091, 2096 | Preserve extracted contracts and isolate process fixtures. |
| [Owner-terminal asks](owner-terminal-ask-lint-2131.md) | 2076–2078 | Closed owner-only initiation template. |
| [Contributor map](coach-agent-contributor-map-2131.md) | 2079 | Agent contributor attribution. |
| [Provider DNS recheck](provider-host-dns-recheck-2131.md) | 2080 | Confirm transient lookup failure. |
| [Codex retry](codex-bare-bad-request-retry-2131.md) | 2081 | Bounded bare Bad Request retry. |
| [Resume lease refusal](legacy-session-resume-lease-refusal-2131.md) | 2082 | Typed live-turn refusal. |
| [Queue reads](kanban-ready-card-queue-reads-2131.md) | 2083, 2088 | Queue evidence and dead-claim release. |
| [Cron shutdown](cron-gateway-shutdown-interrupt-2131.md) | 2084 | Distinguish orderly interruption. |
| [Busy-board GC](kanban-worktree-gc-busy-board-2131.md) | 2085–2086 | Per-board isolation and BOM-tolerant receipts. |
| [Standing-parent refusal](kanban-standing-parent-link-refusal-2131.md) | 2092 | Refuse never-finishing dependency parents. |
| [Portable reason audit](kanban-portable-reason-audit-2131.md) | 2093 | Record placement evidence. |
| [Transient retries](ci-and-build-transient-retries-2131.md) | 2094, 2105 | GitHub reads and product-backup cleanup. |
| [Launchd labels and budgets](launchd-explicit-label-and-launcher-timeout-2131.md) | 2095, 2099 | Explicit job identity and launcher timeout. |
| [Stall handoff](stall-guard-colon-handoff-2131.md) | 2097 | Re-prompt colon-only handoffs without tools. |
| [Chat approval priority](kanban-chat-go-priority-and-wait-notice-2131.md) | 2098 | Rank approved work and report unclaimed waits. |
| [Project move](desktop-project-move-optimistic-2131.md) | 2100 | Optimistic project row update. |
| [Multi-word completion](desktop-at-popover-multiword-2131.md) | 2101 | Opt-in spaced completion queries. |
| [Session task reads](kanban-session-tasks-first-read-retry-2131.md) | 2102–2103 | One bounded busy-retry budget. |
| [Row Archive button](desktop-session-row-archive-button-2131.md) | 2104 | Direct archive control. |
| [Cron ownership](outcome-cron-ownership-2131.md) | 2106–2111 | Done releases owners unless explicitly retained. |
| [Parked completion custody](parked-completion-custody-2131.md) | 2112–2114 | Preserve native completion custody through orderly exit. |
| [Portable producers](portable-producer-admission-2131.md) | 2115–2116, 2123 | Producer enrollment and effective-field audit. |
| [Transcript cache](transcript-cache-metadata-2131.md) | 2117 | Separate metadata from message bodies. |
| [Lifecycle busy responses](board-lifecycle-busy-2131.md) | 2118–2119 | Retryable 503 and sampled holder provenance. |
| [Node dependency custody](node-deps-relay-custody-2131.md) | 2120 | Watch stopped relay before installer handoff. |
| [Read corruption confirmation](read-corruption-background-2131.md) | 2121–2122 | Background confirmation and pending-read 503. |
| [Passive notice outcomes](passive-human-notice-outcomes-2131.md) | 2124–2126 | Preserve preceding outcomes beneath passive human notices. |
| [Update deferral](update-idle-pause-deferral-2131.md) | 2127–2130 | Bounded idle-only pause and legacy acknowledgment. |
| [Singleton usage](singleton-pool-usage-2131.md) | 2131 | Keep one-account pool identity visible. |

## Mirror follow-up 2073

| Feature | Patches | Scope |
|---|---|---|
| [Watch parks](kanban-watch-parks.md) | 2073 | Scheduled background checks do not hold their chat open. |

## Mirror follow-ups 2068–2072

| Feature | Patches | Scope |
|---|---|---|
| [Deleted worktree restoration](kanban-deleted-worktree-restoration.md) | 2068 | Deleted worktree restoration. |
| [Parent-first dispatch and unclaimed-parent notices](kanban-parent-first-dispatch.md) | 2069 | Parent-first dispatch and unclaimed-parent notices. |
| [Reject impossible machine-label conjunctions](kanban-runs-on-label-admission.md) | 2070–2071 | Reject impossible machine-label conjunctions. |
| [Visible pre-verify status and duration](pre-verify-status-duration.md) | 2072 | Visible pre-verify status and duration. |

## Mirror follow-ups 2066–2067

| Feature | Patches | Scope |
|---|---|---|
| [Cron shutdown failure accounting](cron-shutdown-failure-accounting.md) | 2066 | Preserve error evidence without charging shutdown interruptions to the job's failure streak. |
| [Current-chat adoption and subscription targets](kanban-current-chat-adoption.md) | 2067 | Turn-local chat hints and explicit target titles with wrong-chat warnings. |

## Mirror follow-ups 2030–2065

| Feature | Patches | Scope |
|---|---|---|
| [Mirror 2030 follow-ups: test hardening and a catalog revalidation](mirror-2030-test-and-catalog-followups.md) | 2030, 2040, 2042, 2044–2045 | Stall-probe cleanup, catalog annotation, observable daemon argv and shared revoke clock. |
| [Kanban dependency review: mixed prefix reviews resume repair, never approval](kanban-dependency-review-prefix-repairs.md) | 2031 | Mixed-prefix review requests resume repairs, never grant approval. |
| [Desktop: unminted native gateway descriptors surface the ticket mint error](native-gateway-unminted-ticket-errors.md) | 2032 | Preserve mint-required native ticket errors. |
| [Kanban scratch GC: explain preserved scratch without expanding deletion authority](kanban-scratch-preservation-diagnostics.md) | 2033–2034 | Explain retention, recognize nested owners and isolate inventory failures. |
| [Idle sweeps reuse structural scans until a SQLite commit](idle-commit-keyed-scan-memos.md) | 2035, 2037–2039 | Commit-keyed structural scans with clock checks every pass. |
| [TUI notification poller: commit-driven idle waits](notification-poller-commit-driven.md) | 2036 | Due-time waits and commit-keyed empty proofs. |
| [Desktop preview: a parked bystander window declines vault requests](preview-vault-parked-decline.md) | 2041 | Leave vault claims to the visible owner window. |
| [CLI: a typed empty `-t ''` is a no-tools pick](typed-empty-toolsets.md) | 2043 | An explicit empty CLI toolset pick disables tools. |
| [Background plugin checks never resurrect a retired profile home](profile-retirement-background-writes.md) | 2046–2047 | Do not recreate retired homes during background plugin checks. |
| [Evicted scratch custody](kanban-scratch-custody-2048.md) | 2048–2049, 2054 | Host-local receipts, retention fencing, resync preservation and clock-pinned tests. |
| [Relay authority test progress and FIFO policy](relay-authority-test-progress-2048.md) | 2050, 2059 | Progress watchdogs, explicit FIFO and published busy-agent waits. |
| [Locked ripgrep in reusable test jobs](ci-locked-ripgrep-2048.md) | 2051 | Provision test-job ripgrep through locked PM setup. |
| [TUI startup timeout process evidence](tui-startup-timeout-evidence-2048.md) | 2052 | Show screens and owned processes on startup timeout. |
| [Disk plugin refresh exclusion during queued scans](desktop-disk-refresh-exclusion-2048.md) | 2053 | Respect queued scanner in-flight exclusion. |
| [Delegate subscription reclaim error isolation](delegate-reclaim-error-isolation-2048.md) | 2055 | Keep healthy-board and remote notices despite reclamation errors. |
| [Diké catalog corpus annotation](dike-catalog-corpus-2048.md) | 2056 | Comment-only pinned corpus clarification. |
| [Reserved child configuration owner seeding](reserved-child-owner-seed-2048.md) | 2057–2058 | Preserve and classify reserved child first-turn configuration. |
| [WAL replica heartbeat test fixture](replica-heartbeat-wal-fixture-2048.md) | 2060 | Match production WAL concurrency in the fixture. |
| [Replica idempotency-key families in overlap checks](replica-overlap-key-family-2048.md) | 2061 | Consume published remote key families conservatively. |
| [Busy-board activity fallback](kanban-busy-board-activity-2048.md) | 2062 | Reuse stale busy-board rows without suppressing healthy boards. |
| [Launchd label propagation and missing-budget fallback](launchd-label-stop-budget-2048.md) | 2063 | Carry job label and diagnose assumed external stop budgets. |
| [Exact-pin credential recheck](exact-pin-credential-recheck-2048.md) | 2064 | Retry brief credential misses once without pin failover. |
| [Compaction output stubs and missing-image sibling hints](compaction-output-stubs.md) | 2065 | Decoded line counts, bounded edges, explicit elision and local image sibling hints. |

## Mirror follow-ups 2019–2029

| Feature | Patches | Scope |
|---|---|---|
| [Runtime and CI follow-ups](mirror-2029-features.md) | 2019–2026 | Checkpoint staging, queued-delivery/stall-probe synchronization, refusal display parity, pnpm lanes, deadline errors and Nix runner routing. |
| [Cron terminal retirement](mirror-admission-and-test-followups.md) | 2027–2029 | Terminal journal retirement, shared uncertainty holds and operator-pause boundary controls. |

## Mirror follow-up 2018

Upstream exclusion tip moves to `a2db0efb296`; one hash-pinned integration bridge precedes patch 2018. All 2017 previously published patches and all twenty-three earlier bridges are byte-unchanged.

| Feature | Patches | Scope |
|---|---|---|
| [Catalog release identity](catalog-release-identity.md) | 2018 | Plugin-catalog CI imports official upstream `v*` release tags after trusted-base checkout; unknown stays fail-closed. |

## Mirror follow-up 2017

No new merge or bridge; patch 2017 adds regression tests only. Earlier published bytes are unchanged.

| Feature | Patches | Scope |
|---|---|---|
| [Real unknown-scope producer confirmation](mirror-2017-features.md) | 2017 | Running owner accepted without completion; done owner rejected by real confirmation logic. |

## Mirror follow-up 2016

No new merge or bridge; patch 2016 applies directly after 2015. All earlier bridges and patch bytes remain unchanged.

| Feature | Patches | Scope |
|---|---|---|
| [Unknown owner task scope](mirror-2016-features.md) | 2016 | Typed unknown-not-yet for an unreadable owner board; one escalation, 24-hour backoff, no false completion. |

## Mirror follow-ups 2011–2015

Two additional upstream integration bridges precede 2011 and 2015. All earlier bridges and patch bytes remain unchanged.

| Feature | Patches | Scope |
|---|---|---|
| [Compression lifecycle import](mirror-2015-features.md) | 2011 | Follow the moved feasibility helper in tests. |
| [Bootstrap test dependencies](mirror-2015-features.md) | 2012 | Install psutil for real session-end process cleanup. |
| [Bounded live owner reads](mirror-2015-features.md) | 2013 | Recover missing replicas; stale evidence remains fail-closed. |
| [Live-job scratch retention](mirror-2015-features.md) | 2014 | Veto deletion while local owner-tagged jobs depend on the cwd. |
| [Offline repark cooldown](mirror-2015-features.md) | 2015 | Suppress repeated same-key offline escalations for 24 hours, not outage identity. |

## Mirror follow-ups 2006–2010

| Feature | Patches | Scope |
|---|---|---|
| [Worker run-end fencing](mirror-2010-features.md) | 2006 | Refuse post-run graph/lifecycle mutations and annotate successful handoffs. |
| [Transient Board failures](mirror-2010-features.md) | 2007 | Two-pass transient confirmation, diagnostic-only first failure, bounded reserve replay. |
| [Heartbeat verdict memo](mirror-2010-features.md) | 2008–2009 | Reuse projection across timestamp-only refreshes while reevaluating freshness; fixture alias correction. No live p95 claim. |
| [Owned API listener port](mirror-2010-features.md) | 2010 | Publish kernel-assigned port and rediscover it per crash-test daemon restart. |

## Mirror follow-up 2005

| Feature | Patches | Scope |
|---|---|---|
| [Component-evidenced due report overlap](kanban-sync-timing-due-cohorts.md) | 2005 | Separate due/no-due cohorts without changing compatibility total overlap; latency acceptance remains unproven. |

## Mirror follow-ups 1989–2004

| Feature | Patches | Scope |
|---|---|---|
| [Kanban wake-check episodes](kanban-wake-check-episodes.md) | 1989–1997 | A never-passing wake probe wakes its card after `kanban.wake_check_stale_hours` (default 48). Episodes carry across synced gateways through reset barriers. Probe-less and triage parks follow explicit reset rules. |
| [Desktop update-held prompts](desktop-update-held-prompts.md) | 1998 | An idle send held by the core-update admission hold shows "Waiting for a Hermes update". A plain idle Stop keeps it queued, and it still runs. |
| [Background masked-exit notices](process-masked-exit-notices.md) | 1999 | A `; echo EXIT=$?` exit 0 whose output prints a nonzero status gets "exited 0 but output shows failure" plus a Warning. Exit codes are unchanged. |
| [persist_on_release leaves any user unit](process-persist-any-user-unit.md) | 2000 | A persisted job spawned from any systemd user unit gets its own scope in `app.slice` and writes to a log, so a unit restart (e.g. for an update) doesn't kill it. |
| [Pull error attempt timing](kanban-pull-attempt-timing.md) | 2001 | A pull `board_error` records the failed HTTP attempt's `budget_ms`/`elapsed_ms`. Diagnostics only. |
| [Hidden-draft notice on verification gates](stop-gate-hidden-draft-notice.md) | 2002 | Every verification gate that hides a buffered draft prefixes `HELD_NOTICE`. `pre_verify` restores a hidden draft that a stub reply would otherwise lose. |
| [Kanban sync timing diagnostics](kanban-sync-timing-diagnostics.md) | 2003 2004 | Off by default; an opt-in capture of at most 600 s writes allowlisted JSONL, plus a fail-closed analyzer. Latency acceptance is unproven: there is no matched legacy baseline. |

## Mirror follow-ups 1980–1988

| Feature | Patches | Scope |
|---|---|---|
| [Runtime recovery, resource waits and test follow-ups](mirror-1988-features.md) | 1980–1984, 1986–1988 | Probe timeouts, stable clocks, lock ownership, recovery admission, resource waits, CF Access and owner storage refusals. |
| [Exact named-profile multiplexer homes](exact-profile-multiplexer-homes.md) | 1985 | Nested custom homes remain standalone; exact named profiles still multiplex. |
| [1988-entry mirror verification](mirror-1988-verification.md) | verification report | Full inventory, eighteen bridges and normalized tree equivalence. |

## Mirror follow-ups 1933–1979

| Feature | Patches | Scope |
|---|---|---|
| [Storage, sandbox, queue and Desktop follow-ups](mirror-1979-features.md) | 1933–1979 | Scoped corruption confirmation; safe skills copies; queue budgets and orphan supervision; scoped hold release and annotation caches; cron/worker receipts; remote use, queue recovery and test isolation. |
| [1979-entry mirror verification](mirror-1979-verification.md) | verification report | Exact inventory, seventeen bridges, normalized full tree and executed checks. |

## Mirror follow-ups 1924–1932

| Feature | Patches | Scope |
|---|---|---|
| [Runtime safety and review metadata](mirror-1932-features.md) | 1924–1932 | Prompt reset payloads; metadata paths; cron admission; ACP shutdown; reserve retry; terminal batch timeout; redaction; exact-head alias. |
| [1932-entry mirror verification](mirror-1932-verification.md) | verification report | Full inventory/replay/tree comparison and targeted source tests. |

## Mirror follow-ups 1919–1923

| Feature | Patches | Scope |
|---|---|---|
| [Desktop upstream compatibility](desktop-upstream-compat-1919-1922.md) | 1919, 1922 | Sibling SessionInfo/SDK exports; readiness and onboarding mocks. |
| [Worker queue reasons and explicit origin repin](kanban-queue-reasons-repin.md) | 1920 | Revision-conflict retries and scoped creator-worker repins. |
| [Credential read deny-list](credential-files-read-denylist.md) | 1921 | Sanitized skills mounts/sync and case-insensitive read denial. |
| [Commit-only CI merge-base fetch](ci-fetch-merge-base.md) | 1923 | Full commit graph with lazy trees/blobs and explicit fetch failure. |
| [1923-entry mirror verification](mirror-1923-verification.md) | verification report | Complete inventory, sixteen bridges, normalized tree and 216 targeted Python tests. |

## Mirror follow-ups 1822–1918

| Feature | Patches | Scope |
|---|---|---|
| [Gateway adapter lifecycle kept under its health cap after the 717447e upstream merge](gateway-adapter-health-budget.md) | 1822, 1823 | Adapter lifecycle/status methods move into `run_adapter_status.py` so the merged gateway adapter module stays under its health cap (1822/1823). |
| [TUI gateway: prompt turns start without history_lock under _sessions_lock](tui-prompt-turn-lock-order.md) | 1824–1828 | Prompt turns prepare admission before `_sessions_lock`, fixing a resume/submit deadlock; cancelled and raising prepared turns give their admission back (1824–1826); test seams (1827/1828). |
| [Embedded Kanban dispatcher re-reads max_in_progress every tick](kanban-dispatcher-live-cap.md) | 1829, 1833 | The embedded dispatcher picks up `kanban.max_in_progress` changes live (1829); a bad read keeps the last good cap, not the boot cap (1833). |
| [Kanban sync: pre-rename boards, request budgets and a visible stopped sync loop](kanban-sync-schema-and-down-visibility.md) | 1830, 1834, 1847, 1869 | Pre-rename boards sync (1830); per-request retry/redirect budgets (1834); a stopped sync loop is named in warnings, status and create, with sync-down regression tests (1847/1869). |
| [Desktop sends to a busy chat: gateway turns wait for the lease; unqueued sends return to the composer](desktop-busy-turn-lease-wait.md) | 1831, 1832, 1839 | Gateway-hosted Desktop turns wait for a held turn lease instead of refusing (1831/1832); an unqueued busy send goes back to the composer (1839). |
| [PR acceptance: app-pinned contexts accept legacy statuses](kanban-pr-merge-time-acceptance.md) | 1835 | A required context pinned to an app accepts that app's own bot commit status (1835). |
| [Skill index rows show triggers: frontmatter](skill-index-triggers.md) | 1836–1838 | `triggers:` frontmatter renders on skill index rows (`skills.index_triggers`, default on) (1836); skill_manage and learn-prompt description guidance (1837/1838). |
| [Outcome: an unviewed completion note is not an interrupted turn](outcome-unviewed-completion-note.md) | 1840 | A completion note written with no viewer no longer leaves the chat red as interrupted. |
| [Worker-first placement: wider enrollment, host-bound guards](kanban-worker-first-placement.md) | 1841–1843 | Ordinary repo and MacBook-placed cards enroll worker-first (1841); host skills, local worktrees and hub runs stay on their host (1842/1843). |
| [Desktop: SSH keep-alive bounded to the warm set](desktop-ssh-keepalive-warm-set.md) | 1844–1846 | SSH keep-alives are held only for the warm set, so one-off dials stop pinning remote backends (1844–1846). |
| [Kanban: hub CPU/cap final gate for worker-first claims](kanban-hub-cpu-cap-gate.md) | 1848–1860, 1896, 1899 | Measured CPU sampler, hub reporter and a final CPU/cap gate under `admission.lock` for hub claims; waiting turns outlast the sample; unused Board grants released (1848–1860, 1896, 1899). |
| [Kanban: trusted capsule admission for host_repo worktree cards on isolated backends](kanban-capsule-admission.md) | 1861–1866 | Isolated-backend builders can take host_repo worktree cards through a host-owned capsule registration with owner gates and a truthful /workspace contract (1861–1866). |
| [Red-CI owner: FAILED lists keep the same-run rule](kanban-red-ci-failure-owner.md) | 1867 | Same-run red-CI duplicates are refused even when the owner carries a FAILED test list (1867). |
| [Test stability 1822–1918](test-stability-1822-1918.md) | 1868, 1870–1872, 1882, 1883, 1892, 1905, 1906, 1911, 1914 | Collection in minimal venvs, required-extras lane guard, pytest 9.2 Starlette monkeypatch, and several deflakes (1868, 1870–1872, 1882/1883, 1892, 1905, 1906, 1911, 1914). |
| [Dependency handoff: same-second parks and PR-naming parents](kanban-dependency-review-continuation.md) | 1873, 1880 | Same-second dependency parks resume (1873); a parent naming the child's PR stays an ordering handoff (1880). |
| [Desktop: bounded primary reconnect refresh, healed owner-pinned requests](desktop-primary-reconnect-heal.md) | 1874, 1875 | Primary reconnect refresh is bounded and owner-pinned requests on a closed primary heal (1874/1875). |
| [CI: state contract dispatch and advisory-job timeouts](ci-workflow-timeouts-1822-1918.md) | 1876, 1878, 1895, 1898 | State core-contract suites dispatched on relevant core pushes (1876/1878); advisory and sync-PR jobs no longer cancel at timeouts (1895/1898). |
| [create_overlap: re-checked in the insert transaction](kanban-create-overlap.md) | 1877 | The overlap check runs again inside the insert transaction, closing the concurrent-create race (1877). |
| [terminal.credential_files honors the master-store read deny-list](credential-files-read-denylist.md) | 1879 | Config-listed credential files are refused when on the read deny-list (fail-closed). |
| [Kanban: a fenced connect of a missing board DB names the fence](kanban-fenced-connect-message.md) | 1881 | A fenced read-only open of a missing board DB names the fence. |
| [GET /api/sessions: single-flight and data-stamp cache](sessions-list-single-flight.md) | 1884 | Session list requests are single-flighted and cached on a data stamp. |
| [hermes update: restart obligations settle for unmapped stops and external-checkout runtimes](update-restart-obligation-settle.md) | 1885 | Stale "update did not restart gateways" warnings settle for unmapped stops and external checkouts. |
| [Worker-first repins and repin notices](kanban-queue-reasons-repin.md) | 1886, 1887, 1894, 1897, 1913 | Worker kanban_move becomes a Board repin (1886/1887); lost reserves count as pickups (1894); one repin notice per revision for unrunnable cards (1897/1913). |
| [⌘B park from SSH-isolated backends](sessions-mute-live-owner.md) | 1888 | ⌘B park from a remote SSH-isolated backend forwards to the live gateway owner (1888). |
| [Desktop: @session refs and tiles paint from the transcript cache](desktop-session-ref-transcript-cache.md) | 1889–1891, 1893 | Ref clicks and tiles paint from a warmed transcript cache while REST loads (1889–1891); sidebar hook split (1893). |
| [Kanban: worker-created cards readable from the board replica](kanban-foreign-card-replica.md) | 1900 | Worker-created cards are readable from the replica; their writes are refused as foreign_card (1900). |
| [Respawn guard: supported reopen reaches dispatch](kanban-respawn-guard-hold.md) | 1901, 1902 | A deliberate reopen after success lifts recent_success/active_pr until the rerun completes or posts a PR comment (1901/1902). |
| [Credential pool: probe-restored Codex quota persists across reloads](credential-pool-probe-restore-persist.md) | 1903 | A probe-restored Codex account stays restored across reloads and processes. |
| [persist_on_release jobs survive gateway restarts and run-scope stops](process-persist-on-release-survival.md) | 1904 | persist_on_release jobs outlive gateway restarts and Kanban run-scope stops (1904). |
| [Cron: a quota hold is released once its refusing pool serves again](cron-quota-hold-early-release.md) | 1907–1909 | Quota-held cron jobs resume once their pool serves again (1907–1909). |
| [Runtime: skipped worker receipt sequences named on both sides](runtime-worker-receipt-sequence.md) | 1910 | Worker persistence sequence gaps are reported with both sequence numbers. |
| [API server: a stream disconnect during worker teardown keeps the reply](api-server-stream-disconnect-reply.md) | 1912 | Late stream disconnects no longer discard a finished reply. |
| [Kanban comments record writer_kind](kanban-comment-writer-kind.md) | 1915 | Comments carry writer_kind so agent notes are not presented as the operator's. |
| [Model routing: a router-picked pin reroutes in-class on a far quota wall](model-routing-router-pin-reroute.md) | 1916 | Router-chosen pins reroute in-class on a far quota wall; user pins are unchanged. |
| [Redaction masks Google AI Studio AQ. keys](redact-google-aq-keys.md) | 1917 | AQ.-style Gemini keys are redacted. |
| [bot_desktop start() waits for the published DISPLAY](bot-desktop-display-wait.md) | 1918 | start() waits for the DISPLAY value, fixing a start/status race. |
| [1918-entry mirror verification](mirror-1918-verification.md) | verification report | Exact inventory, fourteen-bridge replay (seven new: two upstream merges plus a combined third, two branch-context and two merge-resolution bridges), three source-checked ancestry-only entries, normalized full tree and five fingerprint re-pins. |

## Mirror follow-ups 1703–1821

| Feature | Patches | Scope |
|---|---|---|
| [Kanban board lifecycle isolation and busy budgets](kanban-board-lifecycle-isolation.md) | 1703–1707, 1742, 1754, 1755 | Opening a session database no longer waits behind a Kanban board's journal or lifecycle lock (1703/1704). |
| [Agent plugin activation from a --profile backend](agent-plugin-activate-profile-scope.md) | 1710 | `POST /api/agent-plugins/activate` scopes the default profile by name, so a backend started with `--profile` still serves activations meant for the root home. |
| [Gateway and Kanban idle CPU cuts](gateway-idle-cpu-cuts.md) | 1711, 1718, 1738, 1739, 1758, 1759 | The control socket and session-hosted service skip idle peer and owner-scope work that no caller uses (1711). |
| [Gateway thread profiler: bounded cost, 25 Hz default](gateway-thread-profiler-cost.md) | 1712, 1728 | An active `hermes gateway profile` run bounds its own hot-path cost, with a CPU-isolation test (1712). |
| [Spawn ledger: no rows lost past a stalled lock holder](process-spawn-ledger-stalled-lock.md) | 1713 | A spawn-ledger writer that cannot get the cross-process lock within its wait no longer does an unlocked read-modify-write that drops rows another writer landed. |
| [Post-turn outcome refresh in the session's profile scope](turn-outcome-secret-scope.md) | 1714, 1715 | The post-turn outcome refresh binds the session's profile secret scope, so it reads that profile's credentials, not the launching process's (1714); the regression distinguishes foreign and launch credentials (1715). |
| [Kanban sync loop: skip unchanged chat-link passes](kanban-sync-loop-steady-rounds.md) | 1716, 1717, 1721, 1723 | `reconcile_links(skip_unchanged=True)` (used by `run_forever`) skips a board whose inputs equal its last completed pass, and the placement config is loaded once per round instead of per placeholder check (1716/1717). |
| [Desktop: native gateway dials recover after a ticket refusal](desktop-native-gateway-ticket-recovery.md) | 1719 | A native gateway dial whose ticket factory refused once reconnects on the next attempt instead of staying disconnected. |
| [Kanban: one open hermes-mods core mirror card](kanban-mirror-singleton.md) | 1720, 1772, 1801 | `kanban_create` refuses a second open card that is a hermes-mods core mirror pass and names the open owner (1720); the singleton identity is widened while capability execution cards are preserved (1772). |
| [Desktop poll cost: revalidated annotations and cached status reads](desktop-poll-cost-1722.md) | 1722, 1724, 1743, 1751–1753, 1756, 1798, 1799 | Past its TTL, the Kanban annotation is re-served inline when its fingerprint is unchanged, bounded by max ages for time-derived states; gateway platforms, bundled manifest names and status session rows are cached on file signatures (1722, moved into topical... |
| [Kanban: closeout obligations counted once](kanban-closeout-obligation-dedupe.md) | 1744, 1773 | A remote obligation already counted is not counted again in closeout (1744). |
| [PR acceptance: pre-merge checks vs main CI](kanban-pr-merge-time-acceptance.md) | 1725–1727, 1745, 1782, 1802 | PR acceptance separates pre-merge acceptance from main-branch CI (1725); skipped post-merge jobs stay out of the red-main notice (1726); checks pending at merge keep their outcomes (1727). |
| [Parked PR owners resume after verdict-free reviews](kanban-verdict-free-review-resume.md) | 1729–1731, 1819 | A PR owner parked for review resumes after an exact-head review that carries no verdict (1729); review aliases are bound and diagnostics deduplicated (1730); explicit approval paths are covered (1731). |
| [Committed model switches reach the next build](model-switch-route-intent.md) | 1732–1737, 1763, 1765, 1766 | Adopting a legacy local session restores its recorded route (1732). |
| [Bot Chat sessions follow their profile's selection](bot-chat-follow-profile.md) | 1762, 1764, 1767–1770, 1815 | Managed Bot Chat follows its profile and a held pick ends durably (1762/1764); in-process Bot Chat does the same and the switch intent names the physical target (1767). |
| [Pending completion notices survive an orderly backend exit](native-completion-notice-journal.md) | 1746, 1747 | Pending native completion notices are journaled across an orderly backend exit and delivered after restart (1746); bound notice helpers are used and journal-after-marker cancellation is covered (1747). |
| [Failed-turn boundary explains a provider refusal](provider-refusal-turn-boundary.md) | 1749, 1750 | When the provider refuses (content policy), the failed-turn boundary says so instead of a generic failure (1749); the boundary is asserted through ACP history (1750). |
| [Worker queue reasons and explicit origin repin](kanban-queue-reasons-repin.md) | 1757, 1795 | Worker queue observations persist as queue reasons, and an explicit origin repin is supported; a lost repin reply is reported as an unknown outcome (1757). |
| [API server: per-request context usage beside billing totals](api-context-usage.md) | 1760, 1761 | Chat Completions responses add optional `usage.context_usage = {version: 1, complete, requests: [{provider, model, input_tokens, input_limit}]}`, one entry per main-loop provider response; `usage.prompt_tokens` stays the turn's billing sum (1760/1761). |
| [Model routing state: WAL, and resume on the allocation store's route](model-routing-state-wal-and-resume.md) | 1774, 1792 | The routing state database uses WAL and sticky reuse prepares outside the writer lock, so allocations no longer time out with `database is locked` under contention (1774). |
| [Unit tests: offline catalogs and a loopback-only name fence](offline-unit-test-network-fence.md) | 1775–1781, 1788, 1796 | Video, model-metadata, models.dev and CLI catalog transports are stubbed offline by autouse fixtures (1775–1778). |
| [Kanban parks: deploy window and declared clock waits](kanban-deploy-window-parks.md) | 1783, 1804, 1820 | A deploy park reads the host's `cache/core-update/window.json` (schema v1, fresh, this host) and floors its wake to the window, instead of re-parking and escalating while the deploy is only queued (1783). |
| [Termux assembly: tools dir mode and test mount dir](termux-assembly-permissions.md) | 1784, 1785 | The assembly tools dir is created 0755 regardless of host umask (1785); the test uses a unique, self-cleaning docker mount dir (1784). |
| [TUI gateway: failure reset time stays inside ErrorPayload](tui-error-payload-reset-at.md) | 1789 | Agent-build error events map `failure_resets_at` to the declared `reset_at` instead of leaking an undeclared key that violated `ErrorPayload`. |
| [Kanban host dispatcher/sync locks belong to the root gateway](kanban-host-locks-root-gateway.md) | 1790, 1791, 1807–1812 | Host sync belongs to the root profile gateway, and released inherited creator links are healed (1790). |
| [⌘B park and outcome refresh write on the live owner](sessions-mute-live-owner.md) | 1793 | `POST /api/sessions/{id}/mute` and `GET /outcome?refresh=1` go through the live owner's store instead of `exclusive_maintenance`, which a running gateway always holds, so ⌘B no longer fails with "Exclusive maintenance refused". |
| [Kanban: scratch files named in handoff metadata survive cleanup](kanban-handoff-metadata-files.md) | 1794 | Workspace files cited in `kanban_complete`/`request_review` metadata (for example `replay_report`, `plan`) are preserved when the managed scratch workspace is reaped. |
| [Kanban stop guard flushes the worker's answer first](kanban-stop-guard-flush.md) | 1797 | A worker's text-only final answer is flushed to a durable assistant row before the stop-loop nudge's next model request, so observed managed workers no longer fault closed. |
| [kanban_create: one owner per red-CI failure](kanban-red-ci-failure-owner.md) | 1800 | A new card naming the same failing test function, or the same Actions run when neither names a test, as an open card is refused (`create_overlap`) unless lineage, waits-on, recovers, a citation of the owner's id or `distinct_from` applies. |
| [Desktop: move into a project the gateway knows by another name](desktop-project-move-by-folder.md) | 1803 | Moving a session into a project whose folder the target gateway already owns under another project name reuses that project instead of failing. |
| [scripts/check: quiet skip of a pm environment without JSON](scripts-check-pm-env.md) | 1806 | The pre-push hook skips a composing checkout whose `pm.environments` output is empty or not a JSON object, without printing a traceback. |
| [delegate_task steer/stop on a just-finished child](delegate-finished-child-control.md) | 1817 | Steering or stopping a subagent that already finished answers `already_finished` instead of "No live subagent". |
| [Kanban placement: unknown pin names are refused as unknown](kanban-placement-unknown-pin.md) | 1818 | A `placement` pin naming no gateway hermes-hub has a record of is refused as an unknown name, listing the gateways it knows, instead of "not live: capability record stale or missing". |
| [Blocked notices show the card's exact human ask](kanban-actionable-block-notices.md) | 1821 | Local (`session_notifications`), remote (`kanban_remote_notices`) and gateway (`kanban_watchers_notifier`) blocked notices render the card's validated `human_request` (action, why, scope) and the exact task link, instead of a 160-character clipped event rea... |
| [Test stability 1703–1821](test-stability-1703-1821.md) | 1708, 1709, 1740, 1741, 1748, 1771, 1786, 1787, 1805, 1813, 1814, 1816 | Backup busy deadline tested at its guarded opener (1708/1709); hermetic system-unit home fixture and split socket preflight suite (1740/1741); synthesized-turn commit point bound into rebound harnesses (1748); headless worker probe passes the reservation's... |
| [1821-entry mirror verification](mirror-1821-verification.md) | verification report | Exact inventory, unchanged seven-bridge replay, normalized full tree, six fingerprint re-pins and one new reviewed fingerprint. |

## Mirror follow-ups 1626–1702

| Feature | Patches | Scope |
|---|---|---|
| [Kanban active_pr: foreign pulls, disclaimers, portable comment writer](kanban-respawn-guard-hold.md) | 1626 1636 1637 1653 1654 1655 | `active_pr` ignores another live card's contract pull and narrow "does not block this card" disclaimers (own contract always holds); hold notice reads checks via the broker argv; worker comments carry `writer_run_uid` so synced replicas keep the hold. 1626 is the side-branch form ported by 1653. |
| [Skill locks without a cwd](mirror-runtime-deleted-cwd.md) | 1627 | With locked skills and a deleted process cwd, a command without a workdir is refused instead of crashing in `os.getcwd()`. |
| [state.db repair: no pre-lock read](state-db-repair-prelock-read.md) | 1628 | The persistent repair budget reads the ledger count before fingerprinting, so a queued repairer no longer holds state.db open during its peer's holder preflight. |
| [Plugin activation fan-out](plugin-activation-serve-fanout.md) | 1629 | Activation reaches every live serve and Desktop-child backend; a dead, torn or unreachable record never blocks the other; failures are named per backend. |
| [Auto-decompose grace window](kanban-auto-decompose-min-age.md) | 1630 1631 | The dispatcher's auto-decompose sweep skips triage cards younger than `kanban.auto_decompose_min_age_seconds` (120 s); manual decompose unchanged; docs row. |
| [Kanban assignment capabilities](kanban-assignment-capabilities.md) | 1632 1633 | Block refusals, schema and prompt offer `kind='assignment'` only where a different profile can take the card; otherwise they name owner-card, `needs=[...]` and supervisor-request routes. |
| [Kanban restart resume](kanban-restart-resume.md) | 1634 1635 | An over-headroom prior transcript is resumed and compacted by the worker before its first model call, with a typed fallback to the transcript tail. |
| [Queued-work completion publication (test)](gateway-queued-work-publication-gap.md) | 1638 | The new-session queued-work journey replays until completion publication is visible; a fixture holds the terminal-to-publication gap deterministically. |
| [Capacity health ignores foreign-owned boards](test-host-slot-cap.md) | 1639 | `all_at_capacity` skips boards owned by another dispatcher, so a capped local board is idle, not "stuck". |
| [Worker explicit board pin](kanban-worker-explicit-board-pin.md) | 1640 | A dispatcher worker's explicit `board=` must match its pinned database; a mismatch returns `board_pinned` instead of silently using the pinned board. |
| [Sync repair revision](kanban-sync-repair-revision.md) | 1641 1642 | A cursor-only replica change waits 30 s between full repair passes; local writes, rebuilt replicas (even with a reused inode) and mode changes repair at once. |
| [Codex toolless commentary turns](codex-toolless-commentary-turns.md) | 1643 | A completed commentary-only Codex response is the final answer when no tools are offered; continuations are logged; class routes fail over at once on an exhausted pool. |
| [Kanban supervisor requests](kanban-supervisor-requests.md) | 1644 | Test: an archive request is rejected at execution when the orphan's creator reopens. |
| [Review checkpoint identity across sync](kanban-dependency-review-continuation.md) | 1645 | Test: a synced reassignment never resumes the old review; same-second vs later assignment chronology is pinned. |
| [Wake on a rebased ancestry probe](kanban-wake-dead-ancestry.md) | 1646 1647 1648 | A failing `merge-base --is-ancestor <sha>` wake check whose patch landed upstream under another SHA wakes the card once with the landed SHA; bounded, local-only, sanitized git. |
| [PR landing disposition](kanban-pr-landing-disposition.md) | 1649 1656 1657 1658 | PR completion needs MERGED, CLOSED, an open same-board exact-PR landing owner, or a left-open reason; notices, payload and origin release carry it; malformed `published_pr` refused. 1657/1658 are byte-identical duplicates of 1649/1656. |
| [Subscription quota deadlines](subscription-quota-deadlines.md) | 1650 | A `SubscriptionQuotaUnavailable` in a tool `check_fn` is an INFO verdict with its reset, once per wall, not cached past the reset. |
| [SIGUSR2 stack dump](gateway-sigusr2-stack-dump.md) | 1651 | The native live-test fixture dumps stacks from refcounted Python frames instead of faulthandler's GIL-less watchdog. |
| [Actions minor/patch bump](ci-actions-dependency-bump-1652.md) | 1652 | Dependabot re-pins nine GitHub Actions across 14 workflows. |
| [Human chat ownership excludes machine sessions](kanban-machine-session-links.md) | 1659 | Skip machine transcripts in creator/subscription inheritance; captured reconciliation releases and dry-run/release repair CLI. |
| [Test stability: listener-owned ports](test-stability-1659-1681.md) | 1660 | Bind-guard tests allocate at the listener instead of releasing speculative free ports. |
| [Bot relay connection retention](desktop-bot-relay-connection-retention.md) | 1661 | Pin, roster-sync and drain one representative route per connection; deliver on the exact target profile. |
| [Worker-first portable placement](kanban-worker-first-placement.md) | 1662 | Opt-in tool/CLI enrollment, durable execution fences, bounded fair reservations and whole-host admission metrics; requires matching Board deployment. |
| [Idle watcher change-key gates](gateway-idle-watcher-change-keys.md) | 1663 | Cache directory membership and stable replica snapshots without suppressing edits, grace expiry or session-liveness decisions. |
| [Test stability: shared watcher fixture](test-stability-1659-1681.md) | 1664 | Expose the shared fixture through an explicit module alias without imported-name shadowing. |
| [Central sync: loop-owned connections](kanban-central-sync-client.md) | 1665 | Retain bounded idle SQLite handles per loop and avoid unchanged bookkeeping writes. |
| [Kanban annotation content revisions](kanban-annotation-content-revision.md) | 1666 | Trigger-backed content keys ignore bookkeeping-only writes and reuse equal annotation objects. |
| [Annotation revision schema changes](kanban-annotation-content-revision.md) | 1667 | Drop watched-column triggers before DROP COLUMN; reinstall on writer initialization with stat fallback during the gap. |
| [Test stability: unreachable fanout port](test-stability-1659-1681.md) | 1668 | Hold a bound non-listening socket throughout the unreachable-backend assertion. |
| [Test stability: optional SDK and cgroup gates](test-stability-1659-1681.md) | 1669 | Explicit SDK skips and independent disposable-child migration permission probe; unexpected kernel errors still fail. |
| [Central sync: transactional detach fence](kanban-central-sync-client.md) | 1670 | Recheck sync mode inside feed-apply transaction before queued feed can overwrite detached custody. |
| [Central sync: whole-round detach fence](kanban-central-sync-client.md) | 1671 | Nonblocking per-board exclusion covers complete sync rounds, including repairs and sends. |
| [Central sync: board-local busy refusal](kanban-central-sync-client.md) | 1672 | Typed contention leaves other boards progressing and retries on a later pass. |
| [Gateway external-supervisor handback](gateway-restart-drain-holds-and-reattach.md) | 1673 | Hand back before stale installed-service dispatch; the temporary owner exception is superseded by 1675. |
| [Desktop restart failure logs](desktop-gateway-restart-failure-log.md) | 1674 | Preserve separate stdout/stderr tails and the unchanged one-attempt stale-owner restart policy. |
| [Gateway unconditional supervisor handback](gateway-restart-drain-holds-and-reattach.md) | 1675 | Honor declared supervision before installed-service dispatch even for canonical service owners; reap fake-supervisor children safely. |
| [Gateway restart fixture isolation](gateway-restart-drain-holds-and-reattach.md) | 1676 | Stub service discovery, assert refused handback directly and join teardown instead of sleeping. |
| [Central sync: clean busy-detach CLI](kanban-central-sync-client.md) | 1677 | Print contention and return status 2 without hiding unexpected exceptions. |
| [Kanban profile-owner-down holds](kanban-owner-down-holds.md) | 1678 | Exit 68 for persistent pre-admission owner failure; hold unclaimed work with wake checks and deduplicated repair cards. |
| [Test stability: billing eligibility](test-stability-1659-1681.md) | 1679 | Disable creator-parking grace only in billing E2E fixture configuration. |
| [Decomposer context-local board selection](kanban-decomposer-board-scope.md) | 1680 | Scope background board selection without leaking Kanban-only skills into concurrent catalog/slash discovery. |
| [Test stability: streamed sidecar AST scan](test-stability-1659-1681.md) | 1681 | Retain constants and reduced write references, not every Python AST; preserve unresolved-key accounting. |
| [Kanban-only skills follow the Kanban tool gate](skill-kanban-env-gate.md) | 1682 | A bare `HERMES_KANBAN_BOARD` boot pin no longer lists `environments: [kanban]` skills; detection follows the Kanban tool gate (owned worker task, ambient origin session, or profile `kanban` toolset). |
| [TUI gateway stdio RPC ownership](tui-stdio-rpc-ownership.md) | 1683–1685 | The stdio fallback transport writes only when stdout is the JSON-RPC channel (`_stdio_is_rpc_channel`), so unrouted frames never land in gateway/Desktop logs; test fixtures declare ownership explicitly. |
| [Per-file test peak RSS](test-file-peak-rss.md) | 1686, 1698, 1699 | `run_tests_parallel.py` reports top-5 per-file peak RSS and warns over `HERMES_TEST_FILE_RSS_WARN_MB` (512, per-file marker override, exit code unchanged); suite hub-state default, import-free dashboard-auth dummy hash and a compute-host stub bring the TUI server test file under budget. |
| [Test stability 1687–1700](test-stability-1687-1700.md) | 1687–1689, 1700 | Offline image-gen catalog tests with live GETs refused, a fake clock for Kanban health receipt TTL, and a session-daemon reaper that matches other processes' `HERMES_HOME` lexically on hosts running live gateways. |
| [Platform connect deadline await chain](platform-connect-await-chain.md) | 1690 | A platform connect timeout logs the connect and bot tasks' suspended await chains as `file:line:qualname` only (no reprs or locals) before raising. |
| [Bot relay install-wide mailbox](bot-relay-install-mailbox.md) | 1691, 1692 | Named-profile Bot Chat DMs to remote bots queue in and wait on the install root's relay mailbox the connection backend drains; `from_profile` keeps sender identity. |
| [MCP quiet finalization](mcp-finalization-quiet-exit.md) | 1693, 1702 | During interpreter finalization the MCP loop drops exception reports and server run loops skip transport recovery, so exiting workers leave no `--- Logging error ---` storms. |
| [Gateway thread profiler](gateway-thread-profiler.md) | 1694–1696 | `hermes gateway profile` / control verb `profile`: bounded per-thread wall-stack samples and native CPU deltas from the live gateway, batched identical snapshots, 0.1 s CPU refresh, trimmed responses. |
| [MCP `lazy_ignore_ttl`](mcp-lazy-ignore-ttl.md) | 1697 | With `lazy: true`, `lazy_ignore_ttl: true` lets a server start from a fingerprint-matching schema cache entry past its `ttlMs` (e.g. `ttlMs: 0`). |
| [pre_final_gate held-draft notice](pre-final-gate.md) | 1701 | A hold whose draft never streamed gets core's `HELD_NOTICE` ("Your draft above was NOT shown…") before the hook's nudge; streamed drafts and interrupt-only stops get none. Pairs with session-outcome's held-draft restore. |
| [1702-entry mirror verification](mirror-1702-verification.md) | verification report | Exact inventory, seven-bridge replay, three source-checked ancestry-only entries, normalized full tree and four existing fingerprint re-pins. |

## Mirror follow-ups 1592–1625

| Feature | Patches | Scope |
|---|---|---|
| [Kanban quota episodes and clock-held stuck warning](kanban-quota-episodes.md) | 1592 1594 1595 1606 | A stuck warning names clock-held deadlines and stays quiet when every ready card waits on one; quota-wall requeues honour per-card reset deadlines with backoff, reroute default-routed cards off a known wall, check walls they did not record and fall back to the strong tier. |
| [Kanban worker fence: finished-card shell hint, board-scoped tool fence](kanban-worker-fence-refusal.md) | 1593 1598 | A finished card's live worker shell gets a `kanban_comment` hint instead of the Desktop Cmd-Q leak advice; board-local `kanban_*` tool writes are fenced only on boards under the fenced root, like the DB fence. |
| [Kanban notice: a live holder's never-acked claim no longer blocks the owner](kanban-notice-refusal-rewind.md) | 1597 | A live non-owner's never-acked notice claim no longer blocks the chat's owner: refused or stopping holders release the marker, and silent-only or stale live claims are reclaimed. |
| [Test stability 1592–1625](test-stability-1592-1625.md) | 1596 1599 1604 1621 | Active-session contender tests skip `.N.tmp` staging files and fail fast; every unittest discovery form is refused; pause-boundary handshakes are written atomically; the locked wheelhouse fixture retries dropped PyPI connections with hashes still verified. |
| [Desktop plugin loader refresh](desktop-plugin-loader-refresh.md) | 1600 | Desktop re-reads every known disk plugin's `plugin.js` on power resume and once a minute, and reloads files whose bytes changed when a filesystem watch event was missed. |
| [Kanban edit: lowering priority needs --reason](kanban-edit-priority-reason.md) | 1601 1602 | `hermes kanban edit --priority` refuses to lower a card's priority without `--reason` (recorded on the event); `kanban_db_edit` imports `kanban_db` last to avoid a circular ImportError. |
| [Kanban ordering-park handoff across gates and sync](kanban-pr-park-handoff.md) | 1603 1610 | Ordering-park handoffs survive completed operator-added gates and already-done synced parents, and match the checkpoint by stable `run_uid` across sync's run-id remapping. |
| [Kanban creator chats across board files and chat-link release](kanban-creator-chat-routing.md) | 1605 1613 1614 1615 1623 1624 | A worker's card filed on another board file reaches its creator's human chat; explicit/ambient origins keep parent notify subs and headless ones record unresolved; a closed card's released or owner-written link stops counting as open; replica reads go through the lock guard. |
| [Kanban origin creation: default intent fields](kanban-origin-work-ledger.md) | 1607 | Origin creation ignores default `chat_origin`/`placement_policy` in the stored intent envelope, so pre-upgrade retries reuse the original card. |
| [Update pause: cooperative pause freshness clock](update-pause-followups.md) | 1608 1609 | A cooperative update pause restarts the auto-continue freshness clock at `paused_at` while crash recovery keeps `started_at`; test fixtures reused by assignment (F811). |
| [Kanban recovery park identity](kanban-recovery-park-identity.md) | 1611 1612 | Synced boards stamp each recovery park with its own identity, so reconcile restores no longer change which recovery card may resume; the goal triage hold keeps local order on legacy or forked history. |
| [Background commands under the foreground's bash](background-shell-bash.md) | 1616 1617 | POSIX background commands run under the foreground's bash after login-shell init; dependent tests follow the exec change. |
| [Local session policy quarantine](local-session-policy-quarantine.md) | 1618 1619 1620 | Deterministically invalid local session policies are quarantined (history kept, retryable storage failures not quarantined) and shown in recovery diagnostics. |
| [Kanban sync: kept-alive Board-service connection](kanban-central-sync-client.md) | 1622 | The Board client reuses one kept-alive HTTP/1.1 connection per thread instead of a TLS handshake per request; stale GETs retry once, POSTs are never resent. |
| [Cron quota hold on subscription-pool refusal](provider-fallback-and-token-routing.md) | 1625 | Cron quota hold parks jobs on the routing pool's typed `SubscriptionQuotaUnavailable` refusal until its reset, and warns when a quota failure has no usable deadline. |
| [1625-entry mirror verification](mirror-1625-verification.md) | verification report | Exact inventory, complete six-bridge replay and normalized tree; two existing fingerprints re-pinned (`tui_gateway/kanban_unloaded.py`, `tui_gateway/update_pause.py`), no new fingerprint or bridge. |

## Mirror follow-ups 1589–1591

| Feature | Patches | Scope |
|---|---|---|
| [Kanban restart resume](kanban-restart-resume.md) | 1589 1590 1591 | A run requeued after its owner's restart (`owner_restart_lost`) copies the lost run's transcript into its fresh session and gets a resume note instead of the transcript tail; typed fallbacks, planned and copied off the owner loop, resumes only below 85% of the compaction threshold. |
| [1591-entry mirror verification](mirror-1591-verification.md) | verification report | Exact inventory, complete six-bridge replay and normalized tree; no new fingerprint or bridge. |

## Mirror follow-ups 1584–1588

| Feature | Patches | Scope |
|---|---|---|
| [CI timings: caller-prefixed slice job names](ci-timings-regression-warning.md) | 1584 | Slice durations are read from jobs named `Python tests / Run tests (n/8)`; a run with no per-file durations is unknown, so the regression stays a Warning. |
| [Cron gate failures heal on a gate tick](mirror-admission-and-test-followups.md) | 1585 | Monitor-source and unparseable-config failures are booked `last_error_kind="gate"`; the next good gate tick clears a fire/gate-only streak; an AST guard requires every pre-agent failure return to tag a kind. |
| [Shell-quoted interpreter path](shell-quoted-interpreter.md) | 1586 | `shlex.quote(sys.executable)` at 12 shell-command sites (11 test fixtures, the Teams pip hint); an AST repo guard flags a bare interpreter shell word; shebangs exempt. |
| [Host registry/storage refusals requeue uncounted](kanban-worker-requeue-and-crash-records.md) | 1587 | An empty or invalid host routing registry at worker start books exit 69 with a typed proof and requeues the admitted run uncounted; host faults file one Kanban card per host per 15-min window. |
| [Busy admission lock defers a claim at INFO](update-admission-fence.md) | 1588 | `AdmissionBusy` in `claim_transaction` logs INFO without a traceback; real lock failures stay WARNING with `exc_info`. |
| [1588-entry mirror verification](mirror-1588-verification.md) | verification report | Exact inventory, complete six-bridge replay and normalized tree; no new fingerprint or bridge. |

## Mirror follow-ups 1540–1583

| Feature | Patches | Scope |
|---|---|---|
| [Goal judge unachievable recovery](kanban-goal-handoff-judge.md) | 1540–1541 | A parsed unachievable verdict parks the goal in triage with a kernel receipt and files a bounded supervisor-recovery card; 1541 is an explicit empty duplicate source entry. |
| [Review checkpoint identity across sync](kanban-dependency-review-continuation.md) | 1542 | Review checkpoints carry the run's stable UID so continuation survives sync run-ID remapping; a mismatched UID or remapped legacy ID stays held. |
| [Kanban maintenance fenced to the card's home gateway](kanban-home-gateway-maintenance.md) | 1543 1551 1566 1569 1571 1572 | Standing-ask rechecks, quota releases, agent-block triage, guard-hold cap/notice and readiness/missing-parent upkeep skip foreign copies; only the home writes. |
| [Recovery requester and replica triage stay at the target's home](kanban-home-gateway-maintenance.md) | 1570 1576 | Recovery requesters are pinned to the home machine; stale triage skips foreign copies; recovery links carry a stable `park_event_uid`. |
| [Offline DarkWakes don't spend the recovery budget](kanban-worker-darkwake-recovery.md) | 1544 | With both DNS controls failing, a worker waits in 900 awake-second windows while it still owns the exact claim; only that wait is uncharged, blips and online failures still count. |
| [Managed workers keep the credential rotation pool](managed-worker-credential-pool.md) | 1545–1546 | Managed workers keep the resolver's `credential_pool`, so Anthropic and Codex rotate accounts on a 429; an explicit launch key never gets a pool; fallback entries stay separate. |
| [Owner-wake cross-board unblock grants](kanban-owner-wake-grants.md) | 1547–1548 | Owner-wake supervisors can show/unblock exactly listed (board, id) cards on any local board, with run fencing, ambiguity refusal and revocation re-checked at the write. |
| [Worker-created card chat routing](kanban-creator-chat-routing.md) | 1549–1550 | Worker-created cards link to the nearest human owning chat (or an explicit origin), with no-replay backfill, local origin delivery and unsynced reads. |
| [Session change watcher projection revision](session-projection-revision.md) | 1552–1553 | Triggers replace a random session-projection generation transactionally; the watcher reads it instead of re-digesting sessions; installs seed a unique generation. |
| [Subscription quota deadlines and exact pins](subscription-quota-deadlines.md) | 1554–1556 | Exhausted OAuth pools raise a typed `SubscriptionQuotaUnavailable` with its reset; workers exit 75 and receipts hold admission until reset; exact pins kept; authorized class fallback first. |
| [Shared blocker exact-pin route preview](kanban-shared-blockers.md) | 1557 | `task_route` skips provider-wall retiering for pinned tier reasons, so a pinned card's blocker preview matches dispatch routing. |
| [Remote-assignee entry paths and unknown labels](mirror-0922-fix-kanban-check-requires-where-runs-on-lands-the-card-never-refuse-a-review-verdict.md) | 1558 | Repinning follows create's `runs_on` execution-host policy; an empty or self-less registry keeps local validation instead of assuming remote. |
| [Kanban board cold counts](kanban-board-cold-counts.md) | 1559 | `/boards` counts use a read-only `query_only` connection with no writer init, migration or DB creation; prewarm covers active, default and blocked boards. |
| [Session outcome: silent cron, durable worker leases, routing snapshots](session-outcome.md) | 1560–1562 | Completed silent cron runs read done; unexpired, non-abandoned durable turn leases count as live; gateway routing scans are cached per connection and revision. |
| [Remote notices: replica observer change keys](kanban-remote-notice-sweep.md) | 1563 | Retained read-only SQLite observers key the replica on data_version, generation and file identity, so idle notice polls skip both aggregate scans. |
| [Retry a pre-admission storage_unavailable refusal](kanban-worker-requeue-and-crash-records.md) | 1564 | A pre-admission `storage_unavailable` refusal is retried; after 180 s the run exits owner-unavailable and is requeued without a failure; post-admission refusal stays final. |
| [Sandbox startup-proof fenced cleanup](mirror-ci-follow-ups-1428.md) | 1565 | Startup-proof cleanup uses a subreaper ownership fence plus lock-acknowledged checkpoint disarming (CI workflow and fixture tests). |
| [Shared blocker home-authority fence](kanban-shared-blockers.md) | 1567–1568 | Only a card's home gateway releases cleared shared blockers or acts on a stranded-probe election win; executor copies keep local wake checks; 1568 is a test-fixture fix. |
| [Unittest live-home guard and config-backup anchor](config-backups-live-home-guard.md) | 1573–1575 1577 | `python -m unittest` refuses the live/profile Hermes home; good config backups keep one last-full anchor after a collapse (BOM-tolerant, narrower exceptions). |
| [Provider walls: default-tier fallback and live pool walls](kanban-provider-quota-walls.md) | 1578–1579 | Wall re-tier tries the strong tier when the default provider is walled; live OAuth pool exhaustion counts as a wall at claim; probe errors log a traceback. |
| [Pushed OAuth tokens keep live quota marks](credential-pool-norefresh-adopt.md) | 1580 | Adopting a pushed no-refresh token keeps a live 429 exhaustion/reset and model cooldowns; refresh/select do not lease the walled row. |
| [Upstream-conflict owner card in a fresh interpreter](upstream-sync-refused-diagnostics.md) | 1581 | `hermes update` files the upstream-merge-conflict card in a child process pinned to `HERMES_HOME` and prints "Repair owner: NOT FILED" when none is filed. |
| [CI timings regression attribution](ci-timings-regression-warning.md) | 1582 | A CI wall-time regression stays a Warning only when the PR's own test files or added cases explain it; otherwise Info. |
| [Boot-time project tree prewarm](annotation-prewarm.md) | 1583 | The boot prewarm thread builds `/api/profiles/projects/tree` through the route's single-flight cache right after the annotation prewarm, so the first Desktop reconnect reads a warm tree. |
| [1583-entry mirror verification](mirror-1583-verification.md) | verification report | Exact inventory, complete six-bridge replay, empty duplicate entry 1541 and normalized tree. |

## Mirror follow-ups 1496–1539

| Feature | Patches | Scope |
|---|---|---|
| [Stored chat outcome](session-outcome.md) | 1496 | An agent-init failure in a TUI turn writes one redacted `failed_turn` boundary, so the chat reads `turn_failed`, not `interrupted`. |
| [Runtime merge test ports](mirror-runtime-merge-test-ports.md) | 1497 | The browser-hint keybind test imports at collection time, with the host staged in `vi.hoisted` (test-only). |
| [Kanban assignment capabilities](kanban-assignment-capabilities.md) | 1498 | An assignment block asking to edit another card's model/provider pin is refused as an execution-authority mismatch before supervisor routing. |
| [Kanban supervisor requests](kanban-supervisor-requests.md) | 1499 | New `archive` op: the executor archives an unstarted orphaned descendant (up to 3 creator hops, intermediate cards finished). |
| [Cross-gateway card wait](kanban-wait-replica.md) | 1500 | `kanban_wait_check task` falls back to the Board sync shadow for an evicted foreign copy; a live row still wins. |
| [Worker transcript notify guard](kanban-worker-transcript-notify-guard.md) | 1501 | Headless worker sessions are never subscribed, inherited, shepherd-linked or written to and woken by notice sweeps. |
| [Catalog CI isolation](mirror-ci-follow-ups-1428.md) | 1502 | Real-bubblewrap regression: cancellation during sandbox startup reaps the namespace init before cleanup (test plus workflow). |
| [Required macOS state lane](ci-macos-state-required.md) | 1503–1505 | Selected PRs call the macOS state lane from CI, with a fail-closed required check, a one-shot runner-loss rerun, UTF-8-safe inputs and a `ci-fast` consumer. |
| [Kanban create overlap targets](kanban-create-overlap-targets.md) | 1506 | create_overlap admits cards with a different work role (implement/measure/deliver) and a recovery card for a failed source card. |
| [Unresolved dependency parents](kanban-dependency-block-unresolved-parent.md) | 1507–1508 | Dependency blocks and links refuse a parent with no local or replica state, roll back atomically, and name the cross-gateway card-wait remedy. |
| [Worker DarkWake recovery](kanban-worker-darkwake-recovery.md) | 1509 | Suspend-caused stale kills skip the breaker, in-place recovery waits for a lapsed lease to be renewed, and a booked exit 75 is left to the crash sweep. |
| [Kanban sync links and placement](kanban-sync-links-and-placement.md) | 1510 | Each sync round restores this gateway's own chat links on picked-up foreign copies from the replica, within tombstone, outbox and cursor guards. |
| [Provider-scoped shared blockers](kanban-shared-blockers.md) | 1511–1514 | Scope metadata through block/continue/schedule, conservative route evidence, admission receipts, fixture lint and logged fallback/refactor follow-ups. |
| [One delayed automation replay](automation-replay.md) | 1515 | Coalesced single retry of failed notices/job reports and failed automation wakes, with truthful failure copy and bounded busy rechecks. |
| [Required macOS state validation and runner-loss recovery](ci-macos-state-required.md) | 1516–1519 | Repeated source commits for selected-lane gating/recovery, portable encodings, ci-fast consumer and rewriter preservation of always(). |
| [Bounded session annotation refresh](kanban-session-poll-coalescing.md) | 1520 | Initiating caller can serve a dated previous snapshot while a slow refresh completes off-thread. |
| [Validate where a remote card runs](mirror-0922-fix-kanban-check-requires-where-runs-on-lands-the-card-never-refuse-a-review-verdict.md) | 1521 | Remote-only creation skips irrelevant local profile checks; landing capabilities and explicit concrete-pin provider requirements remain. |
| [Ended-run scope evidence after eviction](managed-worker-run-scope.md) | 1522 | Transactional machine-local tombstones retain cleanup evidence without treating absence as completion. |
| [Retire cleared queued hold evidence](kanban-shared-blockers.md) | 1523 | One clear marker for ready/review cards before admission, plus historical diagnostic wording and event-id tie-breaking. |
| [Control latency and loop diagnostics](gateway-control-diagnostics.md) | 1524–1526 | Queue/run/total timing, sanitized blocked-loop snapshots, listener-safe diagnostic failure handling and parser recursion fallback. |
| [Background lifecycle test synchronization](background-lifecycle-tests.md) | 1527–1528 | Wait for the curator's final summary and allow poller scheduling gaps while retaining thread-exit assertions. |
| [Expire ownerless shared walls](kanban-shared-blockers.md) | 1534 | Open requires newest assertion after clearing and a live owner; completed/handoff metadata never asserts. |
| [Turn reset skips half-imported modules](turn-reset-partial-import.md) | 1529 | A reused gateway turn worker's reset reads `tools.interrupt` / `tools.terminal_tool_sudo` names with `getattr` and skips a module another thread is still importing, instead of dying with `AttributeError: partially initialized module`; source `dee20bcec75f`. |
| [Kanban runtime follow-ups: home-only recovery requester](mirror-kanban-follow-ups-1428.md) | 1530 | `ensure_recovery_cards` skips foreign (worker-node copy) targets, so a `continuation_exhausted` park gets one Board-wide recovery card created on the card's home; source `7dc60085a40a`. |
| [Desktop widget `hermes.send` rules](desktop-widget-send-rules.md) | 1531 | The desktop hint gives agents the 500-char / 1-per-second intent limits, the ack/retry/chunking rules and the no-browser-storage rule (tests read the limits from the renderer); refused `too_long`/`invalid`/`undelivered` intents show a notice under the frame; source `c3505ece7d9c`. |
| [Trusted producer completions for unrouted inline sessions](producer-completion-ingress.md) | 1532 | `admit_producer_completion` durably queues one `user-terminal` completion per identity in the profile store; only the owning inline runtime (chat poller or cold-chat sweeper) delivers it as one `process_complete` notice turn, without takeover or `@` expansion; there is no production caller yet; source `c4565ec4d728`. |
| [PR guard handoff: ordering dependency parks](kanban-pr-park-handoff.md) | 1533 | An owner's fenced `dependency_wait` after its PR comment releases the `active_pr` hold for one continuation once the parents finish; crashes, operator parks or links, removed edges and review verdicts stay guarded; source `197c0a6bfafe`. |
| [Managed crash output and refusal receipts](kanban-managed-crash-output-receipt.md) | 1535 1536 1537 1538 | A managed run's crash output comes only from its own `worker_result` receipt, never the shared log; the refused submitter books the owner's refusal as a fenced receipt under its own pid, so owner death is classified `owner_restart_lost`; a failed booking keeps the exit code and prints the exception type only; sources `fc1ad576793e`, `c185ad7a11ac`, `fb794abb7166`, `8688b266362e`. |
| [Refused-turn replay: settled terminal verdict](mirror-runtime-hardening-1302.md) | 1539 | `nonretryable_client_error_result` always reports `failure_retryable: False`, so a turn that settlement rejected (for example a local `ValueError`/`TypeError` that the classifier first called retryable) is not replayed through auto-continue; source `37c70bbcaa1c`. |

## Mirror follow-ups 1493–1495

| Feature | Patches | Scope |
|---|---|---|
| [Board-targeted sync](board-targeted-sync.md) | 1493–1495 | Feed-directed rounds, fixed full recovery, change-keyed outbox reads and metrics integration. |
| [1495-entry mirror verification](mirror-1495-verification.md) | verification report | Exact inventory, complete four-bridge replay and normalized tree. |

## Mirror follow-ups 1485–1492

| Feature | Patches | Scope |
|---|---|---|
| [Round-scoped replica lifetime](sync-round-replica-lifetime.md) | 1485–1486 | Reuse the pulled connection for repairs; isolated replay fixtures. |
| [Discord stand-in rate expiry](discord-standin-rate-expiry.md) | 1487 | Real SDK cancellation recovery for channel and typing responses. |
| [Selective rejection resolution](kanban-central-service.md) | 1488–1492 | Authoritative per-row proof and receipts, settled-state vetoes, CLI dirty-to-clean and translated run references; fleet rollout still staged. |
| [1492-entry mirror verification](mirror-1492-verification.md) | verification report | Exact inventory, complete four-bridge replay and normalized tree. |

## Mirror follow-ups 1479–1484

| Feature | Patches | Scope |
|---|---|---|
| [Human asks that resolve outside Hermes](kanban-ask-resolved-check.md) | 1479, 1480, 1482, 1484 | `human_request.resolved_check`: filing probe on every block/create/CLI/metadata/dashboard path, `ask_resolved` sweep unblock, failure limit, synced-board probe ownership. |
| [Tests cannot open the production Kanban board](kanban-production-board-guard.md) | 1481 | Pytest-context refusal at `connect()`/`init_db()` plus wider conftest deny and isolated board fixture. |
| [Sync repair revision](kanban-sync-repair-revision.md) | 1483 | Repair scans invalidate on input-table writes, not dispatcher clock commits. |
| [1484-entry mirror verification](mirror-1484-verification.md) | verification report | Complete inventory, unchanged four-bridge replay and normalized final tree. |

## Mirror follow-ups 1477–1478

| Feature | Patches | Scope |
|---|---|---|
| [Classic title test synchronization](classic-title-test.md) | 1477 | Wait for requested title output; exercise fragmented pane observations. |
| [Link-projection dependency scope](links-fingerprint-scope.md) | 1478 | Exclude unread sync shadows from links, retain them for counts and preserve relevant-write invalidation. |
| [1478-entry mirror verification](mirror-1478-verification.md) | verification report | Complete inventory, four-bridge replay and normalized final tree. |

## Mirror follow-ups 1475–1476

| Feature | Patches | Scope |
|---|---|---|
| [State-keyed profile session scans](profiles-session-scan-cache.md) | 1475–1476 | Reuse unchanged database scans under the existing lock, without a TTL; preserve live config and annotations. |
| [1476-entry mirror verification](mirror-1476-verification.md) | verification report | Complete inventory, unchanged three-bridge replay and normalized final tree. |

## Mirror follow-ups 1470–1474

| Feature | Patches | Scope |
|---|---|---|
| [Runtime polling and teardown](mirror-runtime-follow-ups-1470.md) | 1470–1473 | Uncached aggregate scan serialization, exited-process teardown, refresh-only notice memo and state-keyed idle Board sync. |
| [Composable metadata-only ask classification](composable-metadata-ask-classification.md) | 1474 | Classification and audit comments share an outer transaction; rollback preserves metadata and execution state. |
| [1474-entry mirror verification](mirror-1474-verification.md) | verification report | Complete inventory, unchanged three-bridge replay and normalized final tree. |

## Mirror follow-ups 1428–1469

| Feature | Patches | Scope |
|---|---|---|
| [Catalog isolation and deterministic fixtures](mirror-ci-follow-ups-1428.md) | 1428, 1438–1441, 1449–1452, 1455, 1468 | Trusted-base catalog admission, sandbox subreaper acknowledgment and deterministic process/cron/quota tests. |
| [Recorded routing and runtime follow-ups](mirror-runtime-follow-ups-1428.md) | 1429, 1444–1445, 1458–1459, 1461–1462, 1467 | Recorded-route restoration, failed-build sweeping, indexed lineage, sleep observation, bounded listing, activity/liveness and reentrant WAL guard. |
| [Nightly red ownership](nightly-e2e.md) | 1430, 1432 | One red owner per alert intent and advisory pinned-reporter freshness. |
| [Composer editor Escape origin](turn-stop-notice.md) | 1431 | Real contenteditable ChatBar calls haltRun('esc'); mounted busy/idle regression complements non-editor hook. |
| [Kanban runtime follow-ups](mirror-kanban-follow-ups-1428.md) | 1433–1437, 1446–1448, 1453–1454, 1456–1457, 1460, 1464–1466 | Scoped continuation/retry requests, target-policy inspection, historical review evidence, adopted-origin notices, direct triage parking and bounded sync repair. |
| [Superseded upstream sync cleanup](upstream-sync-land-via-pr.md) | 1442–1443, 1463 | Close stale sync PRs, refuse superseded resolution restaging and lease-delete eligible orphan refs. |
| [Offline cron child dependencies](offline-dns-retry-wait.md) | 1469 | Restore test-interpreter dependency paths after real child bootstrap; no production or physical-event claim. |
| [1469-entry mirror verification](mirror-1469-verification.md) | verification report | Complete inventory, three-bridge replay and normalized final tree. |

## Mirror follow-ups 1416–1427

| Feature | Patches | Scope |
|---|---|---|
| [Checkpoint reclamation](mirror-runtime-follow-ups-1416.md) | 1416 | Reclaim pending size work with auto-prune disabled; niced maintenance and daily over-cap health error. |
| [New-session plugin refresh](mirror-runtime-follow-ups-1416.md) | 1417–1419 | Refresh changed enablement at agent build; post-pass cache signature and empty-root equivalence. |
| [Advisory BuildKit cache exports](mirror-runtime-follow-ups-1416.md) | 1420 | Ignore optional cache-export failure, not build failure. |
| [Managed worker warning synchronization](mirror-runtime-follow-ups-1416.md) | 1421 | Await asynchronous warning publication in the lineage regression. |
| [Routeless heavy-work placement](mirror-runtime-follow-ups-1416.md) | 1422–1424 | Route portable heavy work while preserving explicit pins and host-bound work. |
| [Offline DNS retry wait](offline-dns-retry-wait.md) | 1425–1427 | Cumulative per-request awake-time DNS wait, bounded probes, interrupts and cron error metadata. |

## Mirror follow-ups 1382–1415

| Feature | Patches | Scope |
|---|---|---|
| [Cron fixture cleanup scope](kanban-claim-scope-and-review-fixes.md) | 1382 | Test tool/CLI creator-scoped fixture archive authority, source `88e03dbdf9` |
| [Nightly E2E acceptance markers](nightly-e2e-acceptance-markers.md) | 1383 | Name tested refs and scope acceptance fixture alerts; source `ba77f929233d` |
| [Nightly E2E production isolation](nightly-e2e-acceptance-markers.md) | 1384 | Preserve pending main incidents and strip inherited creator identity; source `da4cba694371` |
| [Nightly E2E owner-run pairing](nightly-e2e-acceptance-markers.md) | 1385 | Pair fixture owner task with its live run and strip ordinary-create run identity; source `8092734fd79e` |
| [Nightly E2E fixture requirements](nightly-e2e-acceptance-markers.md) | 1386 | Declare fixture agent creates with --no-requires; source `8d9612cd7f24` |
| [Inherited enclosing run scope](kanban-claim-scope-and-review-fixes.md) | 1387 | Accept a submitter's mismatched run scope only when shared by the gateway, source `154de342db` |
| [Receipt-gated notify reconcile](kanban-notify-reconcile.md) | 1388 | `reconcile_refused_sub` retires wrong-local refused-send tui subs only on exact owner transcript receipt, source `f8c39f0e` |
| [Review change-request handback](kanban-claim-scope-and-review-fixes.md) | 1389 | Recognize completed change-request evidence for one authenticated PR repair continuation, source `801824d02a` |
| [Nightly E2E current-run selection](nightly-e2e-acceptance-markers.md) | 1390 | Select the show payload current run, refuse ambiguity, and skip dotenv-dependent tests when absent; source `85ca95df9102` |
| [Flaky test docstring ordering](nightly-e2e-acceptance-markers.md) | 1391 | Restore docstring before dependency skip in the CLI board regression; source `12369ec78f7c` |
| [Kanban show current run JSON](nightly-e2e-acceptance-markers.md) | 1392 | Serialize current_run_id with an in-process real show regression; source `e205e8367bdb` |
| [Upstream-sync refusal diagnostics](upstream-sync-refused-diagnostics.md) | 1393 | Name required jobs/tests and compare exact main-baseline evidence without changing REFUSED; source `a219c0c52cae`. |
| [Upstream-sync evidence selection](upstream-sync-refused-diagnostics.md) | 1394 | Prefer main first parent, share baseline discovery and distinguish main-set ownership; source `f8989e8d7313`. |
| [Upstream-sync owner matching](upstream-sync-refused-diagnostics.md) | 1395 | Match CLI-shaped nightly cards, keep incomplete logs unknown and budget log reads; source `38b04a4a255c`. |
| [Updater refusal visibility](upstream-sync-refused-diagnostics.md) | 1396 | Preserve gate verdict under diagnostics, tolerate log timeouts and validate state maps; source `b8600f6f813a`. |
| [Updater evidence-first report cap](upstream-sync-refused-diagnostics.md) | 1397 | Drop test-ID listing lines before evidence when the gate report exceeds its cap; source `2947ddb1f550`. |
| [Claim UID and parked completion](kanban-claim-scope-and-review-fixes.md) | 1398 | Keep lease/run identity through capture gaps and force-complete scheduled cards with archive forwarding, source `097b54f5e2` |
| [Reader recovery observation order](kanban-claim-scope-and-review-fixes.md) | 1399 | Snapshot handles before reading live shared-memory identity, source `c6acc8f8e4` |
| [create_overlap compares targets](kanban-create-overlap-targets.md) | 1400 | `kanban_create` overlap check vetoes matches naming a different repo, host, process or inverted-check revision; waiter exemption; one read per pinned board file, source `f5a83d2f11` |
| [create_overlap target veto narrowed](kanban-create-overlap-targets.md) | 1401 | Drops ticket-number veto; whole-token hosts, gateway/pid-only process, stricter repo/inversion/waits_on rules, source `13205355ab` |
| [create_overlap ignores ports, gpu indices, repo asides](kanban-create-overlap-targets.md) | 1402 | Ports, `gpuN`, `metta-3` spelling and one-off body repo mentions no longer distinguish targets; wider negated waits, source `29b193a1dc` |
| [OSC11 straggler test deflake](mirror-runtime-follow-ups-1382.md) | 1403 | Straggler written from the tcsetattr restore, no sleep race; `20f46da` |
| [Archive warnings use live task read](mirror-runtime-follow-ups-1382.md) | 1404 | Fresh rows claiming open Kanban work are re-read live before the archive warning; `3ee37f9` |
| [Windows attach e2e upload non-blocking](mirror-runtime-follow-ups-1382.md) | 1405 | Failure-artifact upload continue-on-error, 1-day retention; `88a09ef` |
| [Artifact uploads never decide a check](mirror-runtime-follow-ups-1382.md) | 1406 | Test pins continue-on-error uploads and no download-artifact behind the required gate; `5286331` |
| [Retired worker generation logs a warning](mirror-runtime-follow-ups-1382.md) | 1407 | Interrupted-turn stale_generation and lease release log WARNING, not ERROR; `4885a5e` |
| [Machine-pin create explanation](kanban-claim-scope-and-review-fixes.md) | 1408 | Preserve machine-specific Board-lease reason in tool create placement output and logs, source `e3bd1c8cd1` |
| [Checkpoint size cap touches only this project](mirror-runtime-follow-ups-1382.md) | 1409 | Take-path cap drops one snapshot of this project per pending gc; stale-gc warning; `5437847` |
| [Admission rides out a busy update lock](mirror-runtime-follow-ups-1382.md) | 1410 | AdmissionBusy retry up to 0.5 s; holds still refuse at once; `84e2354` |
| [Sidebar lists this Mac's chats from registry primary](mirror-runtime-follow-ups-1382.md) | 1411 | Own recents read from the registry primary, passive for local; `d86463b` |
| [Reconcile_notify supervisor op](kanban-notify-reconcile.md) | 1412 | Worker-filed `reconcile_notify` op; non-worker executor reads owner receipts via hermes-hub, two-phase, source `d739a5e2` |
| [Reconcile executor hub_cli_path](kanban-notify-reconcile.md) | 1413 | Executor resolves hermes-hub through `hub_cli_path()` instead of `HERMES_HUB_BIN`, source `816682ae` |
| [Reconcile executor lockguard-exempt](kanban-notify-reconcile.md) | 1414 | Marks executor's read-only `state.db`/`hub.db` opens `noqa: kanban-lockguard`, source `2b855ee2` |
| [Handshake-timeout race logs INFO](websockets-keepalive-logging.md) | 1415 | `unexpected internal error` + `EOFError("stream ended")` from a connect() timing out mid-handshake becomes one INFO line; other exceptions keep ERROR; `_HANDLED` is now message -> predicate, source `160518392a`. |

## Mirror follow-ups 1376–1381

| Feature | Patches | Scope |
|---|---|---|
| [Restricted OS-lane skips](fork-ci-hardening.md) | 1376–1380 | Guard real merge failures; retain only known upstream OS-only predicate skips; writer and checked-in hook stay identical. |
| [Preflight SIGTERM survives callback swallowing](mirror-ci-hardening-1303.md) | 1381 | Signal flag checked by copy loops; real weakref-callback subprocess regression. |

## Mirror follow-ups 1369–1375

| Feature | Patches | Scope |
|---|---|---|
| [Canonical served identity in chat replies](canonical-served-identity.md) | 1369 | Source `970d5e40c12`; implementation and source test inventory in the feature doc. |
| [Serialized disk-cleanup tracked writers](disk-cleanup-tracked-writers.md) | 1370 | Source `12d643cf97a`; implementation and source test inventory in the feature doc. |
| [Durable overlap refusal explanations](overlap-durable-refusal.md) | 1371 | Source `551588c6f57`; implementation and source test inventory in the feature doc. |
| [Managed worker run scope and aggregate memory bound](managed-worker-run-scope.md) | 1372 | Source `cb4ac494b00`; implementation and source test inventory in the feature doc. |
| [Forwarded-card wait survives replica lag](forwarded-card-replica-lag.md) | 1373 | Source `5dc666b7b97`; implementation and source test inventory in the feature doc. |
| [Detached forks skip pre-final gates](fork-pre-final-gates.md) | 1374 | Source `57b10c9efd7`; implementation and source test inventory in the feature doc. |
| [Recovery probe waits for dispatcher admission](recovery-admission-test.md) | 1375 | Source `93843e3a493`; implementation and source test inventory in the feature doc. |

## Mirror follow-ups 1302–1326

| Feature | Patches | Scope |
|---|---|---|
| [fix(cron): defer due jobs under the core-update drain hold; cut cron runs in an update restart past hermes-sync's cron bound ](mirror-runtime-hardening-1302.md) | 1302 | Source `ead069dd57`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(tests): sigterm-mid-copy preflight test outlasts starved runner; lock wait configurable for the standalone script](mirror-ci-hardening-1303.md) | 1303 | Source `7f5538e27a`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(tests): pass lock wait by argv, not env](mirror-ci-hardening-1303.md) | 1304 | Source `49f25091b1`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(notices): a blocked notice whose card moved on never wakes a close-out turn ](mirror-runtime-hardening-1302.md) | 1305 | Source `e555afdb33`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(kanban): restore the code-health caps #318 merged past; dispatcher entry test for forwarded owner archives  (#328)](mirror-ci-hardening-1303.md) | 1306 | Source `5be6ef3079`; behavior, implementation and source test inventory in the linked feature section. |
| [ci(upstream-sync): run all eight Python slices; two slices hit tests.yml's 30 min timeout](mirror-ci-hardening-1303.md) | 1307 | Source `0a91e5a5cd`; behavior, implementation and source test inventory in the linked feature section. |
| [hosts/hermes-ci-runners: runner AMI source and probe (#132)](mirror-ci-hardening-1303.md) | 1308 | Source `50731967b2`; behavior, implementation and source test inventory in the linked feature section. |
| [test(js): symlinked workspace-check CLI test no longer assumes apps/desktop is installed (#341)](mirror-ci-hardening-1303.md) | 1309 | Source `bbae9719e0`; behavior, implementation and source test inventory in the linked feature section. |
| [test(web): avoid repeated library checks in freshness fixture ](mirror-runtime-hardening-1302.md) | 1310 | Source `20c094bf67`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(kanban): a frozen legacy alias no longer restores links on an open card ](mirror-runtime-hardening-1302.md) | 1311 | Source `7af0b82411`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(kanban): an external gateway drain holds dispatcher claims like a deferred restart ](mirror-runtime-hardening-1302.md) | 1312 | Source `ddc3f813f3`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(kanban): a worker may archive a done card its own task created ](mirror-runtime-hardening-1302.md) | 1313 | Source `bb08052cd8`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(kanban): archiving a placement-forwarded card archives its remote copy ](mirror-runtime-hardening-1302.md) | 1314 | Source `b357101aee`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(sessions): a lost request outranks live work, a cron is not a wait, a refused turn is replayed ](mirror-runtime-hardening-1302.md) | 1315 | Source `2185d7299c`; behavior, implementation and source test inventory in the linked feature section. |
| [feat(terminal): kubernetes backend — pod per session, existing-claim workspace  (#242)](mirror-runtime-hardening-1302.md) | 1316 | Source `a969258dc2`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(desktop-e2e): preserve core sandbox run ownership  (#352)](mirror-ci-hardening-1303.md) | 1317 | Source `6f476918c3`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(dashboard-auth): let core session-header service calls pass the gated dashboard ](mirror-runtime-hardening-1302.md) | 1318 | Source `b99ddb8156`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(kanban): order respawn holds by event occurrence ](mirror-runtime-hardening-1302.md) | 1319 | Source `397d5e3077`; behavior, implementation and source test inventory in the linked feature section. |
| [ci(js-tests): run the Prebuilt TUI install-shape checks on frontend PRs ](mirror-ci-hardening-1303.md) | 1320 | Source `5974f72bb3`; behavior, implementation and source test inventory in the linked feature section. |
| [ci(js-tests): probe temp-dir fsync and cgroup PSI during workspace checks (#335)](mirror-ci-hardening-1303.md) | 1321 | Source `35b214a8ee`; behavior, implementation and source test inventory in the linked feature section. |
| [test(acp): tolerate exiting MCP peers during liveness scans (#361)](mirror-ci-hardening-1303.md) | 1322 | Source `abf5f8578a`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(kanban): archive a placed copy whose remote id resolves after its card is archived ](mirror-runtime-hardening-1302.md) | 1324 | Source `96698fb109`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(worker): derive the worker receipt bound from the owner's write patience; log slow worker.persist ](mirror-runtime-hardening-1302.md) | 1325 | Source `01a8fe0b30`; behavior, implementation and source test inventory in the linked feature section. |
| [fix(worker): WorkerRPC connect steps share the owner-stall budget, not fixed 5 s deadlines ](mirror-runtime-hardening-1302.md) | 1326 | Source `ff727b789d`; behavior, implementation and source test inventory in the linked feature section. |

## Frozen-decision reset and fresh-tip follow-ups

| Feature | Patches | Scope |
|---|---|---|
| [Frozen decisions stop at reset](frozen-decision-reset.md) | 1239 1273 1301 | Exact two-part user-steps decisions survive compression but cancel at a genuinely new LOCAL conversation, with event/waiter settlement. 1301 keeps the old owner's lock past a cancelled caller in the native rehome branch. |
| [Admission, restart and test isolation](mirror-admission-and-test-followups.md) | 1236–1238 1240–1253 | Memory-aware CI, drain/crash isolation, cron and replica guards, bounded restart settlement, missing pins and source test synchronization. |

## Append-only follow-ups after 1134

| Feature | Patches | Scope |
|---|---|---|
| [Bounded update pauses and pending clarification](update-pause-followups.md) | 1135 1143 | Pause cap and clarify withdrawal. |
| [Stable discovery records](plugin-metadata-fingerprint.md) | 1137 1138 | Ignore metadata-only ctime; preserve inode invalidation. |
| [CI prerequisites](ci-planner-and-tmux.md) | 1139 1156 | Planner source graph and test-job tmux. |
| [Runtime test synchronization](runtime-test-synchronization.md) | 1136 1140 1146 1147 1148 1155 1157 | Event-based tests and current gateway fixtures. |
| [Annotation prewarm](annotation-prewarm.md) | 1141 | Read the session-list annotation during serve boot. |
| [Source tags and class routing](session-source-class-routing.md) | 1142 1144 1149 1152 1153 | Refusal fields, tool labels, worker and CLI class policy. |
| [Remote notice acknowledgement evidence](remote-notice-ack-evidence.md) | 1145 | Distinguish keyed delivery from stale settlement. |
| [Worker capability relay](worker-capability-relay.md) | 1150 | Route needs through the card's home gateway. |
| [Managed worker environment identity](managed-worker-env-identity.md) | 1151 | Recognize the bound live worker ancestor. |
| [Custom-label launchd gateway](custom-label-launchd-gateway.md) | 1154 1193 1194 1195 | Exact-home supervisor discovery, detached restart and manual-owner handback; a supervised start attaching to a manual owner of its own home records a refusal and hands the home back, and `hermes update` returns a manual gateway to the KeepAlive job serving its home. |

## Newly reconciled feature and compatibility entries

These rows document the 67 source commits absent from the previous 1067-entry mirror. Existing rows below were renumbered by source SHA, not by filename slug.

| Feature | Patches | Scope |
|---|---|---|
| [Owner execution](mirror-kanban-owner-execution.md) | 0982 1022 1027 1028 | Owner model-tier policy, safe worker imports and routing fixtures. |
| [Kanban merge compatibility](mirror-kanban-merge-compatibility.md) | 0993 1031 1035 1040 | Capability, recovery and managed-worker-loss fixture contracts. |
| [Local placement reasons](mirror-kanban-placement-reasons.md) | 1112 | Local-result explanations and alternate-placement advisory. |
| [Gateway-aware comment waits](mirror-kanban-comment-waits.md) | 1119 1120 | Comment origin, wait-comment, stale probes and extracted queries. |
| [Cold plugin records](mirror-kanban-cold-plugin-records.md) | 1121 1130 | Hook-presence and capability records avoid unnecessary discovery/auth probes. |
| [Obsolete remote claims](mirror-kanban-stale-notice-claims.md) | 1131 | Settle dead-holder claims after replica state changes. |
| [Human-request guidance](mirror-kanban-human-request-guidance.md) | 1133 | Structured human asks and verified continuation-exhaustion routing. |
| [Merge renames and integration](mirror-runtime-merge-renames.md) | 0981 0994 1037 1051 1056 1058 1062 | Upstream APIs, safe spawns, build stamps and compatibility fixes. |
| [Worker and config isolation](mirror-runtime-worker-isolation.md) | 0988 0990 0991 0992 1099 1125 | Managed store/config ownership, cron verdicts and checkpoint lock recovery. |
| [Session moves and delegation](mirror-runtime-session-move-delegate.md) | 1034 1042 1043 1044 | Stop identity, re-import, moved-session exit and explicit child pins. |
| [Desktop sidebar stamps](mirror-runtime-desktop-sidebar-stamps.md) | 0984 1113 1114 1124 | Project defaults and owner-authoritative replica activity. |
| [CI and health caps](mirror-runtime-ci-health-caps.md) | 0983 1052 1054 1127 | Concurrency, setup-error counts, catalog descriptions and module extraction. |
| [Merge test compatibility](mirror-runtime-merge-test-ports.md) | 0987 0989 1018 1023 1025 1026 1029 1030 1032 1036 1038 1041 1045 1047 1049 1057 1115 1122 1126 1129 | Source test adaptations; no blanket fresh-test claim. |
| [Deleted working directory](mirror-runtime-deleted-cwd.md) | 1118 | Execute-code and file tools tolerate a removed process cwd. |
| [Background review exit](mirror-runtime-cli-background-review-exit.md) | 1123 | Cancel CLI background review on exit and omit it in one-shot mode. |
| [Startup performance](mirror-runtime-startup-perf.md) | 1116 1117 1128 | Parent-marker checks and provider AST fast paths. |
| [Restart notification opt-out](mirror-runtime-restart-notice-optout.md) | 1132 | Re-read an explicit opt-out at shutdown. |
| [Session probe copies](sessions-branch-probe.md) | 1134 | Standalone hidden root copy; original transcript remains untouched. |

## Merge-aware mirror verification

Documentation coverage (`python3 -B core/check_coverage.py`, the pre-commit gate) also refuses
two staged patches with the same first-line SHA header, naming both ordinals. It needs no source
repository, so it cannot see omitted commits; the verifier below does.
It is not a source-equivalence check. Before publishing a new series, separately run:

```sh
python3 -B core/verify_mirror.py \
  --source-repo /path/to/source-repo \
  --source-tip FROZEN_FORK_COMMIT \
  --base FROZEN_UPSTREAM_COMMIT \
  --patch-dir core/patches \
  --policy core/mirror-policy-NNNN.json
```

For the 1469 snapshot, also pass `--replay-plan core/mirror-replay-1469.json` and use `--policy core/mirror-policy-1469.json`.
This explicit opt-in pins the historical replay start and a bridge's boundary by source SHA, its source merge and its diff by SHA-256. Starts and merge IDs must be full commit IDs; merge ancestry, boundary membership and unique boundaries are validated. Bridge paths are relative to the plan, without parent traversal. Each uninterrupted patch segment is replayed in published order, then the bridge is applied with `git apply --index --3way` and committed. No source patch is skipped; final tree comparison is unchanged. The merge label documents provenance; the diff hash binds the reviewed public bridge (which may contain normalization), not an automatically regenerated source diff.

Exit 0 is the acceptance step for every mirror pass; record its JSON line in the snapshot's
`mirror-NNNN-verification.md`. Use frozen commit IDs, not moving refs, in publication evidence. `--base` is the
upstream exclusion tip for `base..source-tip`; by default replay starts at its merge-base
with the source tip, unless an explicit snapshot-bound replay plan supplies a historical start. The verifier
resolves inputs once and checks the inventory: patch ordinals contiguous from
0001, every patch's first line a `From <sha>` header, no header repeated, and the
header set equal to the set of `git rev-list --no-merges base..source-tip`. A
duplicate names both ordinals and an omission names the missing source commits.
The order of the series is not compared with rev-list order, since published
ordinals never move and merged-branch commits are appended after later-dated
mainline ones. It then applies all mail patches in published order with
`git am -3 --empty=keep`, and checks the
complete committed tree (paths, modes, object kinds and blob content). Successful
application alone cannot hide a merge-only runtime resolution or a removed test.
It verifies final source equivalence, not each intermediate commit's contents or
commit metadata: curated merge-linearization overlays remain supported.
The source repository must hold every blob the curated overlay patches name in their
`index` lines (0378 is the first): a fresh clone of the fork lacks some of them, and
`git am -3` then stops with "sha1 information is lacking". Point `--source-repo` at the
long-lived fork checkout, or give a scratch clone its objects as an alternate
(`echo <checkout>/.git/objects > <clone>/objects/info/alternates`).

The source checkout HEAD is never changed. A uniquely owned detached worktree is
removed in `finally` on replay/comparison success or failure; other worktrees are
not pruned. Removal retries once. If Git persistently refuses removal, the command
fails and preserves the worktree and its registry entry together for recovery via
`git -C /path/to/source-repo worktree list`. Git diagnostics and source contents
are never printed by the CLI; success prints JSON with frozen IDs and patch count.
The source repository and public patch directory must be trusted local inputs;
this is not a sandbox for repository-configured filters or hostile patches.

Default comparison is byte-exact. The historical 0960 series includes public
substitutions and reviewed ordering/formatting differences, so it is not expected
to pass byte-exact comparison without an explicitly reviewed policy. This tooling
change does not certify that snapshot with a new policy or weaken the default.
For approved substitutions, supply `--policy /path/to/public-policy.json`:

```json
{
  "source_tip": "FULL_FROZEN_SOURCE_COMMIT_ID",
  "base": "FULL_FROZEN_UPSTREAM_COMMIT_ID",
  "substitutions": [["original-example", "public-example"]],
  "fingerprints": {}
}
```

Substitutions are literal UTF-8 source-to-public byte replacements, applied in
listed order to differing blobs, never regexes, paths, modes or missing files.
Keep patterns narrow and review their scope. Never generate rules by reading a
private denylist or put private denylist values into public output. All policy
keys are required, with exact resolved snapshot IDs. A historical ordering-only
difference may instead use `"path/to/file": ["SOURCE_BLOB_ID", "APPLIED_BLOB_ID"]`
inside `fingerprints`. Both complete Git blob IDs must match, file modes must
match, and every fingerprint must be used; an altered blob or stale snapshot
fails. Fingerprints require independent review for semantic equivalence: they
are exact reviewed pairs, not a semantic-equivalence algorithm or path exemptions.

A public rename of a file whose source name is private (the contributor-marker files
under `contributors/emails/`) goes in the optional `renames` list:
`{"applied": "public/path", "source_blob": "BLOB_ID", "applied_blob": "BLOB_ID"}`.
The rule names only the public path. Its source side is the one source-only path in the
same directory with the same mode and exactly `source_blob`, so no private file name enters
the policy. Every rule must be used, and any source-only or applied-only path left unpaired
still fails as a path-inventory difference.

Run the stdlib-only fixture suites (also used by the core-mirror CI workflow):

```sh
python3 -B -m unittest discover -s core -p 'test_*.py' -v
```

The fixtures exercise faithful replay, missing and duplicated headers (naming both
ordinals), headers outside the source range, omitted commits (named), a merged-branch
commit appended after later-dated mainline commits (passes), the same series with that
slot holding a duplicate or left out (fails), missing/duplicate/gapped ordinals, omitted merge-only runtime resolution, deleted
regression tests despite successful replay, explicit directional privacy rules,
snapshot-bound fingerprints and altered content, content-pinned public renames, malformed policies, file modes,
advanced upstream bases, CLI privacy, and cleanup after replay/comparison failure.
`test_check_coverage.py` covers the pre-commit gate: duplicate staged SHA headers fail
naming both ordinals, the staged copy is what counts, and uncovered patches still fail.
Cleanup fixtures preserve source HEAD and a sibling worktree, inject transient
and persistent removal errors, and explicitly clean up their own recovery fixture.
No private repository, network, runtime change or third-party Python package is
needed. Real source snapshot verification is an additional publisher-run gate,
not something the public fixture CI can perform without the source repository.

## Features

| Doc | Patches | What you get |
|---|---|---|
| [mirror-0489-chore-i18n-regenerate-desktop-key-manifest-for-shell-statusbar-modelloading-inherited.md](mirror-0489-chore-i18n-regenerate-desktop-key-manifest-for-shell-statusbar-modelloading-inherited.md) | 0489 | `f845189101` — chore(i18n): regenerate desktop key manifest for shell.statusbar.modelLoading (inherited from main 3232b40b24) |
| [mirror-0638-fix-kanban-children-inherit-only-owning-notify-subs-never-shepherd-touch-observers.md](mirror-0638-fix-kanban-children-inherit-only-owning-notify-subs-never-shepherd-touch-observers.md) | 0638 | `684b28d63b` — fix(kanban): children inherit only owning notify subs, never shepherd/touch observers [daveey] |
| [mirror-0646-fix-kanban-backfill-origin-shepherd-provenance-on-legacy-hub-subs-so-old-observer-cop.md](mirror-0646-fix-kanban-backfill-origin-shepherd-provenance-on-legacy-hub-subs-so-old-observer-cop.md) | 0646 | `65651a3328` — fix(kanban): backfill origin/shepherd provenance on legacy hub subs so old observer copies stop spreading [daveey] |
| [mirror-0648-fix-kanban-backfill-takes-an-ancestor-s-reason-only-from-rows-written-before-the-copy.md](mirror-0648-fix-kanban-backfill-takes-an-ancestor-s-reason-only-from-rows-written-before-the-copy.md) | 0648 | `ede1d69bd1` — fix(kanban): backfill takes an ancestor's reason only from rows written before the copy [daveey] |
| [mirror-0661-refactor-kanban-move-inherit-notify-subs-owning-sub-clause-into-kanban-db-notify-file.md](mirror-0661-refactor-kanban-move-inherit-notify-subs-owning-sub-clause-into-kanban-db-notify-file.md) | 0661 | `f9c8908642` — refactor(kanban): move _inherit_notify_subs/_owning_sub_clause into kanban_db_notify (file-size ratchet) [daveey] |
| [mirror-0686-fix-kanban-skip-legacy-hub-sub-provenance-backfill-on-boards-whose-tasks-table-lacks-.md](mirror-0686-fix-kanban-skip-legacy-hub-sub-provenance-backfill-on-boards-whose-tasks-table-lacks-.md) | 0686 | `cb30804763` — fix(kanban): skip legacy hub-sub provenance backfill on boards whose tasks table lacks created_at [daveey] |
| [mirror-0707-ci-workflow-dispatch-gets-its-own-concurrency-group-so-a-manual-gate-on-main-is-never.md](mirror-0707-ci-workflow-dispatch-gets-its-own-concurrency-group-so-a-manual-gate-on-main-is-never.md) | 0707 | `250ee8a8a8` — ci: workflow_dispatch gets its own concurrency group so a manual gate on main is never superseded by or blocking pushes [daveey] |
| [mirror-0708-fix-tests-metrics-harness-passes-on-macos-ls-and-with-the-live-venv-s-bin-on-path.md](mirror-0708-fix-tests-metrics-harness-passes-on-macos-ls-and-with-the-live-venv-s-bin-on-path.md) | 0708 | `00c401c74a` — fix(tests): [daveey] metrics harness passes on macOS ls and with the live venv's bin on PATH |
| [mirror-0709-fix-oneshot-hermes-z-keeps-writing-agent-log-silence-console-log-handlers-instead-of-.md](mirror-0709-fix-oneshot-hermes-z-keeps-writing-agent-log-silence-console-log-handlers-instead-of-.md) | 0709 | `8f03cbcee3` — fix(oneshot): [daveey] hermes -z keeps writing agent.log; silence console log handlers instead of logging.disable |
| [mirror-0710-fix-model-routing-resume-a-chat-on-the-default-when-the-policy-no-longer-approves-its.md](mirror-0710-fix-model-routing-resume-a-chat-on-the-default-when-the-policy-no-longer-approves-its.md) | 0710 | `81c2bebb12` — fix(model-routing): resume a chat on the default when the policy no longer approves its stored model [daveey] |
| [mirror-0711-fix-state-the-deleted-wal-holder-scan-always-runs-on-a-real-thread-of-its-own.md](mirror-0711-fix-state-the-deleted-wal-holder-scan-always-runs-on-a-real-thread-of-its-own.md) | 0711 | `e5dd8a90ec` — fix(state): [daveey] the deleted-WAL holder scan always runs on a real thread of its own |
| [mirror-0712-checkpoints-price-git-add-before-running-it-persist-the-slow-folder-back-off-in-the-s.md](mirror-0712-checkpoints-price-git-add-before-running-it-persist-the-slow-folder-back-off-in-the-s.md) | 0712 | `72755549d1` — [daveey] checkpoints: price git add before running it; persist the slow-folder back-off in the store |
| [mirror-0713-fix-ci-e2e-upgrade-must-not-require-hermes-hub-146.md](mirror-0713-fix-ci-e2e-upgrade-must-not-require-hermes-hub-146.md) | 0713 | `cb2e783fc5` — fix(ci): [daveey] e2e-upgrade must not require hermes-hub (#146) |
| [mirror-0714-fix-tui-an-idle-prompt-submit-refused-only-by-the-core-update-hold-is-queued-not-boun.md](mirror-0714-fix-tui-an-idle-prompt-submit-refused-only-by-the-core-update-hold-is-queued-not-boun.md) | 0714 | `b54002e704` — fix(tui): an idle prompt.submit refused only by the core-update hold is queued, not bounced with 5035 [daveey] |
| [mirror-0716-fix-checkpoints-read-the-slow-folder-back-off-as-utf-8-sig-windows-footgun-red-on-mai.md](mirror-0716-fix-checkpoints-read-the-slow-folder-back-off-as-utf-8-sig-windows-footgun-red-on-mai.md) | 0716 | `95568533b4` — fix(checkpoints): read the slow-folder back-off as utf-8-sig (Windows footgun red on main) [daveey] |
| [mirror-0717-fix-kanban-lock-safe-quarantine-ioerr-transient-quarantine-only-confirmed-corruption-.md](mirror-0717-fix-kanban-lock-safe-quarantine-ioerr-transient-quarantine-only-confirmed-corruption-.md) | 0717 | `f9b92b497f` — fix(kanban): lock-safe quarantine, IOERR transient, quarantine only confirmed corruption (#125) |
| [mirror-0719-fix-tests-pm-launch-tests-ignore-a-supervisor-s-child-marker.md](mirror-0719-fix-tests-pm-launch-tests-ignore-a-supervisor-s-child-marker.md) | 0719 | `76ff5aa9fa` — fix(tests): pm launch tests ignore a supervisor's child marker [daveey] |
| [mirror-0721-fix-update-a-fenced-core-update-defers-gateway-verification-to-the-fence-owner-instea.md](mirror-0721-fix-update-a-fenced-core-update-defers-gateway-verification-to-the-fence-owner-instea.md) | 0721 | `fc7b1ac399` — fix(update): a fenced core update defers gateway verification to the fence owner instead of owing a false restart [daveey] |
| [mirror-0722-fix-ci-windows-footguns-red-on-main-ssh-marker-prune-uses-pid-exists-bom-tolerant-rea.md](mirror-0722-fix-ci-windows-footguns-red-on-main-ssh-marker-prune-uses-pid-exists-bom-tolerant-rea.md) | 0722 | `39b6ddb29f` — fix(ci): [daveey] Windows footguns red on main: SSH marker prune uses _pid_exists, BOM-tolerant reads, explicit ps encoding |
| [mirror-0723-fix-ci-e2e-upgrade-fetches-upstream-release-tags-so-n-1-resolves-on-the-fork.md](mirror-0723-fix-ci-e2e-upgrade-fetches-upstream-release-tags-so-n-1-resolves-on-the-fork.md) | 0723 | `6725b6ff61` — fix(ci): [daveey] e2e-upgrade fetches upstream release tags so N-1 resolves on the fork |
| [mirror-0724-fix-tests-hostile-commit-e2e-frees-each-per-test-install-so-the-git-shard-fits-its-ci.md](mirror-0724-fix-tests-hostile-commit-e2e-frees-each-per-test-install-so-the-git-shard-fits-its-ci.md) | 0724 | `0f838d6b65` — fix(tests): [daveey] hostile-commit e2e frees each per-test install so the git shard fits its CI slot |
| [mirror-0725-test-serve-lifespan-shutdown-releases-the-ssh-runtime-marker.md](mirror-0725-test-serve-lifespan-shutdown-releases-the-ssh-runtime-marker.md) | 0725 | `74f0b74178` — test(serve): lifespan shutdown releases the SSH runtime marker [daveey] |
| [mirror-0726-fix-kanban-dispatch-cli-shows-at-capacity-instead-of-a-silent-spawned-0-140.md](mirror-0726-fix-kanban-dispatch-cli-shows-at-capacity-instead-of-a-silent-spawned-0-140.md) | 0726 | `9920ee6bca` — fix(kanban): [daveey] dispatch CLI shows at_capacity instead of a silent 'Spawned: 0' (#140) |
| [mirror-0727-fix-desktop-an-env-pinned-remote-client-never-spawns-a-local-backend-for-the-registry.md](mirror-0727-fix-desktop-an-env-pinned-remote-client-never-spawns-a-local-backend-for-the-registry.md) | 0727 | `b948615497` — fix(desktop): an env-pinned remote client never spawns a local backend for the registry's 'local' primary [daveey] |
| [mirror-0728-fix-kanban-sync-re-pushes-a-task-field-written-in-a-capture-gap-instead-of-diverging-.md](mirror-0728-fix-kanban-sync-re-pushes-a-task-field-written-in-a-capture-gap-instead-of-diverging-.md) | 0728 | `c36b1ef5f0` — fix(kanban): sync re-pushes a task field written in a capture gap instead of diverging silently [daveey] |
| [mirror-0729-fix-update-never-book-desktop-s-own-backend-on-the-desktop-app-s-launchd-job.md](mirror-0729-fix-update-never-book-desktop-s-own-backend-on-the-desktop-app-s-launchd-job.md) | 0729 | `2b9ed17df6` — fix(update): never book Desktop's own backend on the Desktop app's launchd job [daveey] |
| [mirror-0731-fix-kanban-refuse-claims-under-the-update-hold-before-taking-a-board-service-lease.md](mirror-0731-fix-kanban-refuse-claims-under-the-update-hold-before-taking-a-board-service-lease.md) | 0731 | `452d8e128d` — fix(kanban): refuse claims under the update hold before taking a Board-service lease [daveey] |
| [mirror-0733-test-guard-live-system-guard-sees-its-own-children-without-psutil.md](mirror-0733-test-guard-live-system-guard-sees-its-own-children-without-psutil.md) | 0733 | `a80d30f3e1` — test(guard): live-system guard sees its own children without psutil |
| [mirror-0734-fix-kanban-kanban-origin-adopt-without-courier-context-names-the-exact-hermes-hub-ado.md](mirror-0734-fix-kanban-kanban-origin-adopt-without-courier-context-names-the-exact-hermes-hub-ado.md) | 0734 | `2248ff8527` — fix(kanban): kanban_origin_adopt without courier context names the exact hermes-hub adopt-task next step [daveey] (#145) |
| [mirror-0736-feat-tui-gateway-stamp-the-last-human-typed-prompt-submit-in-desktop-last-human-input.md](mirror-0736-feat-tui-gateway-stamp-the-last-human-typed-prompt-submit-in-desktop-last-human-input.md) | 0736 | `5b3fbc10d5` — feat(tui_gateway): stamp the last human-typed prompt.submit in desktop/last_human_input.json [daveey] |
| [mirror-0737-feat-update-hermes-update-defer-desktop-swap-leaves-a-running-desktop-s-bundle-in-pla.md](mirror-0737-feat-update-hermes-update-defer-desktop-swap-leaves-a-running-desktop-s-bundle-in-pla.md) | 0737 | `a82898b665` — feat(update): HERMES_UPDATE_DEFER_DESKTOP_SWAP leaves a running Desktop's bundle in place [daveey] |
| [mirror-0738-fix-kanban-resume-delivery-after-an-exact-head-review-approval.md](mirror-0738-fix-kanban-resume-delivery-after-an-exact-head-review-approval.md) | 0738 | `e6324b7636` — fix(kanban): resume delivery after an exact-head review approval [daveey] |
| [mirror-0739-fix-kanban-serve-the-last-board-snapshot-while-a-refresh-runs.md](mirror-0739-fix-kanban-serve-the-last-board-snapshot-while-a-refresh-runs.md) | 0739 | `11fd6953d8` — fix(kanban): serve the last board snapshot while a refresh runs [daveey] |
| [mirror-0740-fix-ci-lockguard-lint-allowlists-kanban-placement-forward-s-short-lived-read-only-con.md](mirror-0740-fix-ci-lockguard-lint-allowlists-kanban-placement-forward-s-short-lived-read-only-con.md) | 0740 | `64430a72af` — fix(ci): lockguard lint allowlists kanban_placement_forward's short-lived read-only connect (red since cb924b52fd) |
| [mirror-0741-fix-state-macos-holder-scan-reads-libproc-identities-not-every-foreign-file.md](mirror-0741-fix-state-macos-holder-scan-reads-libproc-identities-not-every-foreign-file.md) | 0741 | `a408ba1b05` — fix(state): macOS holder scan reads libproc identities, not every foreign file [daveey] |
| [mirror-0742-fix-kanban-orphan-sweep-restores-a-review-run-to-review-and-skips-claims-in-flight.md](mirror-0742-fix-kanban-orphan-sweep-restores-a-review-run-to-review-and-skips-claims-in-flight.md) | 0742 | `9f23cabf55` — fix(kanban): orphan sweep restores a review run to review and skips claims in flight [daveey] |
| [mirror-0743-fix-kanban-a-park-with-blocker-id-keeps-its-wake-and-a-blocker-with-only-blocked-card.md](mirror-0743-fix-kanban-a-park-with-blocker-id-keeps-its-wake-and-a-blocker-with-only-blocked-card.md) | 0743 | `c52339ad13` — fix(kanban): a park with blocker_id keeps its wake, and a blocker with only blocked cards still gets a probe [daveey] |
| [mirror-0744-fix-kanban-placement-pin-becomes-runs-on-only-for-a-machine-that-advertises-pickup.md](mirror-0744-fix-kanban-placement-pin-becomes-runs-on-only-for-a-machine-that-advertises-pickup.md) | 0744 | `af0521714f` — fix(kanban): placement pin becomes runs_on only for a machine that advertises pickup [daveey] |
| [mirror-0745-fix-desktop-keep-remote-clients-off-background-local-spawns-149.md](mirror-0745-fix-desktop-keep-remote-clients-off-background-local-spawns-149.md) | 0745 | `4c17b356e9` — fix(desktop): keep remote clients off background local spawns [daveey] (#149) |
| [mirror-0746-fix-code-skew-pin-first-time-in-tree-imports-to-the-boot-commit-after-an-in-place-cor.md](mirror-0746-fix-code-skew-pin-first-time-in-tree-imports-to-the-boot-commit-after-an-in-place-cor.md) | 0746 | `4b29ad8e4d` — fix(code-skew): [daveey] pin first-time in-tree imports to the boot commit after an in-place core update |
| [mirror-0747-fix-update-hand-a-hermes-gateway-launcher-child-dashboard-back-to-its-launcher-never-.md](mirror-0747-fix-update-hand-a-hermes-gateway-launcher-child-dashboard-back-to-its-launcher-never-.md) | 0747 | `28936c0d97` — fix(update): hand a hermes-gateway-launcher child dashboard back to its launcher, never argv-respawn it [daveey] |
| [mirror-0748-fix-checkpoints-seed-a-new-checkout-s-first-checkpoint-from-its-own-git-index.md](mirror-0748-fix-checkpoints-seed-a-new-checkout-s-first-checkpoint-from-its-own-git-index.md) | 0748 | `64d749323e` — fix(checkpoints): seed a new checkout's first checkpoint from its own git index [daveey] |
| [mirror-0749-fix-kanban-deliver-same-profile-operator-comments-to-a-live-worker-run.md](mirror-0749-fix-kanban-deliver-same-profile-operator-comments-to-a-live-worker-run.md) | 0749 | `906cc79472` — fix(kanban): deliver same-profile operator comments to a live worker run [daveey] |
| [mirror-0750-feat-core-update-pause-auto-continuable-desktop-turns-at-a-step-boundary-for-a-drain-.md](mirror-0750-feat-core-update-pause-auto-continuable-desktop-turns-at-a-step-boundary-for-a-drain-.md) | 0750 | `cc29d783ed` — feat(core-update): pause auto-continuable Desktop turns at a step boundary for a drain window [daveey] |
| [mirror-0751-fix-desktop-a-cold-start-session-restore-never-lands-under-a-send-already-in-flight-f.md](mirror-0751-fix-desktop-a-cold-start-session-restore-never-lands-under-a-send-already-in-flight-f.md) | 0751 | `a1ea00192f` — fix(desktop): a cold-start session restore never lands under a send already in flight from the fresh chat [daveey] |
| [mirror-0752-fix-desktop-the-cold-start-restore-stands-down-once-the-user-typed-in-the-fresh-chat-.md](mirror-0752-fix-desktop-the-cold-start-restore-stands-down-once-the-user-typed-in-the-fresh-chat-.md) | 0752 | `cded83cddc` — fix(desktop): the cold-start restore stands down once the user typed in the fresh chat; e2e update harness picks the relaunched main, not its crashpad handler [daveey] |
| [mirror-0753-test-isolate-kanban-db-dispatch-live-worker-procs-per-test-deflake-fakepopen-has-no-a.md](mirror-0753-test-isolate-kanban-db-dispatch-live-worker-procs-per-test-deflake-fakepopen-has-no-a.md) | 0753 | `998234cb3e` — test: isolate kanban_db_dispatch._live_worker_procs per test (deflake '_FakePopen' has no attribute 'poll') |
| [mirror-0754-test-stop-two-hermes-cli-tests-touching-the-real-hermes-home-stub-retarget-to-owning-.md](mirror-0754-test-stop-two-hermes-cli-tests-touching-the-real-hermes-home-stub-retarget-to-owning-.md) | 0754 | `15b4907151` — test: stop two hermes_cli tests touching the real Hermes home (stub retarget_to_owning_install; stub node instead of symlinking PM's tool store) |
| [mirror-0756-ci-run-tests-hermes-state-on-a-self-hosted-macos-runner-non-blocking-lane.md](mirror-0756-ci-run-tests-hermes-state-on-a-self-hosted-macos-runner-non-blocking-lane.md) | 0756 | `0cca76224d` — ci: run tests/hermes_state on a self-hosted macOS runner (non-blocking lane) |
| [mirror-0757-fix-dashboard-stop-names-a-launcher-child-s-keepalive-job-old-launcher-s-wrapper-sh-i.md](mirror-0757-fix-dashboard-stop-names-a-launcher-child-s-keepalive-job-old-launcher-s-wrapper-sh-i.md) | 0757 | `42ea812e27` — fix(dashboard): --stop names a launcher child's KeepAlive job; old launcher's wrapper sh is no successor [daveey] |
| [mirror-0760-fix-kanban-a-blocker-park-keeps-its-wake-and-only-evidence-clears-a-shared-blocker.md](mirror-0760-fix-kanban-a-blocker-park-keeps-its-wake-and-only-evidence-clears-a-shared-blocker.md) | 0760 | `448e876df2` — fix(kanban): a blocker park keeps its wake, and only evidence clears a shared blocker [daveey] |
| [mirror-0762-fix-tests-use-datetime-utc-in-the-shared-blockers-future-iso-helper-ruff-up017-red-on.md](mirror-0762-fix-tests-use-datetime-utc-in-the-shared-blockers-future-iso-helper-ruff-up017-red-on.md) | 0762 | `fca9f79d18` — fix(tests): use datetime.UTC in the shared-blockers future-ISO helper (ruff UP017 red on main) [daveey] |
| [mirror-0763-fix-desktop-retry-paused-update-continuations-across-warm-resumes.md](mirror-0763-fix-desktop-retry-paused-update-continuations-across-warm-resumes.md) | 0763 | `0864c0ca49` — fix(desktop): retry paused update continuations across warm resumes [daveey] |
| [mirror-0764-fix-ci-let-macos-state-suite-finish-on-every-main-push.md](mirror-0764-fix-ci-let-macos-state-suite-finish-on-every-main-push.md) | 0764 | `82c868369c` — fix(ci): let macOS state suite finish on every main push [daveey] |
| [mirror-0765-fix-accept-bom-pause-requests-and-run-darwin-holder-tests-on-macos.md](mirror-0765-fix-accept-bom-pause-requests-and-run-darwin-holder-tests-on-macos.md) | 0765 | `35ed449284` — fix: accept BOM pause requests and run Darwin holder tests on macOS [daveey] |
| [mirror-0766-fix-ci-declare-macos-runner-label-and-preserve-mktemp-errors.md](mirror-0766-fix-ci-declare-macos-runner-label-and-preserve-mktemp-errors.md) | 0766 | `6e65da3e6f` — fix(ci): declare macOS runner label and preserve mktemp errors [daveey] |
| [mirror-0767-test-kanban-sync-guard-that-a-lapsed-mirror-only-review-grant-requeues-to-review.md](mirror-0767-test-kanban-sync-guard-that-a-lapsed-mirror-only-review-grant-requeues-to-review.md) | 0767 | `c074a30228` — test(kanban-sync): guard that a lapsed mirror-only review grant requeues to review [daveey] |
| [mirror-0768-fix-kanban-seed-the-live-comment-watermark-at-the-run-s-start-not-the-first-poll.md](mirror-0768-fix-kanban-seed-the-live-comment-watermark-at-the-run-s-start-not-the-first-poll.md) | 0768 | `91359bea61` — fix(kanban): seed the live comment watermark at the run's start, not the first poll [daveey] |
| [mirror-0769-fix-serve-re-exec-a-serve-whose-checkout-moved-while-it-waited-out-an-update.md](mirror-0769-fix-serve-re-exec-a-serve-whose-checkout-moved-while-it-waited-out-an-update.md) | 0769 | `463b78b4e4` — fix(serve): re-exec a serve whose checkout moved while it waited out an update [daveey] |
| [mirror-0770-ci-tests-yml-ci-bwrap-comment-names-on-demand-box-a-not-spot.md](mirror-0770-ci-tests-yml-ci-bwrap-comment-names-on-demand-box-a-not-spot.md) | 0770 | `2691544878` — ci: tests.yml ci-bwrap comment names on-demand box A, not spot |
| [mirror-0771-fix-tests-pass-check-false-to-the-serve-re-exec-child-run-ruff-plw1510-red-on-main.md](mirror-0771-fix-tests-pass-check-false-to-the-serve-re-exec-child-run-ruff-plw1510-red-on-main.md) | 0771 | `f0845d0b79` — fix(tests): pass check=False to the serve re-exec child run (ruff PLW1510 red on main) [daveey] |
| [mirror-0780-fix-kanban-let-a-sweep-worker-archive-a-provably-stranded-runs-on-copy.md](mirror-0780-fix-kanban-let-a-sweep-worker-archive-a-provably-stranded-runs-on-copy.md) | 0780 | `0af1a2acd8` — fix(kanban): let a sweep worker archive a provably stranded runs_on copy [daveey] |
| [mirror-0781-fix-kanban-origin-chat-of-a-remote-card-is-subscribed-and-hears-it-finish.md](mirror-0781-fix-kanban-origin-chat-of-a-remote-card-is-subscribed-and-hears-it-finish.md) | 0781 | `a7dd1e117c` — fix(kanban): origin chat of a remote card is subscribed and hears it finish [daveey] |
| [mirror-0782-test-kanban-drop-unused-noqa-in-origin-notify-tests.md](mirror-0782-test-kanban-drop-unused-noqa-in-origin-notify-tests.md) | 0782 | `5b0eca6c91` — test(kanban): drop unused noqa in origin-notify tests [daveey] |
| [mirror-0783-fix-tui-gateway-a-notification-turn-no-longer-swallows-a-core-update-paused-turn.md](mirror-0783-fix-tui-gateway-a-notification-turn-no-longer-swallows-a-core-update-paused-turn.md) | 0783 | `d55299af3e` — fix(tui_gateway): a notification turn no longer swallows a core-update-paused turn [daveey] |
| [mirror-0784-fix-kanban-keep-kanban-create-json-output-parseable-when-it-warns-about-an-unlinked-c.md](mirror-0784-fix-kanban-keep-kanban-create-json-output-parseable-when-it-warns-about-an-unlinked-c.md) | 0784 | `a9a6fa2de6` — fix(kanban): keep kanban create --json output parseable when it warns about an unlinked card [daveey] |
| [mirror-0794-fix-tests-scrub-worker-one-shot-markers-and-reset-session-contextvars-per-test.md](mirror-0794-fix-tests-scrub-worker-one-shot-markers-and-reset-session-contextvars-per-test.md) | 0794 | `4cc6506eb1` — fix(tests): scrub worker one-shot markers and reset session ContextVars per test [daveey] |
| [mirror-0795-fix-tests-sandbox-a-hermes-home-nested-inside-the-real-hermes-root.md](mirror-0795-fix-tests-sandbox-a-hermes-home-nested-inside-the-real-hermes-root.md) | 0795 | `89338dbf51` — fix(tests): sandbox a HERMES_HOME nested inside the real Hermes root [daveey] |
| [mirror-0796-fix-kanban-non-owner-backends-and-stopped-pollers-hand-tui-kanban-claims-back.md](mirror-0796-fix-kanban-non-owner-backends-and-stopped-pollers-hand-tui-kanban-claims-back.md) | 0796 | `407b0a7643` — fix(kanban): non-owner backends and stopped pollers hand tui kanban claims back [daveey] |
| [mirror-0797-test-kanban-a-local-archive-closes-as-closed-uncompleted-not-unknown.md](mirror-0797-test-kanban-a-local-archive-closes-as-closed-uncompleted-not-unknown.md) | 0797 | `9b6863cd14` — test(kanban): a local archive closes as closed_uncompleted, not unknown [daveey] |
| [mirror-0798-fix-kanban-start-unloaded-session-sweeper-on-hermes-serve-boot.md](mirror-0798-fix-kanban-start-unloaded-session-sweeper-on-hermes-serve-boot.md) | 0798 | `a24e27850b` — fix(kanban): start unloaded-session sweeper on hermes serve boot [daveey] |
| [mirror-0799-fix-kanban-use-portable-notice-holder-liveness-probe.md](mirror-0799-fix-kanban-use-portable-notice-holder-liveness-probe.md) | 0799 | `2ebff86d10` — fix(kanban): use portable notice-holder liveness probe [daveey] |
| [mirror-0800-fix-state-revalidate-macos-deleted-wal-holders-after-path-comparison.md](mirror-0800-fix-state-revalidate-macos-deleted-wal-holders-after-path-comparison.md) | 0800 | `566cbacc53` — fix(state): revalidate macOS deleted-WAL holders after path comparison [daveey] |
| [mirror-0801-fix-tests-picker-and-test-paths-no-longer-block-on-live-models-dev-portal-update-re-p.md](mirror-0801-fix-tests-picker-and-test-paths-no-longer-block-on-live-models-dev-portal-update-re-p.md) | 0801 | `945b931226` — fix(tests): picker and test paths no longer block on live models.dev/portal; update re-prepares the retirement fence [daveey] |
| [mirror-0802-fix-kanban-kanban-db-schedule-binds-kanban-db-last-so-it-imports-first-without-a-cycl.md](mirror-0802-fix-kanban-kanban-db-schedule-binds-kanban-db-last-so-it-imports-first-without-a-cycl.md) | 0802 | `db733d2c56` — fix(kanban): kanban_db_schedule binds kanban_db last, so it imports first without a cycle [daveey] |
| [mirror-0803-fix-doctor-gateway-supervision-checks-ask-only-whether-s6-is-pid-1-never-systemctl.md](mirror-0803-fix-doctor-gateway-supervision-checks-ask-only-whether-s6-is-pid-1-never-systemctl.md) | 0803 | `4c4db9bb12` — fix(doctor): gateway-supervision checks ask only whether s6 is PID 1, never systemctl [daveey] |
| [mirror-0804-fix-cron-unreachable-retry-still-imports-into-a-scheduler-running-the-previous-releas.md](mirror-0804-fix-cron-unreachable-retry-still-imports-into-a-scheduler-running-the-previous-releas.md) | 0804 | `66c42a1aa5` — fix(cron): unreachable_retry still imports into a scheduler running the previous release [daveey] |
| [mirror-0805-fix-agent-tool-executor-still-imports-into-a-process-running-the-previous-release.md](mirror-0805-fix-agent-tool-executor-still-imports-into-a-process-running-the-previous-release.md) | 0805 | `32c097c75e` — fix(agent): tool_executor still imports into a process running the previous release [daveey] |
| [mirror-0806-fix-desktop-show-ssh-cloud-linux-gateways-with-the-cloud-glyph-not-a-desktop.md](mirror-0806-fix-desktop-show-ssh-cloud-linux-gateways-with-the-cloud-glyph-not-a-desktop.md) | 0806 | `31079f050a` — fix(desktop): show SSH cloud/Linux gateways with the cloud glyph, not a desktop [daveey] |
| [mirror-0807-fix-desktop-api-failure-lines-in-desktop-log-name-the-connection-the-request-went-to.md](mirror-0807-fix-desktop-api-failure-lines-in-desktop-log-name-the-connection-the-request-went-to.md) | 0807 | `02a197fab2` — fix(desktop): api failure lines in desktop.log name the connection the request went to [daveey] |
| [mirror-0808-fix-kanban-placement-hermes-hub-resolves-to-the-hub-gateway-instead-of-refusing-the-c.md](mirror-0808-fix-kanban-placement-hermes-hub-resolves-to-the-hub-gateway-instead-of-refusing-the-c.md) | 0808 | `f88977402b` — fix(kanban): placement='hermes-hub' resolves to the 'hub' gateway instead of refusing the card [daveey] |
| [mirror-0809-fix-kanban-agent-created-cards-must-declare-requires-skill-metadata-feeds-the-preflig.md](mirror-0809-fix-kanban-agent-created-cards-must-declare-requires-skill-metadata-feeds-the-preflig.md) | 0809 | `3234f98376` — fix(kanban): agent-created cards must declare requires; skill metadata feeds the preflight [daveey] (#166) |
| [mirror-0810-fix-kanban-a-cleared-shared-blocker-wakes-each-card-once-and-only-a-blocker-park-s-pr.md](mirror-0810-fix-kanban-a-cleared-shared-blocker-wakes-each-card-once-and-only-a-blocker-park-s-pr.md) | 0810 | `334d58b4c3` — fix(kanban): a cleared shared blocker wakes each card once, and only a blocker park's probe clears it [daveey] |
| [mirror-0811-kanban-final-send-lines-reach-chats-subscribed-by-a-refused-worker-send-144.md](mirror-0811-kanban-final-send-lines-reach-chats-subscribed-by-a-refused-worker-send-144.md) | 0811 | `582e43bdd0` — kanban: FINAL SEND lines reach chats subscribed by a refused worker send (#144) |
| [mirror-0812-stop-the-board-poll-from-stalling-the-desktop-backend.md](mirror-0812-stop-the-board-poll-from-stalling-the-desktop-backend.md) | 0812 | `dff3b8a924` — [daveey] Stop the board poll from stalling the Desktop backend |
| [mirror-0815-fix-desktop-fail-fast-while-a-pooled-remote-backend-is-known-down.md](mirror-0815-fix-desktop-fail-fast-while-a-pooled-remote-backend-is-known-down.md) | 0815 | `08c725d741` — fix(desktop): fail fast while a pooled remote backend is known down [daveey] |
| [mirror-0817-fix-tests-deflake-turn-hold-cross-process-lease-and-hosted-room-tests-poll-long-ci-ru.md](mirror-0817-fix-tests-deflake-turn-hold-cross-process-lease-and-hosted-room-tests-poll-long-ci-ru.md) | 0817 | `9a6270f825` — fix(tests): deflake turn-hold, cross-process lease and hosted-room tests; poll long CI runs for flakes [daveey] |
| [mirror-0818-fix-desktop-remote-down-gate-escalates-only-on-the-first-drop-and-failed-trials.md](mirror-0818-fix-desktop-remote-down-gate-escalates-only-on-the-first-drop-and-failed-trials.md) | 0818 | `7ab44c136e` — fix(desktop): remote down gate escalates only on the first drop and failed trials [daveey] |
| [mirror-0819-test-desktop-type-the-fast-fail-error-in-remote-down-gate-test.md](mirror-0819-test-desktop-type-the-fast-fail-error-in-remote-down-gate-test.md) | 0819 | `4f0678cc92` — test(desktop): type the fast-fail error in remote-down-gate test [daveey] |
| [mirror-0820-fix-dashboard-keep-model-switch-catalog-lookups-out-of-config-lock-waits.md](mirror-0820-fix-dashboard-keep-model-switch-catalog-lookups-out-of-config-lock-waits.md) | 0820 | `f456a8d081` — fix(dashboard): keep model-switch catalog lookups out of config lock waits [daveey] |
| [mirror-0821-ci-coalesce-workflow-dispatch-ci-runs-per-ref-and-docker-main-runs-runner-starvation-.md](mirror-0821-ci-coalesce-workflow-dispatch-ci-runs-per-ref-and-docker-main-runs-runner-starvation-.md) | 0821 | `76a514de64` — ci: coalesce workflow_dispatch CI runs per ref and Docker main runs (runner starvation) (#158) |
| [mirror-0822-fix-kanban-isolate-retained-worker-handles-between-tests-156.md](mirror-0822-fix-kanban-isolate-retained-worker-handles-between-tests-156.md) | 0822 | `a0ebd73b9c` — fix(kanban): isolate retained worker handles between tests [daveey] (#156) |
| [mirror-0823-fix-tests-await-multiplex-status-persistence-before-reading.md](mirror-0823-fix-tests-await-multiplex-status-persistence-before-reading.md) | 0823 | `7eb4ddb0fa` — fix(tests): await multiplex status persistence before reading [daveey] |
| [mirror-0824-feat-tui-gateway-api-ws-client-agent-chats-are-born-hidden-and-are-not-human-input.md](mirror-0824-feat-tui-gateway-api-ws-client-agent-chats-are-born-hidden-and-are-not-human-input.md) | 0824 | `57401bf045` — feat(tui_gateway): /api/ws?client=agent chats are born hidden and are not human input [daveey] |
| [mirror-0825-fix-tests-reset-fixture-skips-tui-gateway-server-while-another-thread-is-mid-import.md](mirror-0825-fix-tests-reset-fixture-skips-tui-gateway-server-while-another-thread-is-mid-import.md) | 0825 | `26fd91a0a6` — fix(tests): reset fixture skips tui_gateway.server while another thread is mid-import |
| [mirror-0826-test-don-t-time-out-the-refused-turn-join-before-its-lazy-tui-gateway-server-import-f.md](mirror-0826-test-don-t-time-out-the-refused-turn-join-before-its-lazy-tui-gateway-server-import-f.md) | 0826 | `77f88c27a7` — test: don't time out the refused-turn join before its lazy tui_gateway.server import finishes (deflake) |
| [mirror-0827-refactor-tui-gateway-born-hidden-default-lives-in-methods-session-hidden-py-code-heal.md](mirror-0827-refactor-tui-gateway-born-hidden-default-lives-in-methods-session-hidden-py-code-heal.md) | 0827 | `7e4d087cf7` — refactor(tui_gateway): born-hidden default lives in methods_session_hidden.py (code-health FILE_LINES) [daveey] |
| [mirror-0828-fix-tests-stop-subagent-handoff-flake-from-racing-a-0-4s-sleep.md](mirror-0828-fix-tests-stop-subagent-handoff-flake-from-racing-a-0-4s-sleep.md) | 0828 | `4d7c25e63d` — fix(tests): stop subagent handoff flake from racing a 0.4s sleep [daveey] |
| [mirror-0829-fix-tests-keep-codex-picker-usage-workers-out-of-image-recorder.md](mirror-0829-fix-tests-keep-codex-picker-usage-workers-out-of-image-recorder.md) | 0829 | `aef9f9a55d` — fix(tests): keep Codex picker usage workers out of image recorder [daveey] |
| [mirror-0830-feat-kanban-placement-heavy-work-tests-builds-benches-runs-on-a-worker-node-via-runs-.md](mirror-0830-feat-kanban-placement-heavy-work-tests-builds-benches-runs-on-a-worker-node-via-runs-.md) | 0830 | `1ba4ced7e2` — feat(kanban placement): heavy work (tests/builds/benches) runs on a worker node via runs_on [daveey] |
| [mirror-0831-fix-gateway-a-starved-profile-verb-waits-out-its-apply.md](mirror-0831-fix-gateway-a-starved-profile-verb-waits-out-its-apply.md) | 0831 | `816c454ef0` — fix(gateway): a starved profile verb waits out its apply [daveey] |
| [mirror-0832-fix-kanban-placement-heavy-route-works-with-the-real-board-registry-no-pickup-field.md](mirror-0832-fix-kanban-placement-heavy-route-works-with-the-real-board-registry-no-pickup-field.md) | 0832 | `ed53a153fd` — fix(kanban placement): heavy_route works with the real Board registry (no pickup field) [daveey] |
| [mirror-0833-state-preflight-ignores-state-db-quarantined-mid-check-deflake-concurrent-quarantine-.md](mirror-0833-state-preflight-ignores-state-db-quarantined-mid-check-deflake-concurrent-quarantine-.md) | 0833 | `3a0ba542f3` — state: preflight ignores state.db quarantined mid-check (deflake concurrent quarantine test) |
| [mirror-0834-test-tui-assert-refused-turn-workers-finish-without-errors.md](mirror-0834-test-tui-assert-refused-turn-workers-finish-without-errors.md) | 0834 | `eae9120eeb` — test(tui): assert refused-turn workers finish without errors [daveey] |
| [mirror-0835-fix-kanban-honest-requires-and-hub-profiles-no-longer-pin-macbook-cards-local.md](mirror-0835-fix-kanban-honest-requires-and-hub-profiles-no-longer-pin-macbook-cards-local.md) | 0835 | `9056be951d` — fix(kanban): honest requires and hub profiles no longer pin MacBook cards local [daveey] |
| [mirror-0836-kanban-remote-card-poller-notices-carry-final-send-and-share-event-identity-with-the-.md](mirror-0836-kanban-remote-card-poller-notices-carry-final-send-and-share-event-identity-with-the-.md) | 0836 | `80012e85fe` — kanban: remote-card poller notices carry FINAL SEND and share event identity with the sweeper |
| [mirror-0837-fix-kanban-keep-remote-notices-in-flight-until-the-transcript-has-them.md](mirror-0837-fix-kanban-keep-remote-notices-in-flight-until-the-transcript-has-them.md) | 0837 | `5bab4bb450` — fix(kanban): keep remote notices in flight until the transcript has them [daveey] |
| [mirror-0838-fix-hermes-cli-do-not-resolve-another-entry-point-s-p-at-import.md](mirror-0838-fix-hermes-cli-do-not-resolve-another-entry-point-s-p-at-import.md) | 0838 | `1ccc80512e` — fix(hermes_cli): do not resolve another entry point's -p at import [daveey] |
| [mirror-0839-fix-gateway-persist-watchdog-degraded-stamp-before-hard-exit.md](mirror-0839-fix-gateway-persist-watchdog-degraded-stamp-before-hard-exit.md) | 0839 | `1230e3d4af` — fix(gateway): persist watchdog degraded stamp before hard-exit [daveey] |
| [mirror-0840-fix-hermes-cli-honor-p-from-the-python-c-source-launcher.md](mirror-0840-fix-hermes-cli-honor-p-from-the-python-c-source-launcher.md) | 0840 | `06f3498b16` — fix(hermes_cli): honor -p from the python -c source launcher [daveey] |
| [mirror-0841-feat-kanban-kanban-move-hermes-kanban-move-a-running-card-relocates-itself-via-runs-o.md](mirror-0841-feat-kanban-kanban-move-hermes-kanban-move-a-running-card-relocates-itself-via-runs-o.md) | 0841 | `dc19ad6ccc` — feat(kanban): kanban_move / hermes kanban move: a running card relocates itself via runs_on [daveey] |
| [mirror-0842-fix-desktop-e2e-fail-fast-on-a-small-dev-shm-and-keep-core-failures-inside-the-job-li.md](mirror-0842-fix-desktop-e2e-fail-fast-on-a-small-dev-shm-and-keep-core-failures-inside-the-job-li.md) | 0842 | `5033f308f0` — fix(desktop-e2e): fail fast on a small /dev/shm and keep core failures inside the job limit [daveey] |
| [mirror-0843-fix-desktop-a-failed-hub-list-no-longer-blanks-this-mac-s-sidebar-stripes.md](mirror-0843-fix-desktop-a-failed-hub-list-no-longer-blanks-this-mac-s-sidebar-stripes.md) | 0843 | `7adaa5306e` — fix(desktop): a failed hub list no longer blanks this Mac's sidebar stripes [daveey] |
| [mirror-0844-fix-tests-deflake-deferred-history-close-test-2s-event-waits-missed-on-a-throttled-ru.md](mirror-0844-fix-tests-deflake-deferred-history-close-test-2s-event-waits-missed-on-a-throttled-ru.md) | 0844 | `131f0a3467` — fix(tests): deflake deferred-history close test; 2s event waits missed on a throttled runner |
| [mirror-0845-fix-kanban-archive-under-another-machine-s-live-lease-sends-a-stop-request-not-a-reje.md](mirror-0845-fix-kanban-archive-under-another-machine-s-live-lease-sends-a-stop-request-not-a-reje.md) | 0845 | `4206276bd9` — fix(kanban): archive under another machine's live lease sends a stop request, not a rejected write [daveey] |
| [mirror-0846-fix-tests-regenerate-prompt-surface-for-clarify-answered-elsewhere-event-sync-the-cla.md](mirror-0846-fix-tests-regenerate-prompt-surface-for-clarify-answered-elsewhere-event-sync-the-cla.md) | 0846 | `1bb8af6ddb` — fix(tests): regenerate prompt surface for clarify answered_elsewhere; event-sync the clarify early-answer tests |
| [mirror-0847-fix-desktop-replica-fallback-keeps-a-gateway-s-last-kanban-counts.md](mirror-0847-fix-desktop-replica-fallback-keeps-a-gateway-s-last-kanban-counts.md) | 0847 | `30c707ff2e` — fix(desktop): replica fallback keeps a gateway's last kanban counts [daveey] |
| [mirror-0848-fix-desktop-re-list-while-a-failed-sidebar-list-leaves-owners-stale-log-freshness-fli.md](mirror-0848-fix-desktop-re-list-while-a-failed-sidebar-list-leaves-owners-stale-log-freshness-fli.md) | 0848 | `dbf62bea79` — fix(desktop): re-list while a failed sidebar list leaves owners stale; log freshness flips [daveey] |
| [mirror-0849-feat-sessions-cooperative-session-handover-hermes-sessions-move-board-owner-fencing.md](mirror-0849-feat-sessions-cooperative-session-handover-hermes-sessions-move-board-owner-fencing.md) | 0849 | `59009a4979` — feat(sessions): cooperative session handover: hermes sessions move + Board owner fencing [daveey] |
| [mirror-0850-test-tui-exercise-deferred-history-cancellation-beyond-old-guard.md](mirror-0850-test-tui-exercise-deferred-history-cancellation-beyond-old-guard.md) | 0850 | `245b04fe5c` — test(tui): exercise deferred history cancellation beyond old guard |
| [mirror-0851-feat-kanban-sync-report-this-machine-s-load-to-the-board-registry-every-30-s.md](mirror-0851-feat-kanban-sync-report-this-machine-s-load-to-the-board-registry-every-30-s.md) | 0851 | `192b9db93e` — feat(kanban sync): report this machine's load to the Board registry every 30 s |
| [mirror-0852-fix-machine-metrics-explicit-encodings-for-proc-and-subprocess-reads.md](mirror-0852-fix-machine-metrics-explicit-encodings-for-proc-and-subprocess-reads.md) | 0852 | `a19249c642` — fix(machine metrics): explicit encodings for /proc and subprocess reads |
| [mirror-0853-fix-machine-metrics-decode-bytes-explicitly-footgun-lint.md](mirror-0853-fix-machine-metrics-decode-bytes-explicitly-footgun-lint.md) | 0853 | `8a09d8aba9` — fix(machine metrics): decode bytes explicitly (footgun lint) |
| [mirror-0854-fix-sessions-move-refuse-while-open-cards-on-other-gateways-notify-the-chat-force-ove.md](mirror-0854-fix-sessions-move-refuse-while-open-cards-on-other-gateways-notify-the-chat-force-ove.md) | 0854 | `ea91fc2b15` — fix(sessions move): refuse while open cards on other gateways notify the chat (--force overrides) [daveey] |
| [mirror-0855-fix-kanban-sync-one-dependency-view-for-cross-gateway-parents.md](mirror-0855-fix-kanban-sync-one-dependency-view-for-cross-gateway-parents.md) | 0855 | `4a16a716f7` — fix(kanban sync): one dependency view for cross-gateway parents [daveey] |
| [mirror-0856-fix-turn-lease-busy-state-db-is-not-another-writer-on-desktop-tui.md](mirror-0856-fix-turn-lease-busy-state-db-is-not-another-writer-on-desktop-tui.md) | 0856 | `7e8e8f4a59` — fix(turn lease): busy state.db is not another writer on Desktop/TUI |
| [mirror-0857-feat-browser-use-agent-tab-ledger-record-cap-and-close-tabs-agents-open-in-the-shared.md](mirror-0857-feat-browser-use-agent-tab-ledger-record-cap-and-close-tabs-agents-open-in-the-shared.md) | 0857 | `410bb33433` — feat(browser-use): agent tab ledger — record, cap and close tabs agents open in the shared Chrome [daveey] |
| [mirror-0858-fix-tests-deflake-deferred-history-reuse-test-2s-release-wait-failed-under-a-stalled-.md](mirror-0858-fix-tests-deflake-deferred-history-reuse-test-2s-release-wait-failed-under-a-stalled-.md) | 0858 | `2596142bf4` — fix(tests): deflake deferred-history reuse test; 2s release wait failed under a stalled setter thread [daveey] |
| [mirror-0860-fix-tests-make-stale-webhook-update-barrier-scheduler-independent.md](mirror-0860-fix-tests-make-stale-webhook-update-barrier-scheduler-independent.md) | 0860 | `76d5f70863` — fix(tests): make stale webhook update barrier scheduler-independent [daveey] |
| [mirror-0861-fix-tests-regenerate-prompt-surface-for-session-move-and-browser-tab-close.md](mirror-0861-fix-tests-regenerate-prompt-surface-for-session-move-and-browser-tab-close.md) | 0861 | `5e206f07aa` — fix(tests): regenerate prompt surface for session_move and browser tab close |
| [mirror-0862-fix-kanban-never-dispatch-a-card-this-machine-cannot-lease-pick-up-pinned-reviews.md](mirror-0862-fix-kanban-never-dispatch-a-card-this-machine-cannot-lease-pick-up-pinned-reviews.md) | 0862 | `c44335f774` — fix(kanban): never dispatch a card this machine cannot lease; pick up pinned reviews [daveey] |
| [mirror-0863-fix-tui-resume-read-unanswered-turn-tail-before-handing-the-profile-db-to-the-hydrati.md](mirror-0863-fix-tui-resume-read-unanswered-turn-tail-before-handing-the-profile-db-to-the-hydrati.md) | 0863 | `ae5cb9a99c` — fix(tui resume): read unanswered-turn tail before handing the profile db to the hydration worker; deflakes deferred-resume db ownership test |
| [mirror-0864-fix-tests-deflake-kanban-notifier-tests-10s-wait-for-deadline-timed-out-on-throttled-.md](mirror-0864-fix-tests-deflake-kanban-notifier-tests-10s-wait-for-deadline-timed-out-on-throttled-.md) | 0864 | `101b35845e` — fix(tests): deflake kanban notifier tests; 10s wait_for deadline timed out on throttled runner (now 120s) |
| [mirror-0866-fix-restart-recovery-an-admitted-decision-turn-a-restart-stops-before-its-first-tool-.md](mirror-0866-fix-restart-recovery-an-admitted-decision-turn-a-restart-stops-before-its-first-tool-.md) | 0866 | `0ee4afb4bf` — fix(restart recovery): an admitted decision turn a restart stops before its first tool is continued at boot; sweep logs every skipped marker; restart failed-turn copy [daveey] |
| [mirror-0867-fix-lsp-do-not-killpg-a-reused-server-pid-when-the-reader-exits-during-init.md](mirror-0867-fix-lsp-do-not-killpg-a-reused-server-pid-when-the-reader-exits-during-init.md) | 0867 | `47ae53b158` — fix(lsp): do not killpg a reused server pid when the reader exits during init [daveey] |
| [mirror-0868-fix-kanban-dispatch-output-names-cards-this-machine-cannot-lease-and-deferred-claims.md](mirror-0868-fix-kanban-dispatch-output-names-cards-this-machine-cannot-lease-and-deferred-claims.md) | 0868 | `b232537d0a` — fix(kanban): dispatch output names cards this machine cannot lease and deferred claims [daveey] |
| [mirror-0880-fix-tests-deflake-unmapped-home-proof-wait-for-the-stub-s-proc-cmdline-environ-after-.md](mirror-0880-fix-tests-deflake-unmapped-home-proof-wait-for-the-stub-s-proc-cmdline-environ-after-.md) | 0880 | `f8d0dfbe76` — fix(tests): deflake unmapped-home proof; wait for the stub's /proc cmdline+environ after Popen (vfork returns before they are published) |
| [mirror-0881-test-gateway-tolerate-descheduling-in-pending-drain-waits.md](mirror-0881-test-gateway-tolerate-descheduling-in-pending-drain-waits.md) | 0881 | `1e29465611` — test(gateway): tolerate descheduling in pending-drain waits [daveey] |
| [mirror-0882-fix-state-kanban-notice-dedupe-no-longer-scans-messages-or-takes-the-write-lock-when-.md](mirror-0882-fix-state-kanban-notice-dedupe-no-longer-scans-messages-or-takes-the-write-lock-when-.md) | 0882 | `593b19f369` — fix(state): kanban notice dedupe no longer scans messages or takes the write lock when already delivered [daveey] |
| [mirror-0883-test-process-hold-the-pre-arm-heartbeat-child-until-the-beat-187.md](mirror-0883-test-process-hold-the-pre-arm-heartbeat-child-until-the-beat-187.md) | 0883 | `5fec10c138` — test(process): hold the pre-arm heartbeat child until the beat [daveey] (#187) |
| [mirror-0884-test-delegation-wait-generously-for-the-fan-out-completion-event-instead-of-a-5s-dead.md](mirror-0884-test-delegation-wait-generously-for-the-fan-out-completion-event-instead-of-a-5s-dead.md) | 0884 | `09251f94f2` — test(delegation): wait generously for the fan-out completion event instead of a 5s deadline |
| [mirror-0885-feat-state-warn-with-the-callback-name-when-a-write-txn-is-held-5s.md](mirror-0885-feat-state-warn-with-the-callback-name-when-a-write-txn-is-held-5s.md) | 0885 | `3f166adee6` — feat(state): warn with the callback name when a write txn is held >=5s [daveey] |
| [mirror-0886-fix-ci-session-move-spawn-env-via-build-subprocess-env-add-session-move-to-v4-tool-en.md](mirror-0886-fix-ci-session-move-spawn-env-via-build-subprocess-env-add-session-move-to-v4-tool-en.md) | 0886 | `4a79c9aff5` — fix(ci): session_move spawn env via build_subprocess_env; add session_move to v4 tool_enabled_unused toolset enum |
| [mirror-0887-kanban-tools-database-errors-carry-a-hint-naming-the-safe-fallback-self-heal-agent-6e.md](mirror-0887-kanban-tools-database-errors-carry-a-hint-naming-the-safe-fallback-self-heal-agent-6e.md) | 0887 | `a38da79f8a` — [daveey] kanban tools: database errors carry a hint naming the safe fallback (self-heal agent-6eb3a51e94) |
| [mirror-0888-fix-tests-arm-process-consumer-notifications-before-spawn.md](mirror-0888-fix-tests-arm-process-consumer-notifications-before-spawn.md) | 0888 | `ea821b5e18` — fix(tests): arm process-consumer notifications before spawn [daveey] |
| [mirror-0889-ci-scripts-check-gates-prompt-surface-staleness-prompt-surface-render-is-hermetic-und.md](mirror-0889-ci-scripts-check-gates-prompt-surface-staleness-prompt-surface-render-is-hermetic-und.md) | 0889 | `1cd314df0b` — ci: scripts/check gates prompt-surface staleness; prompt_surface render is hermetic under any TMPDIR |
| [mirror-0890-fix-cron-end-the-session-row-of-a-run-killed-mid-turn-when-its-execution-is-recovered.md](mirror-0890-fix-cron-end-the-session-row-of-a-run-killed-mid-turn-when-its-execution-is-recovered.md) | 0890 | `fec6ced730` — fix(cron): end the session row of a run killed mid-turn when its execution is recovered [daveey] |
| [mirror-0891-fix-cron-backfill-also-ends-orphaned-cron-session-rows-whose-attempt-was-pruned-or-se.md](mirror-0891-fix-cron-backfill-also-ends-orphaned-cron-session-rows-whose-attempt-was-pruned-or-se.md) | 0891 | `ac1eb73e0e` — fix(cron): backfill also ends orphaned cron session rows whose attempt was pruned or settled unfinalized [daveey] |
| [mirror-0892-feat-kanban-edit-max-retries-goal-mode-goal-max-turns-max-runtime-seconds-session-id-.md](mirror-0892-feat-kanban-edit-max-retries-goal-mode-goal-max-turns-max-runtime-seconds-session-id-.md) | 0892 | `c350e23068` — feat(kanban): edit --max-retries/--goal-mode/--goal-max-turns/--max-runtime-seconds/--session-id and reopen <done> [daveey] |
| [mirror-0893-fix-ci-ci-flaky-cards-does-not-refile-a-job-an-existing-card-already-cites.md](mirror-0893-fix-ci-ci-flaky-cards-does-not-refile-a-job-an-existing-card-already-cites.md) | 0893 | `6db9b6d0bb` — fix(ci): ci-flaky-cards does not refile a job an existing card already cites [daveey] |
| [mirror-0894-fix-ci-document-overlap-config-and-complete-lsp-spawn-fake.md](mirror-0894-fix-ci-document-overlap-config-and-complete-lsp-spawn-fake.md) | 0894 | `b7e5a3b7b0` — fix(ci): document overlap config and complete LSP spawn fake [daveey] |
| [mirror-0895-fix-tests-synchronize-notification-held-handoff-under-load.md](mirror-0895-fix-tests-synchronize-notification-held-handoff-under-load.md) | 0895 | `2e952ee510` — fix(tests): synchronize notification held handoff under load [daveey] |
| [mirror-0896-fix-tests-code-health-cli-fixture-repos-stub-the-prompt-surface-guard-the-hooks-now-r.md](mirror-0896-fix-tests-code-health-cli-fixture-repos-stub-the-prompt-surface-guard-the-hooks-now-r.md) | 0896 | `43ed776b5b` — fix(tests): code-health CLI fixture repos stub the prompt-surface guard the hooks now run |
| [mirror-0897-fix-kanban-remote-notices-stop-re-writing-delivered-notices-every-sweep.md](mirror-0897-fix-kanban-remote-notices-stop-re-writing-delivered-notices-every-sweep.md) | 0897 | `54a602e068` — fix(kanban remote notices): stop re-writing delivered notices every sweep [daveey] |
| [mirror-0898-fix-update-a-git-ancestry-timeout-on-a-loaded-host-keeps-head-instead-of-failing-the-.md](mirror-0898-fix-update-a-git-ancestry-timeout-on-a-loaded-host-keeps-head-instead-of-failing-the-.md) | 0898 | `fb393b96b3` — fix(update): a git ancestry timeout on a loaded host keeps HEAD instead of failing the stable lookup |
| [mirror-0899-deflake-test-heartbeat-carries-only-new-output-gate-child-on-fifo-instead-of-sleep.md](mirror-0899-deflake-test-heartbeat-carries-only-new-output-gate-child-on-fifo-instead-of-sleep.md) | 0899 | `fc9d28e2a6` — Deflake test_heartbeat_carries_only_new_output: gate child on fifo instead of sleep |
| [mirror-0902-test-desktop-voice-prefs-storage-failure-cases-spy-on-storage-prototype.md](mirror-0902-test-desktop-voice-prefs-storage-failure-cases-spy-on-storage-prototype.md) | 0902 | `0a461c1d8f` — test(desktop): voice-prefs storage-failure cases spy on Storage.prototype |
| [mirror-0903-fix-kanban-a-redundant-shared-blocker-clear-does-not-re-wake-a-card-an-earlier-clear-.md](mirror-0903-fix-kanban-a-redundant-shared-blocker-clear-does-not-re-wake-a-card-an-earlier-clear-.md) | 0903 | `fe4027a3bd` — fix(kanban): a redundant shared-blocker clear does not re-wake a card an earlier clear released [daveey] |
| [mirror-0904-fix-tests-re-patch-dispatch-memory-sample-after-profile-cap-reimport-164.md](mirror-0904-fix-tests-re-patch-dispatch-memory-sample-after-profile-cap-reimport-164.md) | 0904 | `8dca42d3f9` — fix(tests): re-patch dispatch memory sample after profile-cap reimport [daveey] (#164) |
| [mirror-0905-fix-kanban-audited-orchestrator-re-point-of-a-completion-contract-that-can-never-pass.md](mirror-0905-fix-kanban-audited-orchestrator-re-point-of-a-completion-contract-that-can-never-pass.md) | 0905 | `2f4774720d` — fix(kanban): audited orchestrator re-point of a completion contract that can never pass [daveey] |
| [mirror-0907-fix-tests-bind-signal-handler-synthetic-worker-to-its-parent-so-it-can-t-orphan.md](mirror-0907-fix-tests-bind-signal-handler-synthetic-worker-to-its-parent-so-it-can-t-orphan.md) | 0907 | `d1dfda1f43` — fix(tests): bind signal-handler synthetic worker to its parent so it can't orphan |
| [mirror-0908-fix-kanban-uncaptured-completions-keep-their-run-worker-node-ghosts-retire.md](mirror-0908-fix-kanban-uncaptured-completions-keep-their-run-worker-node-ghosts-retire.md) | 0908 | `49745758d7` — fix(kanban): uncaptured completions keep their run; worker-node ghosts retire [daveey] |
| [mirror-0909-refactor-kanban-replica-record-identity-helpers-move-to-kanban-session-activity-recor.md](mirror-0909-refactor-kanban-replica-record-identity-helpers-move-to-kanban-session-activity-recor.md) | 0909 | `e54472005d` — refactor(kanban): replica record identity helpers move to kanban_session_activity_records (file cap) |
| [mirror-0910-fix-ci-scripts-check-hooks-and-prompt-surface-run-on-macos-pm-installs.md](mirror-0910-fix-ci-scripts-check-hooks-and-prompt-surface-run-on-macos-pm-installs.md) | 0910 | `5205b04e75` — fix(ci): scripts/check hooks and prompt-surface run on macOS pm installs |
| [mirror-0911-fix-ci-scripts-check-hook-borrows-the-installed-hermes-env-in-a-clone-that-is-not-an-.md](mirror-0911-fix-ci-scripts-check-hook-borrows-the-installed-hermes-env-in-a-clone-that-is-not-an-.md) | 0911 | `fb1ec37282` — fix(ci): scripts/check hook borrows the installed Hermes env in a clone that is not an install |
| [mirror-0912-fix-tests-pin-event-timestamps-in-room-log-page-byte-bound-test.md](mirror-0912-fix-tests-pin-event-timestamps-in-room-log-page-byte-bound-test.md) | 0912 | `3449c1152c` — fix(tests): pin event timestamps in room log page-byte bound test |
| [mirror-0913-fix-gateway-retry-a-failed-startup-stale-handoff-reclaim-instead-of-giving-up.md](mirror-0913-fix-gateway-retry-a-failed-startup-stale-handoff-reclaim-instead-of-giving-up.md) | 0913 | `589ad047e4` — [daveey] fix(gateway): retry a failed startup stale-handoff reclaim instead of giving up |
| [mirror-0914-fix-kanban-module-form-workers-run-with-p-so-a-checkout-workspace-can-t-shadow-the-in.md](mirror-0914-fix-kanban-module-form-workers-run-with-p-so-a-checkout-workspace-can-t-shadow-the-in.md) | 0914 | `648e72d2ec` — fix(kanban): module-form workers run with -P so a checkout workspace can't shadow the install [daveey] (#153) |
| [mirror-0915-fix-resume-prioritize-durable-update-pauses-on-replaced-backends.md](mirror-0915-fix-resume-prioritize-durable-update-pauses-on-replaced-backends.md) | 0915 | `0514681bb4` — fix(resume): prioritize durable update pauses on replaced backends [daveey] |
| [mirror-0916-fix-kanban-a-pr-contract-on-a-repo-with-no-required-checks-completes-via-metadata-con.md](mirror-0916-fix-kanban-a-pr-contract-on-a-repo-with-no-required-checks-completes-via-metadata-con.md) | 0916 | `f441e3ff10` — fix(kanban): a PR contract on a repo with no required checks completes via metadata.contract_waiver, not a hand-edited kanban.db [daveey] |
| [mirror-0917-fix-tests-reclaim-host-test-slots-held-by-orphans-deleted-tree-dead-holder-over-45-mi.md](mirror-0917-fix-tests-reclaim-host-test-slots-held-by-orphans-deleted-tree-dead-holder-over-45-mi.md) | 0917 | `5109888869` — fix(tests): reclaim host test slots held by orphans (deleted tree, dead holder, over 45 min) [daveey] |
| [mirror-0918-fix-tests-clear-the-host-slot-holder-record-on-release.md](mirror-0918-fix-tests-clear-the-host-slot-holder-record-on-release.md) | 0918 | `fb855a7045` — fix(tests): clear the host slot holder record on release [daveey] |
| [mirror-0919-test-desktop-spy-the-live-localstorage-setitem-in-voice-prefs-failure-cases.md](mirror-0919-test-desktop-spy-the-live-localstorage-setitem-in-voice-prefs-failure-cases.md) | 0919 | `5ca8cccfbd` — test(desktop): spy the live localStorage.setItem in voice-prefs failure cases [daveey] |
| [mirror-0920-fix-kanban-refused-block-schedule-unblock-leave-no-reason-comment-explain-the-human-a.md](mirror-0920-fix-kanban-refused-block-schedule-unblock-leave-no-reason-comment-explain-the-human-a.md) | 0920 | `1184aefb32` — fix(kanban): refused block/schedule/unblock leave no reason comment; explain the human-answer refusal; reject free text as task ids [daveey] |
| [mirror-0921-fix-resume-bound-a-replaced-backend-s-durable-pause-hold-to-hold-max-s-paused-continu.md](mirror-0921-fix-resume-bound-a-replaced-backend-s-durable-pause-hold-to-hold-max-s-paused-continu.md) | 0921 | `4897e78880` — fix(resume): bound a replaced backend's durable pause hold to HOLD_MAX_S; paused continuation takes over idle detached holders [daveey] |
| [mirror-0922-fix-kanban-check-requires-where-runs-on-lands-the-card-never-refuse-a-review-verdict.md](mirror-0922-fix-kanban-check-requires-where-runs-on-lands-the-card-never-refuse-a-review-verdict.md) | 0922 | `f46e5f476c` — fix(kanban): check requires where runs_on lands the card; never refuse a review verdict [daveey] |
| [mirror-0923-fix-kanban-name-a-workspace-file-that-shadows-a-stdlib-module-in-a-start-up-crash-blo.md](mirror-0923-fix-kanban-name-a-workspace-file-that-shadows-a-stdlib-module-in-a-start-up-crash-blo.md) | 0923 | `b3f78f0a90` — fix(kanban): name a workspace file that shadows a stdlib module in a start-up crash; block it at once [daveey] |
| [mirror-0924-fix-tests-deflake-background-profile-scope-teardown-181.md](mirror-0924-fix-tests-deflake-background-profile-scope-teardown-181.md) | 0924 | `af02121e95` — fix(tests): deflake background profile-scope teardown (#181) |
| [mirror-0925-fix-kanban-stop-an-ended-run-s-worker-scope-so-orphans-the-worker-left-die-with-it.md](mirror-0925-fix-kanban-stop-an-ended-run-s-worker-scope-so-orphans-the-worker-left-die-with-it.md) | 0925 | `d5b8545b4d` — fix(kanban): stop an ended run's worker scope so orphans the worker left die with it [daveey] |
| [mirror-0926-fix-kanban-a-capability-owner-card-is-not-a-duplicate-of-the-card-it-serves.md](mirror-0926-fix-kanban-a-capability-owner-card-is-not-a-duplicate-of-the-card-it-serves.md) | 0926 | `abee965bfb` — fix(kanban): a capability owner card is not a duplicate of the card it serves [daveey] |
| [mirror-0927-perf-skills-stat-keyed-frontmatter-cache-for-skill-lookup-kanban-admission-20s-timeou.md](mirror-0927-perf-skills-stat-keyed-frontmatter-cache-for-skill-lookup-kanban-admission-20s-timeou.md) | 0927 | `1bab43d41f` — perf(skills): stat-keyed frontmatter cache for skill lookup (kanban admission 20s timeouts) (#176) |
| [mirror-0928-test-tui-gateway-process-consumer-health-waits-use-60s-deadlines-not-5s-cpu-throttled.md](mirror-0928-test-tui-gateway-process-consumer-health-waits-use-60s-deadlines-not-5s-cpu-throttled.md) | 0928 | `9bff6b3d93` — test(tui_gateway): process-consumer-health waits use 60s deadlines, not 5s (CPU-throttled runners) |
| [mirror-0930-fix-tui-gateway-finalize-sessions-in-their-own-profile-scope-182.md](mirror-0930-fix-tui-gateway-finalize-sessions-in-their-own-profile-scope-182.md) | 0930 | `b9459ef5d0` — fix(tui_gateway): finalize sessions in their own profile scope [daveey] (#182) |
| [mirror-0931-fix-kanban-refuse-reclaim-of-a-foreign-live-lease.md](mirror-0931-fix-kanban-refuse-reclaim-of-a-foreign-live-lease.md) | 0931 | `0d3797b361` — fix(kanban): refuse reclaim of a foreign live lease [daveey] |
| [mirror-0932-test-gateway-control-reply-metrics-clock-to-avoid-scheduler-flakes.md](mirror-0932-test-gateway-control-reply-metrics-clock-to-avoid-scheduler-flakes.md) | 0932 | `8e836d76ea` — test(gateway): control reply metrics clock to avoid scheduler flakes |
| [mirror-0933-fix-kanban-don-t-kill-a-live-worker-for-a-lease-that-lapsed-while-the-mac-slept-192.md](mirror-0933-fix-kanban-don-t-kill-a-live-worker-for-a-lease-that-lapsed-while-the-mac-slept-192.md) | 0933 | `49dcb95c95` — fix(kanban): don't kill a live worker for a lease that lapsed while the Mac slept (#192) |
| [mirror-0934-fix-kanban-tag-pr-acceptance-completion-refusals-rejection-pr-acceptance-not-stale-ru.md](mirror-0934-fix-kanban-tag-pr-acceptance-completion-refusals-rejection-pr-acceptance-not-stale-ru.md) | 0934 | `3b678aeda7` — fix(kanban): tag PR-acceptance completion refusals rejection=pr_acceptance, not stale_run [daveey] |
| [mirror-0935-fix-kanban-one-real-board-enumerator-so-boards-archived-can-t-stop-workspace-gc.md](mirror-0935-fix-kanban-one-real-board-enumerator-so-boards-archived-can-t-stop-workspace-gc.md) | 0935 | `15c424d93d` — fix(kanban): one real-board enumerator so boards/_archived can't stop workspace GC [daveey] |
| [mirror-0936-fix-state-a-60s-state-db-writer-pauses-a-turn-instead-of-ending-it-name-the-lock-hold.md](mirror-0936-fix-state-a-60s-state-db-writer-pauses-a-turn-instead-of-ending-it-name-the-lock-hold.md) | 0936 | `4ee467592c` — fix(state): a >60s state.db writer pauses a turn instead of ending it; name the lock holder on macOS [daveey] |
| [mirror-0937-fix-kanban-self-heal-fix-card-may-requeue-the-card-it-repaired.md](mirror-0937-fix-kanban-self-heal-fix-card-may-requeue-the-card-it-repaired.md) | 0937 | `3eaf0d9d4a` — fix(kanban): self-heal fix card may requeue the card it repaired [daveey] |
| [mirror-0938-fix-kanban-refuse-an-explicit-runs-on-machine-pin-to-a-machine-that-does-not-pick-up-.md](mirror-0938-fix-kanban-refuse-an-explicit-runs-on-machine-pin-to-a-machine-that-does-not-pick-up-.md) | 0938 | `cc3d681cb9` — fix(kanban): refuse an explicit runs_on machine pin to a machine that does not pick up the board [daveey] |
| [mirror-0939-fix-kanban-remote-notices-persist-the-delivered-notice-memo-across-restarts.md](mirror-0939-fix-kanban-remote-notices-persist-the-delivered-notice-memo-across-restarts.md) | 0939 | `35c922ec06` — fix(kanban remote notices): persist the delivered-notice memo across restarts [daveey] |
| [mirror-0940-fix-kanban-pin-refusal-names-the-cross-gateway-route-that-works-from-this-gateway.md](mirror-0940-fix-kanban-pin-refusal-names-the-cross-gateway-route-that-works-from-this-gateway.md) | 0940 | `2bb1d3298b` — fix(kanban): pin refusal names the cross-gateway route that works from this gateway [daveey] |
| [mirror-0941-fix-tui-gateway-agent-marked-api-ws-clients-client-agent-never-stamp-last-human-input.md](mirror-0941-fix-tui-gateway-agent-marked-api-ws-clients-client-agent-never-stamp-last-human-input.md) | 0941 | `e216b50f29` — fix(tui_gateway): agent-marked /api/ws clients (?client=agent) never stamp last_human_input; their chats born hidden [daveey] |
| [mirror-0942-fix-kanban-a-plain-word-board-slug-is-not-a-board-reference.md](mirror-0942-fix-kanban-a-plain-word-board-slug-is-not-a-board-reference.md) | 0942 | `004623277d` — fix(kanban): a plain-word board slug is not a board reference [daveey] |
| [mirror-0943-test-gateway-await-interrupted-pending-drain-completion-195.md](mirror-0943-test-gateway-await-interrupted-pending-drain-completion-195.md) | 0943 | `28e3ab07c0` — test(gateway): await interrupted pending-drain completion (#195) |
| [mirror-0944-test-auth-await-openrouter-browser-callback-completion.md](mirror-0944-test-auth-await-openrouter-browser-callback-completion.md) | 0944 | `6a0c518337` — test(auth): await OpenRouter browser callback completion [daveey] |
| [mirror-0945-fix-kanban-placement-live-gateway-reaches-any-gateway-not-only-the-routed-target-refu.md](mirror-0945-fix-kanban-placement-live-gateway-reaches-any-gateway-not-only-the-routed-target-refu.md) | 0945 | `0bc5626cfe` — fix(kanban): placement=<live gateway> reaches any gateway, not only the routed target; refusal message updated [daveey] |
| [mirror-0946-fix-kanban-retire-all-stale-board-handles-before-wal-reopen.md](mirror-0946-fix-kanban-retire-all-stale-board-handles-before-wal-reopen.md) | 0946 | `1e1ec687ef` — fix(kanban): retire all stale board handles before WAL reopen [daveey] |
| [mirror-0947-extend-orphan-install-state-gc-with-idle-safe-hourly-host-sweeps.md](mirror-0947-extend-orphan-install-state-gc-with-idle-safe-hourly-host-sweeps.md) | 0947 | `cee338f7fc` — [daveey] Extend orphan install-state GC with idle-safe hourly host sweeps |
| [mirror-0948-fix-kanban-heal-missing-capture-gap-child-links-154.md](mirror-0948-fix-kanban-heal-missing-capture-gap-child-links-154.md) | 0948 | `6a72fdf911` — fix(kanban): heal missing capture-gap child links [daveey] (#154) |
| [mirror-0949-fix-kanban-drop-a-finished-run-s-identity-at-process-start.md](mirror-0949-fix-kanban-drop-a-finished-run-s-identity-at-process-start.md) | 0949 | `28ebf9034b` — fix(kanban): drop a finished run's identity at process start [daveey] |
| [mirror-0950-fix-kanban-print-a-finished-run-drop-on-stderr.md](mirror-0950-fix-kanban-print-a-finished-run-drop-on-stderr.md) | 0950 | `48f310fd7b` — fix(kanban): print a finished-run drop on stderr [daveey] |
| [mirror-0951-fix-kanban-drop-a-finished-run-from-startup-scrub-not-main.md](mirror-0951-fix-kanban-drop-a-finished-run-from-startup-scrub-not-main.md) | 0951 | `aaab5e3f47` — fix(kanban): drop a finished run from startup scrub, not main [daveey] |
| [mirror-0952-kanban-identity-mark-read-only-startup-probe-as-lockguard-exempt-fixes-lockguard-test.md](mirror-0952-kanban-identity-mark-read-only-startup-probe-as-lockguard-exempt-fixes-lockguard-test.md) | 0952 | `a83962fbcc` — [daveey] kanban_identity: mark read-only startup probe as lockguard-exempt (fixes lockguard test) |
| [mirror-0953-test-kanban-goal-mode-cli-gate-tests-use-a-task-id-shaped-id-1184aefb32-rejects-t1.md](mirror-0953-test-kanban-goal-mode-cli-gate-tests-use-a-task-id-shaped-id-1184aefb32-rejects-t1.md) | 0953 | `4d3b720714` — test(kanban): goal-mode CLI gate tests use a task-id-shaped id (1184aefb32 rejects 't1') [daveey] |
| [mirror-0954-test-kanban-goal-mode-gate-tests-assert-the-judge-was-not-called-so-an-earlier-refusa.md](mirror-0954-test-kanban-goal-mode-gate-tests-assert-the-judge-was-not-called-so-an-earlier-refusa.md) | 0954 | `2213c415ac` — test(kanban): goal-mode gate tests assert the judge was (not) called, so an earlier refusal cannot hollow them [daveey] |
| [mirror-0955-ci-e2e-upgrade-split-suites-into-4-file-shards-and-run-one-worker-per-slot-cpu.md](mirror-0955-ci-e2e-upgrade-split-suites-into-4-file-shards-and-run-one-worker-per-slot-cpu.md) | 0955 | `e8d99e7a39` — ci(e2e-upgrade): split suites into <=4-file shards and run one worker per slot CPU [daveey] |
| [mirror-0956-test-delegate-survive-a-throttled-runner-when-waiting-for-a-background-completion.md](mirror-0956-test-delegate-survive-a-throttled-runner-when-waiting-for-a-background-completion.md) | 0956 | `7b909338a4` — test(delegate): survive a throttled runner when waiting for a background completion [daveey] |
| [mirror-0957-test-tui-own-and-drain-goal-draft-rpc-workers.md](mirror-0957-test-tui-own-and-drain-goal-draft-rpc-workers.md) | 0957 | `c7058ec919` — test(tui): own and drain goal draft RPC workers [daveey] |
| [kanban-host-reboot.md](kanban-host-reboot.md) | 0958 | `43cb700f69` — Attribute host reboots; requeue bystanders without counting failures and trip an evidenced culprit. |
| [mirror-0959-fix-kanban-retry-guarded-terminal-scratch-cleanup-during-gc.md](mirror-0959-fix-kanban-retry-guarded-terminal-scratch-cleanup-during-gc.md) | 0959 | `4c2a3c5e25` — fix(kanban): retry guarded terminal scratch cleanup during GC [daveey] |
| [mirror-0960-test-kanban-pin-the-replica-clock-in-exact-stale-age-assertions.md](mirror-0960-test-kanban-pin-the-replica-clock-in-exact-stale-age-assertions.md) | 0960 | `8d288f1b68` — test(kanban): pin the replica clock in exact stale_age assertions [daveey] |

| [update-hold-queue-survives-restart.md](update-hold-queue-survives-restart.md) | 0900, 0901, 0929 | A message sent while the core-update drain window holds admission is journaled and replayed verbatim by the backend after the update restart (1 h bound), delivered once: a turn already started is continued, not resent; a renderer reload to the same backend leaves it to the live queue (0929); Stop discards it. The queued reply passes `PromptSubmitResult`. |
| [update-pause-terminal-frame-contract.md](update-pause-terminal-frame-contract.md) | 0906 | A core-update-paused turn's `message.complete` frame passes `MessageCompletePayload`: the deliberate `paused_for_update` flag is declared as an optional bool (generated TS/OpenRPC regenerated); ordinary Stops never carry it. |
| [cron-finalize-slow-write.md](cron-finalize-slow-write.md) | 0875 | A state.db write that outlives cron finalization's cleanup bound no longer crashes the job: finalization returns, the agent is torn down or handed off, and one WARNING names the job and session while a daemon thread retries title + end_session on a fresh connection. |
| [kanban-isolated-home-module-restore.md](kanban-isolated-home-module-restore.md) | 0872 | Test fixtures that drop hermes modules to see a temp HERMES_HOME put the process's module objects back, so a later file in the same pytest process patches the object it imported. Test harness only. |
| [turn-lease-busy-release.md](turn-lease-busy-release.md) | 0876 | A turn-lease release that loses the state.db write lock marks the holder abandoned (reclaimable by the next turn in-process) and retries the DELETE on a daemon thread; live leases are still refused. |
| [ws-orphan-resume-ctx.md](ws-orphan-resume-ctx.md) | 0788 | Orphan-race resume tests build ctx from a real `session.resume` `_Resume` instead of a duck-typed double, so a new ctx attribute cannot break the fixtures. Test harness only. |
| [kanban-worker-task-marker.md](kanban-worker-task-marker.md) | 0775 | A Kanban worker's tool children (terminal, background processes, execute_code) get `HERMES_KANBAN_WORKER_TASK=<card>` so shell tooling knows its card; `HERMES_KANBAN_TASK` stays scrubbed, and a process with only the marker is not a worker. |
| [held-session-refusal-backoff.md](held-session-refusal-backoff.md) | 0759 | A completion or other automatic turn refused because another backend holds the chat backs off (30 s doubling to 300 s) and shows one "open in another Hermes window or device" notice per streak instead of an "internal error" card every poll; the event waits and is delivered once the chat is free. |
| [serve-update-wait.md](serve-update-wait.md) | 0720, 0786 | `hermes serve` waits out a live core update (600 s) or hermes-sync's admission hold (60 s) before loading the agent, so no backend boots on a tree about to be swapped. 0786 re-execs on a `clear` gate when the cached git identity is not HEAD; a ci/build stamp is not compared. |
| [boot-pinned-git.md](boot-pinned-git.md) | 0776 | Boot-pinned imports resolve `git` through `locate_command`; a missing git disables blob reads; an explicit path skips discovery. |
| [update-pause-in-place-handoff.md](update-pause-in-place-handoff.md) | 0772, 0779, 0789, 0791, 0859, 0870 | A core-update-paused turn whose checkout moved waits for a successor backend only while one is coming (retiring, or skew watchdog + idle proof); with no watchdog it continues at once, kept busy 60 s it continues in place (backstop 300 s, was 900 s). 0779 stops owned non-persist background jobs for that pause, journals the outcome before the next kill, and lists them on resume; a failed write or kill refuses the pause. 0789 (`91f48a91`) journals an idle chat's owned non-persist jobs when the backend exits (`backend_shutdown`) and the next backend reports them; a notify job's receipt outlives the turn freshness window (24 h). 0791 re-arms the inherited pause watcher after a synthesized notification turn succeeds, raises, or is refused at preparation. 0859 (2nd attempt, `26691e1c`): from the window end until the continuation starts, automatic turns (notices, completions, /loop, heartbeats) requeue instead of claiming the paused chat (bounded); hand-off bounds count from the move even during a carried turn; a `coming` outlook that never begins retiring continues here after 90 s. 0870 (`9b77743e`): a /goal continuation waits for the paused turn instead of overwriting its marker. |
| [background-review-retirement-hold.md](background-review-retirement-hold.md) | 0735, 0758, 0761 | A running post-turn background review holds the backend retirement fence: skew/idle retirement and `hermes update`'s stale-dashboard stop (0758) wait for it, ask it to stop between provider calls after 300 s, and stops counting it at 900 s; a retiring backend skips new reviews cleanly. 0761 also holds an installation-root cross-process admission reservation for the review's whole run, so an update hold that already counts reservations sees a review with no turn lease, and a new review is refused while that hold stands. |
| [kanban-delegate-child-terminal-isolation.md](kanban-delegate-child-terminal-isolation.md) | 0704 | Delegate children keep their own terminal env; `--board` works from worker shells; `boards list` counts each board. |
| [kanban-blocked-create-needs-input.md](kanban-blocked-create-needs-input.md) | 0705 | A card created blocked with a human_request is classified `needs_input`. |
| [kanban-create-overlap.md](kanban-create-overlap.md) | 0865, 0877 | `kanban_create`, `hermes kanban create` and REST `POST /tasks` refuse a card that open cards on any board or gateway (live boards + hermes-hub replica) already cover, by board/repo name or title overlap, naming them (`code: create_overlap`); `distinct_from` + `distinct_reason` overrides; refusals/overrides are `create_overlap` events counted by the coach's `create_overlap_watch`. |
| [pm-range-server-threaded.md](pm-range-server-threaded.md) | 0706 | Test range server is threaded so a paused download stops in 0.04 s, not 3.7 s. |
| [kanban-wake-check-confirm.md](kanban-wake-check-confirm.md) | 0703 | A wake probe exiting 0 is confirmed by a second run; the reaper stops stealing probe statuses. |
| [retirement-ledger-bom.md](retirement-ledger-bom.md) | 0700 0701 | Retirement ledger reads BOM JSON; a nested `open(..., "rb")` is not a text-mode footgun. |
| [projects-tree-activity-mock.md](projects-tree-activity-mock.md) | 0699 | Projects tree activity test mocks `_all_counts(include_snapshot=...)`. Test harness only. |
| [kanban-unlink.md](kanban-unlink.md) | 0694 | `kanban_unlink` removes an edge the worker owns (orchestrators any); a worker may complete the card it just parked; a parked card with a live run pid is not a leaked-worker env. |
| [transcript-page-request-arg.md](transcript-page-request-arg.md) | 0693 | Direct history-page tests pass `Request` and an explicit `order`. Test harness only. |
| [model-provider-guard.md](model-provider-guard.md) | 0691 | A bare vendor model id on OpenRouter or Nous is refused before any request; API worker logs carry the session tag. |
| [kanban-skill-pin-validation.md](kanban-skill-pin-validation.md) | 0690 | A skill pin the assignee cannot load is refused at create (`unknown_skill`); a stale pin does not kill or retry a dispatcher worker. |
| [test-run-summary-errors.md](test-run-summary-errors.md) | 0689 | The parallel test summary appends `, N errored` when pytest reported setup errors. |
| [kanban-replica-human-asks.md](kanban-replica-human-asks.md) | 0687, 0688 | Replica human asks paint the chat red; unverified remote cards count as parked; a local+remote lineage counts once. |
| [ci-bwrap-e2e-upgrade.md](ci-bwrap-e2e-upgrade.md) | 0684 | `e2e-upgrade` runs on self-hosted hub slots labeled `ci-bwrap`. |
| [macos-venv-test-fixtures.md](macos-venv-test-fixtures.md) | 0755 | The source-driver PM-observer fixture links the base interpreter, and the packaged-launch test counts launches instead of every `subprocess.run`, so both pass under a copied-binary macOS venv. Test harness only. |
| [bootstrap-relaunch-test.md](bootstrap-relaunch-test.md) | 0683 | The real-bootstrap relaunch test imports its path fakes before the bootstrap preload. Test harness only. |
| [desktop-project-folder-create.md](desktop-project-folder-create.md) | 0681 | Move-to-project creates the session profile's folder when the gateway lacks the lane path. |
| [kanban-central-service.md](kanban-central-service.md) | 0698 0732 | Worker nodes (card runs): `runs_on` placement labels (`kanban create --runs-on`, `kanban_create(runs_on=...)`, `placement=<enrolled machine>` → `runs_on=[machine:<it>]`, and the create result says so); `kanban.sync.pickup` (`explicit` / `true`) copies eligible cards other gateways created from the replica and claims them through Board leases, which check the operator-set machine registry (labels, `pinned_only`, `hold`, `max_card_runs`); the creator's orphan/stale sweeps and concurrency counts skip cards running under another gateway's lease. |
| [kanban-local-lineage-counts.md](kanban-local-lineage-counts.md) | 0682, 0695 | Local cards shared across compression aliases count once, matching the task strip; parked, held and stalled subsets deduplicate with the same snapshot. Distinct boards/cards remain distinct. |
| [kanban-managed-pin-policy.md](kanban-managed-pin-policy.md) | 0685, 0778 | A model pin the managed routing registry cannot serve is refused at create and set-model (code `model_pin_not_in_routing_registry`), not at worker startup. 0778 clears a stored refused pin when no supervisor can take the assignment block, unless it is also a family mismatch, and dispatches that tick on the profile default. |
| [cron-managed-pin-preflight.md](cron-managed-pin-preflight.md) | 0879 | A cron job whose model the managed routing registry rejects is blocked_config at preflight (alert once), refused by `hermes cron create/edit`, and listed by `hermes cron doctor`; a wakeAgent=false / unchanged-monitor tick keeps the last real run's status, failure streak and alert marker. |
| [standing-instructions.md](standing-instructions.md) | 0678 | Standing instructions survive compaction word for word, reach workers ahead of the comment cap, and hold a don't-wait block once. |
| [bare-checkout-recover.md](bare-checkout-recover.md) | 0673, 0677 | `hermes update` resets `core.bare` on a work tree that `git init --bare` flipped; a genuine bare repo is left alone. The `.git` marker is read as utf-8-sig. |
| [model-id-whitespace-entry-parity.md](model-id-whitespace-entry-parity.md) | 0674 | `-m`/`-z`/`--tui` and Kanban model pins refuse spaced cloud model ids like `/model` does (exit 2 before any API call); Anthropic 404 `model: <id>` classifies as model_not_found. |
| [cron-script-sane-path.md](cron-script-sane-path.md) | 0676 | Cron scripts and the cron worker get the terminal's sane PATH (`~/.local/bin` and the standard dirs) whichever process won the tick lock; run docs name the runner process. |
| [spotlight-noindex-scratch.md](spotlight-noindex-scratch.md) | 0679 0692 | On macOS cache/scratch and cache/uv live in `*.noindex` dirs behind symlinks, so Hermes's temp churn stays out of Spotlight. Spotlight contracts run on the real macOS host, not a `sys.platform` fake. |
| [kanban-spawn-tests-host-independent.md](kanban-spawn-tests-host-independent.md) | 0675 | Kanban spawn env/PYTHONPATH tests pin the restart-safe scope wrapper so they pass with or without systemd. |
| [git-consumer-fallback-tests.md](git-consumer-fallback-tests.md) | 0672 | Inject consumer failures at public PM boundary; cover missing-runtime preflight without losing callback assertions. |
| [kanban-session-poll-coalescing.md](kanban-session-poll-coalescing.md) | 0671, 0869 | Overlapping session-list polls share one annotation refresh; failures remain unknown. 0869: a cold profile's poll behind another profile's or a failed read reads its own scope. |
| [kanban-wait-replica.md](kanban-wait-replica.md) | 0668, 0873, 0874, 0878 | Unreachable SSH card waits use fresh owner-qualified replica rows; a missing card or a future-dated replica is exit 2, orphan producers are refused, and automatic probes share the route. 0873, 0874: a card still in a stale replica of an offline gateway is exit 3 (unknown, not yet) until the replica is 24 h stale, then the card wakes with a gateway-offline note. |
| [live-git-config-test-isolation.md](live-git-config-test-isolation.md) | 0667 | Tests scrub inherited Git location overrides and restore modified live core.bare values. |
| [display-transcript-compression.md](display-transcript-compression.md) | 0666 0670 | Display-only transcript responses omit redundant provider fields and support gzip; sidebar click prepaints cached transcript. |
| [borrowed-home-bootstrap.md](borrowed-home-bootstrap.md) | 0665 | Bootstrap checkout dependencies before isolated borrowed-home user code; avoid re-exec on the store interpreter. |
| [kanban-pin-provider.md](kanban-pin-provider.md) | 0663 | Bind concrete model pins to a serving provider at create/set time; refuse missing or ambiguous providers; resolve legacy NULL-provider pins at dispatch. |
| [kanban-board-recovery.md](kanban-board-recovery.md) | 0640, 0641, 0642, 0643, 0644 0647, 0649 0657, 0658 0662 0669 | Out-of-process table b-tree salvage and in-place SQLite backup install, tombstone-aware reconcile, shared recovery lock with holder identity, contention/deleted-sidecar refusal and forensic reports. |
| [backend-activity-quiet-hint.md](backend-activity-quiet-hint.md) | 0639 | Backend last_active, not a window's event silence, decides a running turn's quiet hint. |
| [recovery-era-test-isolation.md](recovery-era-test-isolation.md) | 0637 0645 0649 0660, 0662 | Sandbox profile wrappers, reach the git fallback branch and synchronize downloader counters. |
| [kanban-descendant-wait-refusal.md](kanban-descendant-wait-refusal.md) | 0650 | Refuse explicit or inferred waits on a card's own descendants and mutual return waits. |
| [kanban-dispatch-fairness.md](kanban-dispatch-fairness.md) | 0652, 0653 | Rotate the first board on spawning ticks and measure stranded-ready age from the last wake or claim. |
| [kanban-unroutable-tier.md](kanban-unroutable-tier.md) | 0654 | Keep the profile model when an automatically chosen class has no eligible route; explicit pins stay unchanged. |
| [kanban-remote-reference-diagnostics.md](kanban-remote-reference-diagnostics.md) | 0659 | Replica-known remote ids are not phantom completion references; uncertain replica absence cannot prove a warning. |
| [kanban-central-service.md](kanban-central-service.md) | 0631 | With `kanban.sync.links_reader: replica` the sidebar count and the linked-card strip read one projection (`_remote_cards`): a done card whose release isn't verified stays an `unknown` row in both, staleness comes from the replica's last pull (`pulled_at`), an unreadable replica keeps its last rows as stale, a card shared across one lineage counts once, and `links-diff` compares `unknown` rows and is never clean over an unreadable replica (Upkeep showed 14 counted over 9 chips). |
| [tui-gateway-ws-peer-drop.md](tui-gateway-ws-peer-drop.md) | 0602 | A WebSocket peer dropped mid-send ends the read loop as a disconnect with one WARNING, not an ERROR "ws receive failed" traceback. |
| [browser-managed-leases.md](browser-managed-leases.md) | 0608 0609 | Every built-in browser tool (navigate, cdp, dialog, vault ops) leases the managed Chrome under `browser.managed_agent`; supervisors, sidecars and vault scope are bound to the lease. |
| [checkpoint-staging-cap.md](checkpoint-staging-cap.md) | 0613 0633 | A workspace deleted mid-snapshot is one WARNING; oversize files are excluded before `git add`, staging timeouts clean their debris and back off 60 min. |
| [kanban-lockguard.md](kanban-lockguard.md) | 0614 0616 | Every Kanban board connection takes the OFD lock guard and stranded readers reopen; dashboard requests no longer re-run board init + integrity_check. |
| [interrupt-reason.md](interrupt-reason.md) | 0630 0634 | Every interrupt producer records a fixed reason (user_stop, ws_orphan, backend_exit, lease_takeover, session_close) and phase on the interrupted row, the result and one INFO line. |
| [model-routing-managed-adoption.md](model-routing-managed-adoption.md) | 0617 | Live agents adopt the managed subscription policy when enforcement is enabled; aux and main round-robin counters are split. |
| [process-exit-log-noise.md](process-exit-log-noise.md) | 0620 | A process exit mid-turn logs one line, not a traceback, in the pre-final gate, tool executor and errors.log. |
| [test-host-slot-cap.md](test-host-slot-cap.md) | 0607 0702 | xdist workers are capped per host test slot (and by the host's `tests.xdist_workers`); a full `max_in_progress` is reported as idle, not stuck. |
| [hub-project-groups.md](hub-project-groups.md) | 0626 0627 | Every gateway's project groups come from the hub replica when that gateway can't be reached (or before it answers), so an asleep gateway's groups stay; another gateway's declared project can be renamed or themed from here as a hub shared field, applied by its owner when it wakes (`hermes project tree` / `project update`). |
| [websockets-keepalive-logging.md](websockets-keepalive-logging.md) | 0622, 1415 | A websockets peer that stops answering pings is a handled drop: every Hermes connection passes `logger=ws_logger()`, so the library's `ERROR keepalive ping failed` + traceback becomes one INFO line (errors.log and self-heal stay quiet; Kanban sync still reconnects). code_health HX015 flags a new `connect()`/`serve()` without `logger=`. 1415 also demotes the library's `unexpected internal error` / `EOFError("stream ended")` handshake-timeout race to INFO. |
| [desktop-planned-exit-notice.md](desktop-planned-exit-notice.md) | 0972 | A backend stopped by a core update (live update marker) shows a quiet "Updating Hermes…" notice instead of the red "stopped working in the background" card, never fails the boot overlay; crash cards dedupe by id and clear when the backend serves again. |
| [desktop-remote-update-pause.md](desktop-remote-update-pause.md) | 0605 0606 | While a remote gateway runs its own `hermes update`, its SSH dials pause per host: requests and prewarm fail fast from the cached refusal and one probe per backoff window (2–30 s) checks for the end, instead of a new SSH control master per request (53 in 108 s on 2026-10-27); the connection comes back once. |
| [desktop-remote-dial-backoff.md](desktop-remote-dial-backoff.md) | 0618 | A remote that cannot be dialed (host down, refused, SSH timeout, rejected credentials) is remembered by the same gate as 0605: requests and prewarm fail fast from the cached error and one probe per backoff window (2–60 s) checks for it, instead of one SSH control master and a 15 s wait per request (11 dials, 36 stalled requests in 2 min on 2026-10-27); Reload and retry, apply/save and a reachable Test dial at once. |
| [kanban-central-service.md](kanban-central-service.md) | 0590, 0787 | A stale feed copy cannot reopen a locally ended run: pending child-row edits and acknowledged run versions fence older copies; the orphan pass closes non-current runs older than ten minutes (`close_dangling_runs`). 0787 restores a run the card already ended from its terminal event instead of rewriting it to `reclaimed`. |
| [desktop-stale-list-open.md](desktop-stale-list-open.md) | 0584, 0585, 0586, 0587 0598 | A sidebar row kept from a failed session list (busy local backend) still opens live, and the notice and tooltip name its connection as "not responding" instead of "its gateway is offline … never synced"; replica rows of unreachable gateways are unchanged. |
| [kanban-default-placement.md](kanban-default-placement.md) | 0571 0573 0575 0578 0600 0601 0636 0651 0655 0715 | `kanban.placement.route` (e.g. `{macbook: hub}`): a `kanban_create` card with no local-only need is created on the target through `hermes-hub create-task`, bound to its origin chat, and the call returns the target's task id (`hermes-hub outcomes`, 10 s wait) or `status: queued` with a note that the card IS created; an identical retry is never sent twice; stays local for parents, projects, dir/worktree workspaces, uncarried options, local markers or a non-live target; a caller pin to a gateway it cannot reach is refused (`placement_unavailable`), never a silent local card. `hub_cli_path()` finds `hermes-hub` without the login PATH. A card with parents is created here and forwarded to the target once its parents finish (0715). |
| [sidebar-boot-snapshot.md](sidebar-boot-snapshot.md) | 0577 0623 | Cold start paints the sidebar (every gateway's hub-replica rows and the last projects tree) from a snapshot Electron main reads before the backend is up; the CONNECTING overlay stays down; a failed replica read publishes nothing; `[sidebar-timing]` log line. |
| [anthropic-pool-pin.md](anthropic-pool-pin.md) | 0579 | A session pinned to a credential-pool account keeps that account's token on every request (no swap back to an expired first entry); expired entries skipped by model-aware resolution; drift warning in 401 recovery. |
| [harness-daemon-owner-reap.md](harness-daemon-owner-reap.md) | 0594 0595 | A named browser-harness daemon (`browser_exec` `session=`) is stopped once no live process that drove it remains, or after `browser.harness_daemon_idle_s` (2 h) idle, from `browser_exec` and every process exit; the shared default daemon is kept; the `session` description says to omit it. |
| [terminal-exit-kill-waits-for-spawns.md](terminal-exit-kill-waits-for-spawns.md) | 0583 | The graceful exit kill waits (up to 5 s) for foreground commands that have forked but are not yet registered, so a process exit no longer orphans them. |
| [sessions-list-preview-page-only.md](sessions-list-preview-page-only.md) | 0533 | The sidebar session list computes the preview subquery for the page rows only, not every admitted session (1.39 s to 0.35 s on a 12 GB store). |
| [sidebar-poll-reads.md](sidebar-poll-reads.md) | 0656 | One sidebar poll reads each store once: its three slices share one kanban snapshot and remote projection, boards are read on one cached connection and memoized per board, the session list filters and computes last_active once per pass and back-fills pins from the pinned ids, and the replica memo survives hub pulls: sidebar CPU 167-176 to 79-86 ms per 2 s poll (Desktop's query 85-116 to 56-63), same responses. |
| [kanban-replica-read-memo.md](kanban-replica-read-memo.md) | 0592 | The sidebar's remote-links projection re-reads hub.db's ~6k replica task rows only when an index-only seq probe says they changed (or a stale/heartbeat cut-off passes), and matches legacy cards to receipt rows by index: remote projection 49 to 25 ms per sidebar poll, same answers. |
| [kanban-replica-decode-memo.md](kanban-replica-decode-memo.md) | 0536 | Replica rows are decoded and hashed once per distinct stored text, not on every remote-links rebuild. |
| [sidebar-stripe-transition-log.md](sidebar-stripe-transition-log.md) | 0534 | Each sidebar row logs `stripe-transition old->new` with its row inputs to desktop.log (rate-capped). |
| [session-info-queued-switch-provider.md](session-info-queued-switch-provider.md) | 0535 | `session.info` keeps a queued model switch's provider; goal tests stop faking `Path.home()`. |
| [global-context-files.md](global-context-files.md) | 0568 0574 | `agent.global_context_files` (e.g. `["~/AGENTS.md"]`): user-level rules load in every session of the profile whatever its cwd (Kanban scratch dirs, cron without a workdir, project chats), ahead of the project chain, once when the cwd chain already loads the same file; `/context` lists the rows. |
| [wal-holder-scan-single-flight.md](wal-holder-scan-single-flight.md) | 0565 0567 0580 | macOS deleted-WAL holder scan runs at most one libproc pass per process (joiners share it, a pass past its deadline fails open at once and stops itself), and a clean verdict is cached only while the db directory and sidecar identities are unchanged. |
| [backend-writable-open-guard.md](backend-writable-open-guard.md) | 0566, 0591, 0621 | A call site that keeps opening writable `SessionDB()` handles (30+ a minute) is named in a WARNING with the read-only / registry fix; chat-closeout reads compression lineage read-only, ending the 23-writable-opens-per-sidebar-poll loop that held the Desktop backend near 100% CPU; the per-chat Kanban notify poller skips boards whose files have not moved; `GET /board` reuses its read while the board is unchanged. |
| [kanban-owner-wake-visible-waits.md](kanban-owner-wake-visible-waits.md) | 0557, 0558, 0559, 0560 0570 | Every parked card names what it waits on and since when (`wait`: producer, agent-owned, premise); agent-owned waits over 2 h paint an amber "stalled" sidebar stripe/count; the tracker default `wait-check` wakes on owner review/block/triage/agent-owned parks without re-waking for a status it already handled. Pairs with the chat-closeout owner wake. |
| [instant-session-loads-cache.md](instant-session-loads-cache.md) | 0545, 0546, 0564 0628 0680 | A click paints the chat from a local SQLite transcript cache that a background prefetcher keeps current for every gateway's 200 most recent sessions; the REST read starts at the click; an asleep gateway's chat opens read-only; multi-segment lineage reads page in SQL; per-open timing log. An open Inbox card's hidden or unlisted session is prefetched too. |
| [notification-held-handoff.md](notification-held-handoff.md) | 0541, 0582 | Owner-addressed notifications wait with their owner's poller instead of bouncing through every chat's poller on the shared queue (a busy chat no longer pins the Desktop backend); sidebar kanban counts recompute once for all queued readers; each chat's kanban poll reads only its own subscriptions. |
| [desktop-sidebar-replica-live.md](desktop-sidebar-replica-live.md) | 0537 | Every gateway's sessions show from the hub replica at once on a cold start (no SSH wait); owners that published within 180 s give ordinary, running-aware rows. |
| [kanban-no-producer-no-park.md](kanban-no-producer-no-park.md) | 0540 0603 0632 0773 | Every Kanban park records who produces its condition (`awaiting` = card / ci / deploy / external / clock); agent-action waits with no producing card (`agent_action_wait`) and hidden waits on the user (`human_wait`, ask-gate ledger aware) are refused; the third park on one premise becomes an owner card for a supervisor. 0773 refuses a human wait even when the external ref has a number or URL. |
| [exhausted-provider-recovery.md](exhausted-provider-recovery.md) | 0528 | Exhausted providers (`failure_kind` exhausted/auth_missing, `provider`, nullable `reset_at` in UNIX seconds) open a recovery screen naming the provider and reset time instead of the first-run picker; nine locales. |
| [kanban-central-sync-client.md](kanban-central-sync-client.md) | 0472, 0474, 0476, 0478, 0481, 0509, 0510, 0513, 0514, 0515, 0516, 0547 | Central Board sync client: pull-then-push, shadow/import/diff, full-row sends, transient-rejection backoff, capture-gap fix, push feed, synced chat links, seconds columns normalized on apply. |
| [kanban-remote-observation-scope.md](kanban-remote-observation-scope.md) | 0467, 0468, 0490, 0491, 0492, 0497, 0504, 0505, 0507, 0508, 0512, 0532, 0556, 0619 | Remote kanban observations scoped to linked cards, non-blocking remote-links projection, retained-row evidence refresh in Desktop; projection keyed on replica content (0619). |
| [prompt-admission.md](prompt-admission.md) | 0447, 0448, 0449, 0450, 0452 | Durable exact-owner prompt admission with atomic cancel/reopen and a projected admitted user-row event. |
| [provider-fallback-and-token-routing.md](provider-fallback-and-token-routing.md) | 0486, 0487, 0511, 0527 | Fallback failures preserved, quota holds bounded to whole chains; routing caps compare token estimates, not bytes. |
| [model-pill-stored-model.md](model-pill-stored-model.md) | 0473, 0484, 0495 | Composer pill and effort menu use the chat's stored model while its agent is unloaded. |
| [update-admission-fence.md](update-admission-fence.md) | 0503 1078 1588 | Cross-process admission fence for Desktop reservations and Kanban claims during `hermes update`. |
| [final-output-withhold-and-submit-survival.md](final-output-withhold-and-submit-survival.md) | 0479, 0500 | Rejected final output withheld until final transforms; an accepted submit survives a client socket drop. |
| [scratch-home-metrics-and-ci-stability.md](scratch-home-metrics-and-ci-stability.md) | 0471, 0485, 0496, 0498, 0499, 0501, 0502, 0506, 0526, 0531 | Scratch-home isolation, shared-metrics offer never shown, and CI/portability fixes. |
| [kanban-claim-lease-fencing.md](kanban-claim-lease-fencing.md) | 0530 0543 1072 | Reset heartbeat at claim and fence stale lease echoes with the authoritative grant version, without dropping unrelated task updates. |
| [pm-target-gap-no-worker.md](pm-target-gap-no-worker.md) | 0624 | `pm.ensure()` refuses a package's target gap (POSIX git) in-process instead of spawning a PM worker per call; `selected_git_env` no longer starts an interpreter on every internal git call. |
| [test-runner-host-slots.md](test-runner-host-slots.md) | 0542 0544 0569 0572 | Concurrent `run_tests.sh` runs share a host-wide cap of `cpu_count` pytest file processes (flock slots in `/tmp/hermes-test-slots-<uid>`), instead of each starting `cpu_count`; nested runs skip it. |
| [gateway-launchd-core-update-launcher.md](gateway-launchd-core-update-launcher.md) | 0539 | The launchd gateway plist runs `stderr_timestamp` under an installed core-update launcher (`<root>/bin/hermes-gateway-launcher`), inside the shim's `sh -c exec` string, so plist refreshes keep the launcher; `gateway.status` never takes the launcher's process for the gateway. |
| [kanban-unloaded-sweep-wake.md](kanban-unloaded-sweep-wake.md) | 0525 | Unclaimed kanban notices start one agent turn (worker thread, budget, skip rules) instead of only a transcript row; a held chat whose poller missed is logged as a "poller miss". |
| [kanban-read-connection-cache.md](kanban-read-connection-cache.md) | 0521 | Reuse read-only board connections while preserving fresh commits, replacement detection and nonblocking checkpoints. |
| [onboarding-defer-default.md](onboarding-defer-default.md) | 0520, 0522 | The provider overlay no longer overwrites a configured default model: on a configured install every write waits for Begin on the confirm card (which says it changes the default for new chats); dismiss writes nothing. |
| [kanban-sync-client-fixes.md](kanban-sync-client-fixes.md) | 0518, 0519 | Sync client retries rejected rows under a fresh op id, skips non-board folders, keeps every chat of a shared card and syncs link-only boards. |
| [kanban-parked-vs-live-sidebar.md](kanban-parked-vs-live-sidebar.md) | 0517 | Sidebar stripe tells live kanban work (no stripe) from parked cards (grey) and queues held behind a human ask (red); new `kanban_parked`/`kanban_held` row stamps. |
| [model-class-routing.md](model-class-routing.md) | 0411, 0412, 0413, 0414, 0415, 0416, 0417, 0418, 0419, 0420, 0421, 0422, 0423, 0424, 0425, 0426, 0427, 0428, 0429, 0430, 0431, 0432, 0433, 0434, 0435, 0436 0437, 0438, 0439, 0440, 0441, 0442, 0443 0446 | Opt-in sticky class allocator (`class:deep|standard|fast`) with managed-subscription fences across CLI, Desktop, API, messaging, delegation, cron, Kanban and auxiliary calls. |
| [desktop-agent-test-instance-hidden.md](desktop-agent-test-instance-hidden.md) | 0475 | A Desktop test instance a kanban worker launches gets no Dock icon and never shows a window; the owner's own Desktop stays visible. |
| [at-completion-refresh.md](at-completion-refresh.md) | 0470 | `@` completion sources can `subscribe` so an open popover re-asks when their background fetch lands. |
| [core-update-drain-hold.md](core-update-drain-hold.md) | 0465, 0477 | A live `<home>/core-update/drain-hold` stops new Kanban claims so a saturated board drains for a core update; running workers are untouched, and wake probes, promotion and reclaim keep running (0477). |
| [kanban-provider-quota-walls.md](kanban-provider-quota-walls.md) | 0463 | Record provider retry-after walls and re-tier walled Kanban cards, with a storm alert. |
| [kanban-ownerless-blocks.md](kanban-ownerless-blocks.md) | 0462 0596, 0597 | Refuse ownerless agent blocks (incl. needs_input with no human_request) and undecided blocked cards; sweep old ones through triage once. |
| [kanban-worker-crash-oom-recovery.md](kanban-worker-crash-oom-recovery.md) | 0444 0460 0576 0593 0871 | Scope-killed workers count as crashes; typed OOM recovery raises MemoryMax with admission and alerts on OOM rate. 0871: `note_oom_kill` reuses the reclaim connection so the OOM alert does not deadlock on a second write transaction. |
| [kanban-triage-sweep-default-assignee.md](kanban-triage-sweep-default-assignee.md) | 0458 | CLI create defaults the assignee; stale triage cards are swept to the default profile once. |
| [desktop-remote-backend-reuse.md](desktop-remote-backend-reuse.md) | 0454, 0455, 0456 | Release instead of kill remote SSH backends on quit/resume, macOS launcher ownership proof, reuse-miss logging. |
| [desktop-always-on-remote-dashboard.md](desktop-always-on-remote-dashboard.md) | 0538, 0548, 0612, 0682, 0695, 0718 | Always-on supervised remote dashboard (launchd/systemd, no idle exit, retires on a replaced runtime, never in a loop: PM ignores runtime markers, identical re-realizations reattach, 15 min retire backoff), startup prewarm (default + recently used named profiles) + keepalive reconnect, 30 s dispatch probe skip, `[remote-timing]` logs. |
| [desktop-home-session-placement.md](desktop-home-session-placement.md) | 0410 | New cwd-only sessions keep a visible provisional or Home placement in every sidebar view. |
| [kanban-sync-seams.md](kanban-sync-seams.md) | 0451 0464 | Phase 1 of the central Kanban sync service, off by default (`kanban.sync.enabled`): outbox capture triggers with a drift guard, `apply_remote_changes`, lease seams in claim/heartbeat/stale-claim, tombstoned deletes, wide ids, dispatcher wake; no network code yet |
| [kanban-actionability-durable-evidence.md](kanban-actionability-durable-evidence.md) | 0343, 0361, 0364, 0365, 0366, 0387, 0392, 0400, 0401, 0402, 0403, 0404, 0405, 0406, 0407 0402 0408 | Merge-adapted actionable escalation, scope heartbeat freshness, exact legacy attestation with a distinct wire action, contention timestamp retention, and test followups; locally verified candidate |
| [unresolved-goal-fallback.md](unresolved-goal-fallback.md) | 0399 | Optional structured `pre_final_gate` fallback retained at budget/cap/unavailable boundaries, ownership-bound rechecks, private held text, scoped Kanban/process/dispatcher health receipts; enforcement stays opt-in (coach plugin) |
| [retention-observation.md](retention-observation.md) | 0398 | Fail-closed, observation-only non-CI retention slice (`retention_cli observe`); cannot delete or authorize deletion |
| [kanban-origin-adoption.md](kanban-origin-adoption.md) | 0385, 0386, 0388, 0774 | State-preserving existing-task origin adoption with immutable request replay/conflict checks. 0774 selects the Kanban toolset around trusted-courier dispatch so a worker-only profile is not stuck `kanban_tool_unavailable`. |
| [kanban-local-owner-projection.md](kanban-local-owner-projection.md) | 0390, 0391, 0393, 0394, 0395, 0396, 0397 0664 | Exact local owner evidence and alias-qualified dedup, foreign-profile preservation, nonblocking ledger refresh, one scoped annotation snapshot and per-projection alias-read reuse; live acceptance remains separate. Readers lock the ledger only to advance an observation, and a busy refresh no longer flips every hub row stale (0664). |
| [kanban-origin-work-ledger.md](kanban-origin-work-ledger.md) | 0221 0223 0242, 0243, 0244, 0245, 0246, 0247 0268 0270 0273, 0274, 0275, 0276, 0277, 0278, 0279 0281, 0282 0293 0295, 0296, 0297 0299 0311 0313, 0314 0316 0324, 0325, 0326, 0327, 0328 0342 0363 0409 0461 0777 | Owner-qualified remote work, durable binding/creation receipts, fresh lifecycle evidence, routed drawers/archive checks and deferred completion notices. 0777 verifies pre-receipt goal completions from the closing run, matches a long handoff against that run's full summary, and closes an owner-observed archive as `closed_uncompleted` instead of leaving it unfinished. |
| [kanban-origin-ownership-snapshot.md](kanban-origin-ownership-snapshot.md) | 0523, 0524 0589 | Read-only, query_only origin ownership snapshot: binding generation, owner running-claim/wake proof and scoped consumer health with epoch; unknown without proof. |
| [kanban-agent-recovery-routing.md](kanban-agent-recovery-routing.md) | 0269 0301 0307, 0308 0334 0370 | Current-run continuation exhaustion and automatic stops route to agent recovery while genuine human asks remain visible |
| [kanban-assignment-capabilities.md](kanban-assignment-capabilities.md) | 0300 0336 | Shared configured-capability/model preflight and bounded durable assignment repair |
| [kanban-capability-routing.md](kanban-capability-routing.md) | 0549, 0550, 0551, 0552, 0553 0562 0589 0696 | Cross-gateway capability routing: live-checked capability registry shared via hermes-hub, `needs` on block/schedule opens one owner card on the holding gateway and parks on it, typed refusals (no holder / stale / held locally / exhausted), placement preflight for namespaced `requires`. The owner wait-check reads the owner id from every applier result shape. |
| [kanban-strong-tier.md](kanban-strong-tier.md) | 0554 0555 0563 | `kanban.strong_model` / `strong_provider` name the model Kanban review, retry-after-failure and keep-pattern cards run on (instead of following the profile default); `cron.model_provider` applies only to jobs whose model came from `cron.model`, so a provider-less pinned cron job runs on its own model's provider. |
| [registry-backend-reconnect.md](registry-backend-reconnect.md) | 0309, 0310 0315 0317, 0318, 0319, 0320, 0321, 0322, 0323 | Exact-generation Linux registry reconnect, atomic retirement and stopped-owner recovery; repeated source identities retained |
| [kanban-pr-reconciliation-wakes.md](kanban-pr-reconciliation-wakes.md) | 0339 0344, 0345 0347 | Explicit checkpoints do not self-rearm holds; terminal PR and verified supersession wakes reconcile without claiming acceptance |
| [kanban-dispatch-ownership.md](kanban-dispatch-ownership.md) | 0367, 0368 0371, 0372, 0373, 0374 | Canonical-database dispatch authority, native-connection fencing, lifecycle contention and per-board pre-work isolation |
| [process-ci-test-followups.md](process-ci-test-followups.md) | 0257 0262, 0263 0346 0348, 0349 0358 0445 0453 | Fresh descendant cleanup, bounded event waits, cancellation handoffs, attribution and repeated Nix pipefail fix |
| [managed-chrome-leases.md](managed-chrome-leases.md) | 0375, 0376, 0377 | Experimental, not release-approved: opt-in owner-scoped default/local/named browser requests, selected vault scope and task/shutdown lease cleanup; configuration docs corrected locally, exact-head CI and changed-head review remain blockers; native core repetition and genuine credential use are unexercised, not claimed |
| [ci-poll-runner-routing.md](ci-poll-runner-routing.md) | 0330, 0331, 0338 | Poll-only CI routing, cancellation-safe aggregators and Detect timeout |
| [kanban-pr-park-handoff.md](kanban-pr-park-handoff.md) | 0350 | Orderly park releases PR guard; broker-aware PR-state reads |
| [orphan-reaper-home-scope.md](orphan-reaper-home-scope.md) | 0353 | Orphan reaping requires proven home/UID ownership and fresh process identity |
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
| [kanban-condition-wakes.md](kanban-condition-wakes.md) | 0261 0298 0457 | Condition-driven scheduled wakes with generation fencing and shared-park guards |
| [inline-preview-full-document.md](inline-preview-full-document.md) | 0264 | Whole-document inline previews |
| [kanban-default-provider.md](kanban-default-provider.md) | 0265 | Default tier provider inference |
| [test-live-git-config-guard.md](test-live-git-config-guard.md) | 0266 0271 | Protect live git identity during tests |
| [test-real-home-guard-checkout-probes.md](test-real-home-guard-checkout-probes.md) | 0635 | The tests' real-home guard lets a checkout under a guarded root read its own `install-stamp.json` / `installs/*/facts.json` probes; writes stay refused. |
| [kanban-worker-scope-failures.md](kanban-worker-scope-failures.md) | 0267 | Diagnose worker OOM and signal deaths |
| [kanban-dependency-review-continuation.md](kanban-dependency-review-continuation.md) | 0383, 0384 | Authenticated dependency parks permit one same-PR repair after structured independent review; claim release remains transactional |
| [archive-linked-confirm.md](archive-linked-confirm.md) | 0110 0389 0561 0615 | Archiving a chat with open Kanban tasks or rented machines asks first, listing them; plugins add checks via `ctx.registerArchiveGuard` |
| [screen-share.md](screen-share.md) | 0109 | Inline Screen Share plugin opens strictly validated VNC addresses on the client Desktop OS, never the remote gateway |
| [session-navigation-shortcuts.md](session-navigation-shortcuts.md) | 0019 0021 0022 0026 0033 0035 0043 0048 0049 0050 0065 | ⌘]/⌘[ next/prev session with a green/red status stripe (stepping from the focused tab), ⌘B mark blocked (grey, skipped), ⌘⇧]/⌘⇧[ any session, ⌘⇧A archive-and-advance, ⌘R resume (⌥⌘R in 0064, 0065, 0066, 0067, 0068, 0069, 0070), ⌥⇧⌘R window reload |
| [ai-session-rename.md](ai-session-rename.md) | 0020 0032 0042 | ⌘⇧E rename (was ⌘⇧N until 0042), pre-filled with an AI short name (`session.title.suggest` RPC) |
| [project-palette.md](project-palette.md) | 0004 0018 0030 0061 | ⌘⇧P moves a session to a project by typeahead (a new name offers "Create project" with an empty `~/code/<slug>`); typing a project name in ⌘P lists "Move to project: <name>" |
| [multi-gateway-projects.md](multi-gateway-projects.md) | 0023 0024 0025 0028 0029 0031 0054 0101 0149 0150 | Sidebar shows every connected gateway's projects (colored, with show/hide toggles); move a session into any gateway's project; archive on the owning gateway; `@session` ref clicks open on the owning gateway; the overview reads each remote's all-profiles tree, and This device's chats never resume on a selected remote |
| [sidebar-status-stripe.md](sidebar-status-stripe.md) | 0038 0039 0044 0051 0063 0070 0089 0091 0094 0095 0099 0104 0124 0215 | Sidebar row stripe shows status (green = done, waiting on you; red = stuck), never green while its Kanban tasks (also cards it shepherds on other gateways, read from the hermes-hub replica), `/goal` or a live background process are still running (project-mode rows past the recents page included) (and the backend no longer reaps a detached session whose background job still runs); a session that owns a live cron job shows a grey loop glyph and is skipped like a parked row; the row shows open/blocked Kanban task counts and a laptop / Mac mini / cloud glyph for the owning gateway; the travelling arc marks only the selected row |
| [new-chat-gateway-hotkeys.md](new-chat-gateway-hotkeys.md) | 0042 | ⌘N starts the new chat on the saved new-session gateway (0813), else This device (switches the gateway to local); ⌘⇧N keeps the selected gateway |
| [new-session-target.md](new-session-target.md) | 0813, 0816 | Device switcher menu gains "New sessions on ▸" (gateway picker): sticky, survives relaunch, unchanged by opening sessions elsewhere; ⌘N, palette and shell New session use it; unreachable target offers This device for that one session |
| [persistent-new-session-draft.md](persistent-new-session-draft.md) | 0036 0170 | ⌘N returns to the unsent new-session draft (text + attachments) instead of an empty composer; the dead-session prune no longer deletes it after you switch away |
| [session-takeover.md](session-takeover.md) | 0013 0014 0015 0060 | "Take over here" for a chat that's live in another Hermes window, naming the machine that holds it; a hidden chat's agent keeps its Browser page |
| [auto-continue-resume.md](auto-continue-resume.md) | 0008 0010 | `desktop.auto_continue.resume_on_startup`: interrupted sessions resume when the backend boots, including turns cut off by a backend shutdown |
| [auto-continue-stored-source.md](auto-continue-stored-source.md) | 0785 | A client-less auto-continue (startup sweep or in-place update-pause watcher) runs as the session's stored `desktop`/`tui` source instead of the backend's env fallback, rebuilding an agent built for another platform; unknown sources keep the default. |
| [fork-updates-and-relaunch.md](fork-updates-and-relaunch.md) | 0007 0011 0012 0016 0066 0697 0730 | **Start here if you'll carry patches.** `updates.fork_merge_upstream` lets `hermes update` merge upstream into your fork. Also: the app relaunches after a headless rebuild, `hermes serve --stop` spares Desktop's backend, no "Switch to This device?" prompt, and `HERMES_DESKTOP_PREBUILT_APP` installs a prebuilt app instead of packing (also one built from an ancestor of HEAD when no desktop input changed since), and it stays current after the stage that packed it moves on |
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
| [kanban-timed-waits.md](kanban-timed-waits.md) | 0113 0153 0155 0231 0378 | Kanban waits on time park `scheduled` with a wake (`kind='time'`, a parent-less dependency or `SCHEDULED_UNTIL=`; default 24h), not as a red human block; later comments no longer mask a due wake; `overdue_wake` diagnostic; only human blocks count as blocked in the sidebar |
| [pre-verify-every-turn.md](pre-verify-every-turn.md) | 0052 | `agent.pre_verify_every_turn: true` runs the `pre_verify` plugin hook on every final answer, not only after file edits |
| [skills-review-fork-count.md](skills-review-fork-count.md) | 0055 0097 0128 | The background skill-review fork can patch a skill the session already loaded, as long as the loaded copy still matches the file on disk; a refused write hands back the current file so the next call is the corrected write |
| [process-readoption.md](process-readoption.md) | 0069 0072 0073 0076 0077 0085 | Background jobs stay in the shared `processes.json` while other Hermes processes run; after a Desktop restart the backend re-adopts the orphaned ones and wakes their chat when they exit, even one you haven't opened |
| [gateway-exit-diagnostics.md](gateway-exit-diagnostics.md) | 0074 0075 0081 0611 0629 | Gateway exit records carry an info/warning/error level, so routine restarts stop showing as errors; fewer false Slack/websocket errors; `hermes update` finds a gateway whose interpreter path has a space |
| [credential-pool-429-rotation.md](credential-pool-429-rotation.md) | 0078 | A 429 with a reset window over 60 s rotates to another pooled key right away instead of sleeping out the window |
| [account-limit-causes.md](account-limit-causes.md) | 0133 0134 0144 0340 0469 | Fresh usage with quota left lifts a stale quota bench (early subscription reset); an auxiliary 429 benches only the failed model on the failed key (billing/auth stay account-wide); pooled usage rows say why an account is limited (quota vs cooldown, reason, expiry, model-only cooldowns); a duplicate login's stale bench no longer paints a usable account red |
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
| [turn-stop-notice.md](turn-stop-notice.md) | 0126, 1323, 1431 | A client Stop names its issuer and gesture; SSH Desktop reads "Stopped by Desktop". Editor Escape calls haltRun('esc'), independently of the non-editor Escape hook, with a mounted real ChatBar regression. |
| [kanban-open-task-hook.md](kanban-open-task-hook.md) | 0127 | `window.__hermesKanbanOpenTask(taskId, board?, gateway?)` lets disk plugins open a card's drawer on its own board and gateway |
| [review-evidence-gate.md](review-evidence-gate.md) | 0129 | `auxiliary.background_review.require_evidence` makes background skill review edit a skill only when a specific moment in the conversation justifies it; "Nothing to save." becomes a neutral outcome |
| [kanban-block-instructions.md](kanban-block-instructions.md) | 0130 | `kanban_block`'s `reason` asks for ask-first, numbered, concrete unblock steps (what the blocked-panel Desktop plugin shows above the composer) |
| [sidebar-row-actions.md](sidebar-row-actions.md) | 0131 | `SidebarSectionRow` takes hover-revealed `actions` and a `strong` (unread) label, for the inbox plugin's per-row Archive |
| [kanban-notice-refusal-rewind.md](kanban-notice-refusal-rewind.md) | 0132 0792 0793 | Refused notices rewind for the owning backend; 0792 keeps local tui claims in flight until a deduplicated transcript row is durable and recovers dead holders; 0793 tests a notice arriving during compaction landing once in the new tip. |
| [kanban-goal-handoff-judge.md](kanban-goal-handoff-judge.md) | 0135 | Goal-mode handoff judge treats the `kanban_complete` / `kanban_request_review` call it gates as the card's "finish with" step, so such cards can reach review |
| [goal-status-only-not-progress.md](goal-status-only-not-progress.md) | 0529 | `/goal` and Kanban goal-mode continuation prompts say a status-only turn is not progress: take the next concrete action or name the exact blocker |
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
| [delegate-route-conflicts.md](delegate-route-conflicts.md) | 0356 | Offline model/provider coherence preflight rejects incompatible delegated batches before spawning |
| [delegate-oneshot-budget.md](delegate-oneshot-budget.md) | 0357 | Advertise the one-shot total delegation budget and distinguish oversized requests from exhaustion |
| [kanban-unfinished-visibility.md](kanban-unfinished-visibility.md) | 0359 | Blocked-only work stays unfinished in both Desktop dots; archive writes invalidate cached counts; conditional child holds require immediate human action |
| [model-tiers.md](model-tiers.md) | 0151 0152 0302 | Lazy selected-route preflight; per-task `model`/`provider` on delegate_task (`delegation.allow_task_model`); Kanban cards with no pinned model run on `kanban.default_model`, except reviews, retries after a failed run and keep-pattern matches (each run logs a `model_tier` event) |
| [sidebar-exclude-sources.md](sidebar-exclude-sources.md) | 0154 | Per-profile `sessions.sidebar_exclude_sources` (e.g. `[api_server]`) keeps a machine profile's sessions out of the sidebar project tree |
| [gemini-provider-defaults.md](gemini-provider-defaults.md) | 0156 0258 | `--provider gemini` without `-m` uses a Gemini model instead of sending your `claude-*` default (404); Gemini aux/title default is `gemini-3.8-flash` |
| [sidebar-created-sort.md](sidebar-created-sort.md) | 0158 0581 0588 | Sidebar 'created' sort orders, clusters and date-buckets by `started_at`, so a reply in an old chat no longer lifts it to the top; projects keep their place too (no active-project hoist, no activity re-sort); lane and preview rows rank by their own `started_at`, so rows outside the loaded page stop moving |
| [session-context-nested-rebind.md](session-context-nested-rebind.md) | 0159 | A nested re-bind of the turn's own session restores the binding on clear, so cards created after a queued model switch keep their session id |
| [credential-pool-norefresh-adopt.md](credential-pool-norefresh-adopt.md) | 0160 | A live credential pool adopts tokens pushed to disk for no-refresh OAuth rows instead of keeping the expired copy |
| [kanban-escalated-triage-red.md](kanban-escalated-triage-red.md) | 0161 0234 | A block-loop-escalated triage card counts as waiting on you, so its chat paints red; 0234: non-human/NULL triage counts as unfinished work, so its chat is not green |
| [kanban-worktree-retention.md](kanban-worktree-retention.md) | 0303 | Age-based terminal-worktree cleanup with verified recovery refs; startup orphan tombstones |
| [kanban-worker-worktree-cleanup.md](kanban-worker-worktree-cleanup.md) | 0162 | Worker guidance: `git worktree remove` every worktree you add before `kanban_complete` |
| [browser-background-tab.md](browser-background-tab.md) | 0163 0164 | `browser_exec`'s own tab and the CDP supervisor's fallback page open with `background=True`, so a new browser session stops stealing keyboard focus on macOS |
| [pre-final-gate.md](pre-final-gate.md) | 0165 0167, pending (fork PR #401) | `pre_final_gate` plugin hook: hold a final answer silently (never shown or stored) or append to it; `needs_buffer` keeps a held answer from streaming; PR #401 skips callbacks on fork turns (background review, `/btw`) unless `include_forks` |
| [sidebar-automated-sessions.md](sidebar-automated-sessions.md) | 0166 0190 0191 0192 | Bot / smoke-test sessions (per-profile `sessions.automated_sources`, `"*"` = whole profile, or an allowlist `sessions.interactive_sources` that marks every other source; root `sessions.sidebar_profiles` makes every unlisted profile automated by default, clones included) are hidden from the Desktop sidebar by default; filter-menu "Show automated & test sessions" brings them back; pinned and blocked / needs-attention rows always stay; ⌘]/⌘[ skips the same rows |
| [kanban-park-invariants.md](kanban-park-invariants.md) | 0169 0335 | Optional profile-scoped GitHub broker argv for PR acceptance; cards never park without showing who they wait on: respawn-guard holds are capped then blocked, a closed-unmerged PR binding can be replaced, every `scheduled` park has a wake (human asks stay blocked), block-loop escalations count as waiting on you |
| [top-level-env-scrub.md](top-level-env-scrub.md) | 0169 0171 0172 | `serve`/`dashboard`/`desktop`/`gui`/`gateway`, the post-update Desktop reopen and Electron main drop the inherited kanban worker / delegate-child / session env (whole agent-scope family, one shared list), so chats are not fenced as delegate children; a process that still carries a leaked marker gets Kanban refusals naming the leak and the dead worker card |
| [create-titled-session-source.md](create-titled-session-source.md) | 0180 | `chat -c <title> --create-if-missing` labels the created session with `--source` (or an inherited automation source) instead of `cli`, so integration threads stay out of the sidebar |
| [fork-ci-hardening.md](fork-ci-hardening.md) | 0173 0176 0177 0179 0181 0182 0183 0188 0189 0193 0196, 0197, 0198, 0199, 0200, 0201, 0202, 0203, 0204, 0205, 0206, 0207 0209 0218 0224 0226 0230 0249 0252 0480 0482 0494 0495 0599 0604 0625 | Fork CI runs its Linux jobs on self-hosted runners; PM-locked Node for python tests; load/container-tolerant test fixes; 0196, 0197, 0198, 0199, 0200, 0201, 0202, 0203, 0204, 0205, 0206, 0207/0209: hub-runner timeouts and worker sizing, rootless-docker lane, 3-day artifact retention, per-push concurrency on main, nix `workflow_dispatch` |
| [portability-and-provider-fixes.md](portability-and-provider-fixes.md) | 0174 0175 0178 0187 0233 | Contributor email map, commandcode keeps `COMMANDCODE_API_KEY`, utf-8 subprocess decoding and psutil pid check |
| [sidebar-background-process-badge.md](sidebar-background-process-badge.md) | 0184 | Sidebar row lists the background processes holding it off green, with a Stop button |
| [kanban-unloaded-session-delivery.md](kanban-unloaded-session-delivery.md) | 0185 0186 0610 | Card results are written into chats no backend holds (sweeper); `kanban_unseen` paints the row green; a chat mid-turn defers the notice quietly (one overdue WARNING after 15 min), and HX014 flags contextmanagers that swallow the caller's error at a yield |
| [kanban-delegate-marker-owner.md](kanban-delegate-marker-owner.md) | 0208 | The delegate-child env marker counts only while its owner process is alive and an ancestor of the reader; a process outside the lineage drops the leaked agent-scope env once instead of being fenced |
| [plugin-dir-conflicts.md](plugin-dir-conflicts.md) | 0210 | Plugin discovery skips backup-named dirs (`*.bak*`, `*.orig`, `*.old`) and resolves same-name manifests deterministically (basename match wins); `plugins list` and `doctor` show ignored/shadowed copies |
| [kanban-worker-session-id.md](kanban-worker-session-id.md) | 0211 | Task detail API gives each run its `worker_session_id`, so clients open the worker's transcript, not the creating chat |
| [kanban-failure-notices.md](kanban-failure-notices.md) | 0216 0217 | Kanban `gave_up` / `crashed` / `timed_out` notices (Desktop and messaging gateway) name the real cause (model refusal, provider configuration, no result reported, launch failure, time limit, process exit), promise a retry only when the event records one, and make no unsupported fallback claims |
| [file-safety-token-broker.md](file-safety-token-broker.md) | 0222 | Read- and write-deny `~/.config/token-broker` (broker client token) in file tools, search, vision and `@file` refs |
| [hub-sync-replica.md](hub-sync-replica.md) | 0219 0220 0232 0235 0329 0369 | Desktop reads the hermes-hub replica (hub-sync plugin): owner lookup without probing, Kanban falls back to the replica for an unreachable gateway, and cached sessions of unreachable gateways appear as offline-marked rows in the normal sidebar list (standalone section removed; live rows win) |
| [chat-closeout.md](chat-closeout.md) | 0253 0254 0255 | Chat close-out seams: session rows carry `kanban_waiting_on`, grey `waiting` sidebar stripe while cards are open, plugin waiting sources, and `_wake_session_turn` to run one turn in an unloaded chat |
| [kanban-respawn-guard-hold.md](kanban-respawn-guard-hold.md) | 0212 0225 0227 0241 0248 0250 0251 0312 0459 0339 0466 0790 | A held card gets one comment + fix card; `active_pr` lifts for merged/closed PRs or completed queue rejections. 0790 keeps comments from resetting the hold-cap clock and distinguishes deliberate ready holds from stranded cards. |
| [desktop-kanban-worker-readonly.md](desktop-kanban-worker-readonly.md) | 0213 0229 | Desktop opens a Kanban worker session read-only and quietly instead of a "Resume failed" toast (gateway code 4132) |
| [api-server-thread-hops.md](api-server-thread-hops.md) | 0214 0228 | API-server SSE flushes worker-thread hops before `assistant.completed`/EOS so the last commentary is not dropped |
| [codex-subscription-routing.md](codex-subscription-routing.md) | 0236 0237 0238 0239 0240 | Account-gated Codex models route via an entitled ChatGPT/Codex subscription: union catalog, plan-gated pool selection, subscription-first picker |
| [kanban-quota-recovery.md](kanban-quota-recovery.md) | 0259, 0304 | Recovered quota walls use cooldowns; healthy heartbeats/checkpoints clear stale failure errors; stale guard-hold blocks are released |
| [runtime-safety-followups.md](runtime-safety-followups.md) | 0272 0280 0283, 0284, 0285 | Portable wake cleanup, disabled-MCP diagnostics, fail-closed delegated lineage and Git-guard PATH isolation |
| [session-start-requests.md](session-start-requests.md) | 0287 | Durable plugin-authorized session start identity, at-most-once first-turn dispatch and owner-local reconciliation |

## Applying

For the current **4059-patch snapshot**, start at `831637fe8d58f050e9aa776b621bb2c7b614dcd1`. Apply forty-seven bridges and three ancestry-only markers from `mirror-replay-4059.json`. The verifier first proves each ancestry-only patch against its source-parent tree and proves that its named source merge retains exactly its first-parent tree. Do not discard those patches without that check, start at the new merge-base, or apply one entire glob. Example (Bash, with this repository's absolute path in `MODS`, and run from a fork clone holding both source commits):

```bash
set -e
SOURCE=$(pwd)
python3 -B "$MODS/core/verify_mirror.py" --source-repo "$SOURCE" \
  --source-tip 3c0d856312756637a976519ed8315cffa4356c33 \
  --base b51a30c461ee663313ce89d01947bb10b55a619c \
  --patch-dir "$MODS/core/patches" --policy "$MODS/core/mirror-policy-4059.json" \
  --replay-plan "$MODS/core/mirror-replay-4059.json"
git worktree add --detach ../hermes-mods-test 831637fe8d58f050e9aa776b621bb2c7b614dcd1
cd ../hermes-mods-test
patches=("$MODS"/core/patches/*.patch)
git am -3 --empty=keep "${patches[@]:0:1327}"
git apply --index --3way "$MODS/core/bridges/error-card-v2.diff"
git commit -m 'Explicit upstream error-card-v2 merge bridge'
git am -3 --empty=keep "${patches[@]:1327:102}"
git apply --index --3way "$MODS/core/bridges/upstream-04422ac2f04.diff"
git commit -m 'Explicit upstream merge bridge before 1430'
git am -3 --empty=keep "${patches[@]:1429:2}"
git apply --index --3way "$MODS/core/bridges/upstream-fef6b4fb42f.diff"
git commit -m 'Explicit upstream merge bridge before 1432'
git am -3 --empty=keep "${patches[@]:1431:46}"
git apply --index --3way "$MODS/core/bridges/upstream-through-7dc18193.diff"
git commit -m 'Explicit upstream merge bridge before 1478'
git am -3 --empty=keep "${patches[@]:1477:45}"
git apply --index --3way "$MODS/core/bridges/upstream-bbb69887a1c.diff"
git commit -m 'Explicit upstream merge bridge before 1523'
git am -3 --empty=keep "${patches[@]:1522:26}"
git apply --index --3way "$MODS/core/bridges/upstream-6e2fbc15549.diff"
git commit -m 'Explicit upstream merge bridge before 1549'
git am -3 --empty=keep "${patches[@]:1548:77}"
git commit --allow-empty -m 'Ancestry-only source merge 0013a0897e0 retains patch 1626'
git apply --index --3way "$MODS/core/mirror-upstream-1702.diff"
git commit -m 'Explicit upstream merge bridge before 1627'
git am -3 --empty=keep "${patches[@]:1626:22}"
git commit --allow-empty -m 'Ancestry-only source merge ccbe7c69d0b retains patch 1649'
git am -3 --empty=keep "${patches[@]:1649:6}"
git commit --allow-empty -m 'Ancestry-only source merge ccbe7c69d0b retains patch 1656'
git am -3 --empty=keep "${patches[@]:1656:165}"
git apply --index --3way "$MODS/core/bridges/upstream-717447e-via-02df0dd38e8.diff"
git commit --allow-empty -m 'Explicit upstream merge bridge before 1822'
git am -3 --empty=keep "${patches[@]:1821:1}"
git apply --index --3way "$MODS/core/bridges/upstream-fbf6c6c-via-95e40c5b81b.diff"
git commit --allow-empty -m 'Explicit upstream merge bridge before 1823'
git am -3 --empty=keep "${patches[@]:1822:1}"
git apply --index --3way "$MODS/core/bridges/context-1824-prompt-turn-branch-side.diff"
git commit --allow-empty -m 'Source-context bridge before 1824'
git am -3 --empty=keep "${patches[@]:1823:4}"
git apply --index --3way "$MODS/core/bridges/resolution-1bf6faea509.diff"
git commit --allow-empty -m 'Merge-resolution bridge before 1828'
git am -3 --empty=keep "${patches[@]:1827:38}"
git apply --index --3way "$MODS/core/bridges/context-1866-kanban-db-before-cpu-gate.diff"
git commit --allow-empty -m 'Source-context bridge before 1866'
git am -3 --empty=keep "${patches[@]:1865:27}"
git apply --index --3way "$MODS/core/bridges/upstream-through-27576ed88c6.diff"
git commit --allow-empty -m 'Explicit upstream merge bridge before 1893'
git am -3 --empty=keep "${patches[@]:1892:3}"
git apply --index --3way "$MODS/core/bridges/resolution-04bc5b36944.diff"
git commit --allow-empty -m 'Merge-resolution bridge before 1896'
git am -3 --empty=keep "${patches[@]:1895:23}"
git apply --index --3way "$MODS/core/bridges/upstream-958df18a746.diff"
git commit -m 'Explicit upstream merge bridge before 1919'
git am -3 --empty=keep "${patches[@]:1918:3}"
git apply --index --3way "$MODS/core/bridges/upstream-ced18437aa5.diff"
git commit -m 'Explicit upstream merge bridge before 1922'
git am -3 --empty=keep "${patches[@]:1921:53}"
git apply --index --3way "$MODS/core/bridges/upstream-through-c79ab4146a8.diff"
git commit -m 'Explicit upstream integration bridge before 1975'
git am -3 --empty=keep "${patches[@]:1974:8}"
git apply --index --3way "$MODS/core/bridges/upstream-through-41f4b65b573.diff"
git commit -m 'Explicit upstream integration bridge before 1983'
git am -3 --empty=keep "${patches[@]:1982:6}"
git apply --index --3way "$MODS/core/bridges/upstream-through-f95fb98c590.diff"
git commit -m 'Explicit upstream integration bridge before 1989'
git am -3 --empty=keep "${patches[@]:1988:10}"
git apply --index --3way "$MODS/core/bridges/upstream-through-5ff44110acc.diff"
git commit -m 'Explicit upstream integration bridge before 1999'
git am -3 --empty=keep "${patches[@]:1998:2}"
git apply --index --3way "$MODS/core/bridges/upstream-through-857ffb31e9b.diff"
git commit -m 'Explicit upstream integration bridge before 2001'
git am -3 --empty=keep "${patches[@]:2000:10}"
git apply --index --3way "$MODS/core/bridges/upstream-0dfd1965f18.diff"
git commit -m 'Explicit upstream integration bridge before 2011'
git am -3 --empty=keep "${patches[@]:2010:4}"
git apply --index --3way "$MODS/core/bridges/upstream-0f099072b6b.diff"
git commit -m 'Explicit upstream integration bridge before 2015'
git am -3 --empty=keep "${patches[@]:2014:3}"
git apply --index --3way "$MODS/core/bridges/upstream-b45f3ad7cff-via-b447a1f13d2.diff"
git commit -m 'Explicit upstream integration bridge before 2018'
git am -3 --empty=keep "${patches[@]:2017:1}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2019.diff"
git commit -m 'Explicit upstream integration bridge before 2019'
git am -3 --empty=keep "${patches[@]:2018:2}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2021.diff"
git commit -m 'Explicit upstream integration bridge before 2021'
git am -3 --empty=keep "${patches[@]:2020:5}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2026.diff"
git commit -m 'Explicit upstream integration bridge before 2026'
git am -3 --empty=keep "${patches[@]:2025:7}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2033.diff"
git commit -m 'Explicit upstream integration bridge before 2033'
git am -3 --empty=keep "${patches[@]:2032:2}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2035.diff"
git commit -m 'Explicit upstream integration bridge before 2035'
git am -3 --empty=keep "${patches[@]:2034:5}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2040.diff"
git commit -m 'Explicit upstream integration bridge before 2040'
git am -3 --empty=keep "${patches[@]:2039:1}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2041.diff"
git commit -m 'Explicit upstream integration bridge before 2041'
git am -3 --empty=keep "${patches[@]:2040:1}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2042.diff"
git commit -m 'Explicit upstream integration bridge before 2042'
git am -3 --empty=keep "${patches[@]:2041:1}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2043.diff"
git commit -m 'Explicit upstream integration bridge before 2043'
git am -3 --empty=keep "${patches[@]:2042:1}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2044.diff"
git commit -m 'Explicit upstream integration bridge before 2044'
git am -3 --empty=keep "${patches[@]:2043:6}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2050.diff"
git commit -m 'Explicit upstream integration bridge before 2050'
git am -3 --empty=keep "${patches[@]:2049:3}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2053.diff"
git commit -m 'Explicit upstream integration bridge before 2053'
git am -3 --empty=keep "${patches[@]:2052:2}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2055.diff"
git commit -m 'Explicit upstream integration bridge before 2055'
git am -3 --empty=keep "${patches[@]:2054:7}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2062.diff"
git commit -m 'Explicit upstream integration bridge before 2062'
git am -3 --empty=keep "${patches[@]:2061:4}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2066.diff"
git commit -m 'Explicit upstream integration bridge before 2066'
git am -3 --empty=keep "${patches[@]:2065:17}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2083.diff"
git commit -m 'Explicit upstream integration bridge before 2083'
git am -3 --empty=keep "${patches[@]:2082:4}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2087.diff"
git commit -m 'Explicit upstream integration bridge before 2087'
git am -3 --empty=keep "${patches[@]:2086:167}"
git apply --index --3way "$MODS/core/bridges/upstream-before-2254.diff"
git commit -m 'Explicit upstream integration bridge before 2254'
git am -3 --empty=keep "${patches[@]:2253:1}"
git apply --index --3way "$MODS/core/bridges/upstream-22084b164869-via-caa185a23c0.diff"
git commit -m 'Explicit upstream integration bridge before 2255'
git am -3 --empty=keep "${patches[@]:2254:1}"
git apply --index --3way "$MODS/core/bridges/upstream-78abaafcccec-via-9f84f76fc8c.diff"
git commit -m 'Explicit upstream integration bridge before 2256'
git am -3 --empty=keep "${patches[@]:2255:929}"
git apply --index --3way "$MODS/core/bridges/upstream-d01ed2bfac-via-91c2b94744.diff"
git commit -m 'Explicit upstream integration bridge before 3185'
git am -3 --empty=keep "${patches[@]:3184:466}"
git apply --index --3way "$MODS/core/bridges/upstream-55b0b2611f-via-db5a056366.diff"
git commit -m 'Explicit upstream merge bridge before 3651'
git am -3 --empty=keep "${patches[@]:3650:68}"
git apply --index --3way "$MODS/core/bridges/upstream-b51a30c461-via-a38507dd8f.diff"
git commit -m 'Explicit upstream merge bridge before 3719'
git am -3 --empty=keep "${patches[@]:3718:341}"
# Mandatory additive repair: historical mails intentionally remain immutable.
python3 -B "$MODS/core/repair_hostname_escapes_4059.py" --tree "$PWD"
git add hermes_cli/kanban_placement.py
git commit -m 'Repair historical hostname regex word boundaries'
python3 -B "$MODS/core/repair_hostname_escapes_4059.py" --source-repo "$SOURCE" --applied "$(git rev-parse HEAD)"
```

The source object store must contain historical patch-index blobs (see verification prerequisites above). Use Git 2.51 or newer for the replay: Git 2.43's three-way merge conflicts on patch 0032 (`apps/shared/src/gateway-contract.openrpc.json`), which later Git versions merge cleanly. Run the full verifier with its policy and replay plan to check final source equivalence; successful application alone is insufficient. Intermediate trees and standalone feature-patch application are not certified. Remove the throwaway worktree when finished.

### Historical apply reports (not instructions for the current series)

The historical 400-patch candidate was pinned to source
`0146a882edf55e22fc9197e2a2e190a0fbacf068` (source independently reviewed and published).
All 400 apply in order with `git am -3 --empty=keep`; 290 selected Python tests across
15 files pass on the applied public series, with no failures or skips.
Patches 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127, 0128, 0129, 0130, 0131, 0132, 0133, 0134, 0135, 0136, 0137, 0138, 0139, 0140, 0141, 0142, 0143, 0144, 0145, 0146, 0147, 0148, 0149, 0150, 0151, 0152, 0153, 0154, 0155, 0156, 0157, 0158, 0159, 0160, 0161, 0162, 0163, 0164, 0165, 0166, 0167, 0168, 0169, 0170, 0171, 0172, 0173, 0174, 0175, 0176, 0177, 0178, 0179, 0180, 0181, 0182, 0183, 0184, 0185, 0186, 0187, 0188, 0189, 0190, 0191, 0192, 0193, 0194, 0195, 0196, 0197, 0198, 0199, 0200, 0201, 0202, 0203, 0204, 0205, 0206, 0207, 0208, 0209, 0210, 0211, 0212, 0213, 0214, 0215, 0216, 0217, 0218, 0219, 0220, 0221, 0222, 0223, 0224, 0225, 0226, 0227, 0228, 0229, 0230, 0231, 0232, 0233, 0234, 0235, 0236, 0237, 0238, 0239, 0240, 0241, 0242, 0243, 0244, 0245, 0246, 0247, 0248, 0249, 0250, 0251, 0252, 0253, 0254, 0255, 0256, 0257, 0258, 0259, 0260, 0261, 0262, 0263, 0264, 0265, 0266, 0267, 0268, 0269, 0270, 0271, 0272, 0273, 0274, 0275, 0276, 0277, 0278, 0279, 0280, 0281, 0282, 0283, 0284, 0285, 0286, 0287, 0288, 0289, 0290, 0291, 0292, 0293, 0294, 0295, 0296, 0297, 0298, 0299, 0300, 0301, 0302, 0303, 0304, 0305, 0306, 0307, 0308, 0309, 0310, 0311, 0312, 0313, 0314, 0315, 0316, 0317, 0318, 0319, 0320, 0321, 0322, 0323, 0324, 0325, 0326, 0327, 0328, 0329, 0330, 0331, 0332, 0333, 0334, 0335, 0336, 0337, 0338, 0339, 0340, 0341, 0342, 0344, 0345, 0346, 0347, 0348, 0349, 0350, 0351, 0352, 0353, 0354, 0355, 0356, 0357, 0358, 0359, 0360, 0362, 0363, 0367, 0368, 0369, 0370, 0371, 0372, 0373, 0374, 0375, 0376, 0377, 0378, 0379, 0380, 0381, 0382, 0383, 0384, 0385, 0386, 0388, 0389, 0390, 0391, 0393, 0394, 0395, 0396, 0397, 0398, 0399 are byte-identical and unrenumbered. Patch 0343, 0361, 0364, 0365, 0366, 0387, 0392 explicitly integrates
merge `2b6644387f`; overlapping branch commits must not also be replayed.
See [current verification](mirror-400-verification.md)
for privacy, preservation and exact execution scope. The earlier 377-patch run's
Desktop/typecheck results are historical, not rerun for this export. No runtime
deployment or legacy migration is claimed.
Chrome 0375, 0376, 0377 remains experimental; see [managed-chrome-leases.md](managed-chrome-leases.md).
Existing source identities are retained, with renumbered references; do not assume
that repeated changes can be applied twice without an explicit adaptation.
See [ci-retry-isolation-followups.md](ci-retry-isolation-followups.md) for the
historical 275-patch verification and topology renumbering (67 tests passed, two bootstrap
home-guard failures; not a fully green suite).

The earlier 0272, 0273, 0274, 0275, 0276, 0277, 0278, 0279, 0280, 0281, 0282, 0283, 0284, 0285, 0286 mirror verification (historical numbering) applied all 260 patches with `git am -3 --empty=keep` at
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

The 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105 mirror verification (October 16, 2026 PT) applied all 105 patches cleanly
and passed all changed regression files: 197 Python tests across 8 files and 99 Desktop
tests across 7 files. This targeted run did not rerun the full suite or typecheck.

The 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127 mirror verification (October 17, 2026 PT) applied all 127 patches cleanly
and passed every test file those patches add or touch: 195 Python tests across 16 files and
214 Desktop tests across 8 files. Again no full suite or typecheck.

The 0128, 0129 mirror verification (October 18, 2026 PT) applied all 129 patches cleanly and
passed the changed test files: 92 Python tests across 3 files.

The 0130 mirror verification (October 18, 2026 PT) applied all 130 patches cleanly with `git am -3 --empty=keep`
at merge-base `4957bbd0a0`; the patch's new schema test passes (and fails without it).

## Historical mirror verification (before the 368-patch export)

Previously verified full ordered series: 292 patches from merge-base `4957bbd0a0`, fetched upstream `9b904dcc22`, fork `e295d0da73` (last non-merge patch `2c047de606`). All patches apply with `git am -3 --empty=keep`. The applied final public series passes 43 tests across six files: shebang checker, durable CLI takeover, adversarial takeover, full-turn takeover, durable recovery and terminal background-chat guard. Coverage and normal privacy hooks pass. Desktop suites and full Nix builds were not independently rerun by this mirror.

Merged branches inserted earlier commits: existing patch bytes and slugs are preserved while numbers and documentation references move together. Patch 0284 substitutes example contributor identifiers. Patch 0362 shortens a mock API key and parenthesizes a fixture argument to avoid scanner false positives; both affected test files pass in the final applied series. Existing privacy scrubs and the 0251 adaptation remain intact.

| Feature | Patches | Behavior |
|---|---|---|
| [CI and source fixes](mirror-ci-source-fixes.md) | 0256 0284 0341 0351, 0352 0354 | CI race/warmup fixes, public attribution, pinned source mirror and pipefail guards |
| [Foreign tile lifecycle](foreign-tile-lifecycle.md) | 0360 | Keep owner-routed foreign tiles across ambient refresh; retry missing owner sessions |
| [Session takeover](session-takeover.md) | 0362 | Cooperative durable CLI handoff and detached-chat origin guard |

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
- **Fix-up patches 0031, 0032, 0033, 0034.** These close test and lint gaps in earlier patches. Take each one with the
  patch it fixes:

  | Patch | Fixes | Goes with |
  |---|---|---|
  | 0031 | translations for the `sidebar.filter.*` labels | 0024 |
  | 0032 | `session.title.suggest` wire contract + regenerated contract files | 0020 |
  | 0033 | import order lint in `use-desktop-integrations.ts` | 0022 |
  | 0034 | regenerated `locales/_keys.desktop.json` | 0019, 0020, 0021, 0022, 0023, 0024 |
- **Keybinds.** The defaults live in `apps/desktop/src/lib/keybinds/actions.ts` and the handlers in
  `apps/desktop/src/app/hooks/use-keybinds.ts`. `actions.test.ts` rejects chord conflicts.
- **Tags.** I tag my commits `[daveey]` in the subject so they're easy to list
  (`git log upstream/main..origin/main`). Use your own tag.


| Feature | Patch | Description |
|---|---|---|
| [Process and CI followups](process-ci-test-followups.md) | 0327 0332 0333 0337 0355 0379, 0380 | Readiness, duplicate deadline identities, curl detection fallback and bounded fast-check lane. |
| [Session start requests](session-start-requests.md) | 0381, 0382 | Trusted metadata, authenticated SSH principal and persisted-owner preservation. |
| [Session-start toolset allowlist](session-start-toolsets.md) | 0483 0493 0488 | A durable start can pin a closed toolset allowlist (validated, persisted, kept across resume, compression and branches; reloads cannot widen it). Runners repair chats use it to get only the runners tools. |
| [Blocked clarify answered elsewhere](clarify-early-answer.md) | 0814 | A decision recorded in the user-steps For-you queue ends a session's blocked `clarify` within seconds (`answered_elsewhere`) instead of at its timeout |
| [Per-callback hook budgets and a host deadline/abandon signal](hook-callback-budget.md) | 1059 | A plugin hook callback may declare its own bound: `callback.hermes_hook_timeout = N` seconds. The value is clamped to 600, applies to bounded hooks only, and never turns the threaded path on when `plugins.hook_callback_timeout` is 0 or lowe |
| [Stored chat outcome, server-side mute and sidebar stripe](session-outcome.md) | 1005 1006 1007 1008 1009 1010 1011 1015 1077 1079 | `sessions.outcome_json` / `outcome_at` hold one outcome per chat, and `sessions.muted` is a lineage-wide mute flag. `hermes_state_outcome` derives it by contract precedence 1-8: muted, live turn, open decisions, live work, a held declaratio |
| [Kanban stale checks count awake time only](kanban-suspend-ledger.md) | 1003 1014 1017 | Each dispatcher pass stamps `kanban.db`; a gap over `max(300, 3 x kanban.dispatch_interval_seconds)` is recorded as a suspend (pruned after 7 days). `awake_seconds()` subtracts recorded suspends. |
| [Kanban sync: pickup publish, partition repair, lease renewal, claim fence](kanban-sync-fixes.md) | 0967 0996 1004 1048 1064 | 967: the sync client posts `kanban.sync.pickup` (null when unset) once per round and only when it changed, because the Board gives a machine pin `runs_on` only when `/v2/machines` advertises pickup. |
| [Remote notices: stale and superseded cards stop replaying](kanban-remote-notice-sweep.md) | 0969 0976 1063 | The sweep ignores superseded legacy replica rows, bounds terminal time with the freshness window and checks the delivered ledger (0969). |
| [create_overlap ignores URL slugs; ci-flaky-cards owner rules](kanban-overlap-url-slug-and-flaky-cards.md) | 1012 1024 1033 1039 1046 1050 | 1024: a plain-word slug is referenced only by `board <slug>`, `--board <slug>`, `<slug> board`, `@task:<slug>@gw/...` or `<gateway>/<slug>` as a whole token; `/<slug>` in a URL, path or slash list no longer counts. Naming a plain-word board |
| [Capped turns parked for a core update keep continuation eligibility](core-update-capped-pause.md) | 0962 | The live writer restamps `parked_at` while parking (and once as parking ends); the in-place continue, the startup sweep and the notice hold measure `core_update_pause` freshness through `turn_marker.freshness_since`. |
| [Worker-terminal write refusal names the worker fence](kanban-worker-fence-refusal.md) | 0963 | The env-inherited refusal now names the worker-terminal fence, the owning agent pid and card, and the authorized paths (kanban_* tools, or gateway/dispatcher board-repair code). In-process delegate children keep their text; leaked markers k |
| [A block on a human must say what unblock and archive mean](kanban-user-ask-lint.md) | 0986 | `kanban_block` and `kanban_create` (tool entry points) refuse a `human_request` whose `action` does not state what unblocking and what archiving do (rejection `user_ask_incomplete`). Internal trackers (`kb.create_task`) are not linted. The  |
| [Class routes: gateway rows, API reuse and resume ordering](class-route-resume.md) | 0971 0974 1053 | 971: when the lease adopts the gateway's bare session row it seeds the agent's init `model_config` (`SessionDB.seed_session_model_config` fills only missing keys atomically), so gateway rows under `class:deep` resume as a class, not a pin.  |
| [Cold kanban admission helper reuses recorded discovery](cold-admission-helper.md) | 0977 | Each is done once and reused by later cold processes with identical results: the `OPTIONAL_ENV_VARS` additions run lazily on first use under a re-entrant lock; `plugins_snapshot` records plugin toolset keys and portable MCP names; skill fro |
| [Desktop wake burst and board snapshot cost](backend-burst-perf.md) | 0995 1055 1066 1071 1075 | `/api/status` caches the `_config_version` parse on the file signature (errors still re-read, warn and raise); `stale_ok` reaches `_ledger_owners` so the session strip no longer waits on the links lock; `GET /board` refreshes its snapshot a |
| [doctor dashboard import probe launches like the runtime](doctor-dashboard-import-probe.md) | 0985 | The probe uses `runtime_command(code=...)`, the same launch as the update import probe. |
| [CI lanes and test deflakes, 0961-1050](ci-test-stability-0961-1050.md) | 0961 0964 0965 0966 0968 0970 0973 0975 0978 0979 0980 0997 0998 0999 1000 1001 1002 1013 1016 | 965 (source fix): the deterministic compression fallback waits out its local work instead of inheriting the LLM idle window. |
| [Pull requests run only the Python tests their change can reach](ci-affected-tests.md) | 1019 1020 1021 | Pull requests run only the tests a change can reach (import closure + smoke set); full suite on main, dispatch, release, risky paths or any planner failure. Reads are BOM-tolerant, git calls are bounded, slice count is pinned to the matrix. |
| [CI lanes and test deflakes, 1060-1080](ci-test-stability-1060-1080.md) | 1060 1061 1065 1070 1073 1074 1080 | Deflake fixes (test-only) plus one CI-card wording change from the 1060-1080 range. |
| [Placement placeholders never route chats or become a child card's origin](kanban-placement-placeholders.md) | 1067 1068 1076 | A card the MacBook places on the hub carries the creator placeholder session `kanban-placement-<gateway>`, which is attribution only, not a chat. These patches keep it out of every chat-routing path. |
| [Scope-heartbeat freshness follows the owner's published collector period](kanban-scope-heartbeat-window.md) | 1069 | One rule decides how long an owner's `task_scopes[].observed_at` is trusted. |
| [Auxiliary class routing rotates OAuth accounts before excluding a route](auxiliary-oauth-account-rotation.md) | 1111 | An account 429/402 rotates within the fenced OAuth pool (once per account per call, never to a paid key, no replay after delivery) before the route is excluded. Mirror only: live deployment is tracked on the routing recovery card. |
| [CI lanes, cache budget and test deflakes, 1081-1111](ci-test-stability-1081-1111.md) | 1081 1083 1085 1091 1093 1094 1095 1096 1097 1098 1101 | Deflake fixes (test-only) plus CI changes: completion gates split from comment pollers, stable PM cache key, bounded cache saves with a timeout smoke. |
| [Kanban sync keeps dependency and chat-link edges; placement no longer pins Linux work local](kanban-sync-links-and-placement.md) | 1082 1084 1110 | Foreign copies keep Board dependency edges; chat-link deletes are captured; a product name alone no longer pins Linux-only work to the MacBook. |
| [Kanban board prewarm and per-scope attention caches](kanban-board-read-perf.md) | 1086 1109 | Board prewarm at boot and per-scope attention-count caches cut cold and polled reads from seconds to tenths of a second. |
| [Kanban capability, PR hold, origin-ledger and relay-ask fixes](kanban-worker-policy-fixes.md) | 1087 1088 1090 1102 | Queue-only assignees skip the capability check; PR holds scope to comment writers; terminal accepts release obligations; agent-relay human asks are refused. |
| [Plugin module eviction walks a snapshot of sys.modules](plugin-module-eviction-snapshot.md) | 1089 | Plugin loading survives concurrent imports. |
| [Gateway exit deadline, launchd signal forwarding and cron update admission](gateway-exit-deadline-and-signals.md) | 1092 1106 1107 | One exit deadline tied to the supervisor stop budget, launchd wrapper forwards stop signals, scoped cron runs drop the update reservation. |
| [Session-state and Desktop wake fixes: passive notices, live-turn rows, change-watcher throttle, power settle](desktop-tui-session-state-fixes.md) | 1100 1103 1104 1105 1108 | No duplicate passive notice on a woken turn, background reads held across sleep/wake, rows of externally running turns show working, throttled change-watcher digest. |
| [Gateway exit causes, start-race refusals and fallback-dir cleanup](gateway-start-ownership-and-exit-cause.md) | 1158 1168 1174 1175 1192 1198 1200 1202 | Every failed gateway start names its cause in the exit log, losing the profile-reservation race to a live gateway is a recorded refusal, stale `hermes-gw-<hash>` dirs are removed, a one-off launchd gateway job runs under the signed Hermes Python.app, and several gateway tests are made deterministic. |
| [Kanban worker requeue grants and crash records](kanban-worker-requeue-and-crash-records.md) | 1162 1163 1164 1165 1166 1177 | Shared worker sidecar validation, scoped supervisor requeue grants, deferred class routing, and failed-run-only crash records with flushed replies. |
| [Kanban goal-loop exits, sweeps and remote notice acks](kanban-goal-loop-sweeps-and-remote-notices.md) | 1176 1183 1184 1188 1189 1190 1197 | Stops goal-mode workers that park themselves or can't reach their owner being booked as crashes, re-checks standing human asks and stale triage on each dispatch, and acks remote notices only once their transcript row exists. |
| [Boot revision, plugin fingerprints and backend idle work](code-skew-plugin-fingerprint-and-backend-perf.md) | 1160 1161 1180 1181 1185 1186 1191 | Record the boot revision, stabilize plugin discovery fingerprints, and reduce cold-start and idle backend work. |
| [CI apt/Nix-sandbox, code_health merge judging, termux APT keys and test deflakes](ci-and-test-deflakes-1159-1201.md) | 1159 1167 1169 1170 1171 1172 1173 1178 1179 1182 1187 1196 1199 1201 | JS CI refreshes apt indexes and fails closed, the Nix flake check requires a real sandbox, code_health judges merges against each parent, termux signing imports protected keys attended, plus test-only deflakes and reproductions. |
| [Gateway restart drain holds, cron gate and sandbox job re-attach](gateway-restart-drain-holds-and-reattach.md) | 1228 1211 1213 1231 | A restart's drain no longer kills or charges the Kanban runs it waits on, agent cron jobs are held rather than failed while the gateway restarts, a claimed admission runs after a drain flip, and a restart re-attaches sandbox background jobs or tells the session its watcher is lost. |
| [Kanban wake probes, remote notice re-offers and ask re-checks](kanban-wake-probes-remote-notices-and-ask-recheck.md) | 1205 1207 1212 1232 | A woken remote notice whose turn ended without its row is re-offered, standing-ask re-checks read named cards from title/action/why_human, a park's wake_check keeps one live probe across worker nodes, and the origin ledger strips both sides of its evidence comparison. |
| [CI fast lane, test slices and the upstream-sync gate](ci-fast-lane-test-slices-and-upstream-sync-gate.md) | 1203 1204 1222 1206 1221 | An upstream sync reaches main only after a full suite, Python tests run as 8 slices on one-VM-per-job runners, light PR checks and aggregates run on the fast lane, and the sync resolves gh under a service PATH. |
| [Desktop remote-primary windows never cold-start a local serve](desktop-remote-primary-no-local-serve.md) | 1208 1209 1210 | A remote-primary window no longer cold-starts a local `hermes serve` for hub replica and retained-Kanban reads; follow-ups keep gateway.ts within its size gate and the connection id a string. |
| [Dashboard host-attach stale-while-revalidate and update idle probe](dashboard-host-attach-swr-and-update-idle-probe.md) | 1215 1216 1218 1219 1220 | The host-gateway memo is served stale-while-revalidate, control verbs get their own executor, pid liveness uses psutil, and an unanswered idle probe while waiting on a review is not the review ending. |
| [Test deflakes and the sidecar chmod race](test-deflakes-and-sidecar-chmod-race.md) | 1214 1217 1223 1224 1225 1226 1227 1229 1230 1233 1235 | Deflakes for browser, local-model, desktop send-then-close and preference-store, gateway compression and local-recovery tests, plus the state-db sidecar unlink-between-lstat-and-chmod fix with its test. |
| [Desktop ConfirmDialog unmount guard](desktop-confirm-dialog-unmount-guard.md) | 1234 | `ConfirmDialog` arms no 600 ms close timer when it unmounts while `onConfirm` is still pending, so no timer fires after teardown. |
| [Kanban restart, reaper and false-signal fixes](kanban-restart-reaper-and-false-signal-fixes.md) | 1254 1265 1266 1269 1272 | Reject foreign fingerprints safely, drain claims during deferred restart, declare worker restart safety and avoid phantom-reference reports on partial synced boards. |
| [Cron planned-pause one-shot slots](cron-planned-pause-oneshot-slots.md) | 1255 | Preserve specific one-shot occurrences across planned admission holds and gateway drains without exempting unrelated stale schedules. |
| [Test-only deflakes and regression coverage, 1254–1277](test-deflakes-batch-1254-1277.md) | 1256 1257 1260 1261 1263 1267 1268 1271 1275 1276 1277 | Test-only readiness, cancellation, evidence, process-reaping, database-handle, admission, cron-slot and teardown-order follow-ups. |
| [Kanban park and owner-archive fixes](kanban-park-and-owner-archive-fixes.md) | 1258 1259 1264 1274 | Refuse already-met parks, wake on producer completion and allow scoped capability-owner cleanup locally or through a home-relay request. |
| [Sandbox background watcher idle-reap fixes](sandbox-bg-watcher-idle-reap.md) | 1262 | Keep active sandbox environments through idle cleanup and reattach a failed poller handle before declaring its process lost. |
| [Affected tests include PM toolchain changes](ci-affected-tests-pm-toolchain.md) | 1270 | Select the full Python suite for shared PM interpreter and activation inputs while leaving unrelated PM data narrow. |
| [Kanban supervisor requests](kanban-supervisor-requests.md) | 1278 1279 1280 1281 | A worker files a durable, deduplicated `kanban_supervisor_request` to unlink edges between cards its task created; the dispatcher tick executes it with a lease and receipts, link ops are refused up front, and a worker's shell `unlink` is guarded. |
| [1281-entry mirror snapshot](mirror-1281-verification.md) | verification report | Replay and tree-comparison evidence for the 28 entries appended after the 1253 snapshot. |
| [Attach e2e probe uses installed Node](attach-probe-node.md) | 1282 | The attach-handshake e2e helper passes the suite's Node as `HERMES_NODE`, and a new test fails if attach ever reaches PM provisioning. |
| [1299-entry mirror snapshot](mirror-1299-verification.md) | verification report | Duplicate 1282 replaced by the omitted `3c2af6ee2e`; `verify_mirror.py` exits 0 end to end with policy [mirror-policy-1299.json](mirror-policy-1299.json). |
| [1301-entry mirror snapshot](mirror-1326-verification.md) | verification report | Appends 1300 (`642d2c6878`) and 1301 (`7fbf27b309`); `verify_mirror.py` exits 0 with policy [mirror-policy-1326.json](mirror-policy-1326.json). |
| [Kanban scratch archive](kanban-scratch-archive.md) | 1299 | `gc_task_scratch` archives (bundle, diff, untracked tar, meta) then removes aged terminal scratch holding git clones; retention `kanban.worktree_retention_days` (default 7, 0 disables). |
| [Bounded launcher preflight](launch-preflight-bound.md) | 1284 1285 1286 1287 1300 | The launch-time PM currency probe has a 1 s deadline; unknown defers repair and `hermes update` keeps the full check. 1300 keeps a permission error's denied path across the probe and PM workers. |
| [TUI/e2e CI timing stability](tui-ci-timing-stability.md) | 1283 1297 1298 | A single durable-flush seam for the TUI, no-op in tests, plus synchronized receipt tests and a glyph-layout tolerance. |
| [Core-update Kanban-only stop budget](gateway-core-update-kanban-stop-budget.md) | 1288 1294 | A spent Kanban update grace gets one 40 s stop leash so the whole restart fits 60 s. |
| [Kanban owner-archive relay split](kanban-owner-archive-relay-split.md) | 1289 | Pure move of the kanban_archive worker flow to keep the code-health ratchet green. |
| [Home I/O guard: SQLite URIs](home-io-guard-sqlite-uri.md) | 1290 1291 | The test home-I/O guard checks SQLite URI filenames, including encoded Windows drives. |
| [Serve update-gate log](serve-update-gate-log.md) | 1292 | Gate waits and re-execs are appended to `logs/serve-update-gate.log`. |
| [Upstream sync lands via PR](upstream-sync-land-via-pr.md) | 1293 1295 1296 | `land()` merges a PR pinned to the head once the required check is green; GitHub refusals are reported verbatim. |
| [Kubernetes terminal backend: one pod per session](kubernetes-terminal-backend.md) | pending (fork PR #242) | `terminal.backend: kubernetes`: a hardened pod per session in a profile-configured namespace, exec over the pod API, existing-claim or emptyDir workspace, no PVC rights needed. |

## Mirror follow-ups 1327–1368

| Feature | Patches | Scope |
|---|---|---|
| [Per-profile background-job recovery](process-profile-recovery.md) | 1327 | Each profile recovers only its own background jobs; finished jobs leave every checkpoint. Source `b57f02fcdf8`. |
| [Desktop renderer resilience](desktop-renderer-resilience.md) | 1328 1336 1338 1345 1346 1361 | Recovery action label kept across the error-card merge, PowerSettlingError rejections swallowed, ticket bootstrap cause named with one retry, storage-spy test seams, teardown-safe notification timers. |
| [SIGUSR2 stack dump](gateway-sigusr2-stack-dump.md) | 1329 1367 | GIL-safe Python SIGUSR2 handler replaces `faulthandler.register`; 1367 hardens the stress test. |
| [Managed-worker launch terminal policy](managed-worker-launch-terminal-policy.md) | 1330 1331 1332 | Secondary-profile workers no longer inherit the launch profile's derived terminal settings; `home_mode` rebuilt from the target profile. |
| [Desktop reload resume snapshot](desktop-reload-resume-snapshot.md) | 1333 | A reloaded window resumes a streaming segment from a snapshot instead of a replay it may miss. |
| [Gateway GIL interval and trim](gateway-gil-and-trim.md) | 1334 1335 | 1 ms GIL switch interval for the API loop; mem trim skips full `gc.collect()` where no page release follows. |
| [Sidebar session-state fixes](sidebar-session-state-fixes.md) | 1337 1347 1359 | Cold sessions with history never read as drafts, project-tree rows carry stored outcome and mute, session-control owners resolve from the project tree. |
| [Webapp SSH-served Desktop owner](webapp-ssh-desktop-owner-identity.md) | 1339 | Another Mac's SSH-served Desktop maps to the host owner so its archive of a hub chat stops 403ing. |
| [Kanban owner_restart_lost](kanban-owner-restart-lost.md) | 1340 1341 1342 | A run lost to its owner's unclean death is booked uncharged; systemic crash fingerprints open one fix card. |
| [Kanban restart resume](kanban-restart-resume.md) | 1589 1590 1591 | A run requeued after `owner_restart_lost` resumes the lost run's conversation in its fresh session, or falls back to the transcript tail with a typed reason. |
| [Kanban placement cancellation](kanban-placement-cancellation.md) | 1348 1349 1350 1362 1363 | Completion or archive durably cancels the placed copy across crash gaps and failed sends. |
| [API chat completions served model](api-chat-served-model.md) | 1351 1352 | Non-streaming replies name the served provider and model. |
| [Routing: managed pin and refused route](routing-managed-pin-and-refused-route.md) | 1353 1360 | A managed pin on its own endpoint is not an override; a policy-refused route cannot block switching away. |
| [Nightly e2e](nightly-e2e.md) | 1354 1355 1356 1357 | Dispatch-only nightly e2e plus hub report script that files a Kanban card on red or on persistent did-not-run. |
| [CI/test stability](ci-test-stability-1343.md) | 1343 1344 1358 | PTY frame consumption, runtime-only driver import check, awaited authorization dispatch. |
| [pre_verify skips fork turns](pre-verify-every-turn.md) | 1364 | `pre_verify` hooks are not called for detached fork turns (extends the live-turn verification doc). |
| [Kanban worker task marker in prompt](kanban-worker-task-marker.md) | 1365 | Worker protocol prompt names `$HERMES_KANBAN_WORKER_TASK` for shell use. |
| [managed-worker.log block header](managed-worker-log-header.md) | 1366 | Each worker block starts with `[pid N] @ <UTC> core=<sha>` so crash storms date against deploys. |
| [Classic CLI quit on gateway proxy](cli-quit-gateway-proxy-restore.md) | 1368 | Quit no longer runs the prompt restore on the gateway proxy; store reads keep parity. |
| [1368-entry mirror snapshot](mirror-1368-verification.md) | verification report | Appends 1327–1368; `verify_mirror.py` exits 0 with policy [mirror-policy-1368.json](mirror-policy-1368.json). |
| [1375-entry mirror snapshot](mirror-1375-verification.md) | verification report | Appends 1369–1375; full inventory, replay and tree acceptance with the snapshot-bound policy and replay plan. |
| [1381-entry mirror snapshot](mirror-1381-verification.md) | verification report | Appends 1376–1381; full replay/tree acceptance and 60 passing targeted source tests. |
| [1415-entry mirror snapshot](mirror-1415-verification.md) | verification report | Appends 1382–1415; full replay/tree acceptance with two re-pinned historical fingerprints. |
| [1427-entry mirror snapshot](mirror-1427-verification.md) | verification report | Appends 1416–1427 through offline DNS retry wait; full inventory, replay and normalized-tree verification. |
