# Mirror 1979: feature changes in patches 1933–1979

This documents the patch bodies through frozen source tip
`af81aae2d90e2df708f9b4e725e740bcdc4d385e`. Ranges below identify contributing
patches, including follow-up and test-only changes, not standalone apply sets.
Paths are relative to the Hermes **source** tree unless prefixed `core/`.

**Verification status:** source and applied trees each passed 2185 Python tests
(47 files, 12 platform skips), 62 Desktop tests (9 files), and 9 TUI tests (1 file).
Full inventory/replay/tree verification and the actual README recipe passed;
see the [verification report](mirror-1979-verification.md). Behavior descriptions
explain the implementation and its regression fixtures, not proof that every
integration path has been exercised. Live GUI and native Windows remain untested.

## Behavior and feature map

### Scoped storage-corruption confirmation

**Patches: 1934–1936, 1946–1947.**

Before allowing one replay after a structural SQLite error, the writer or pooled
reader performs a bounded, fresh-process, read-only `quick_check`. Only a clean
check permits replay. Confirmed damage, timeout, missing budget, check errors or
recurrence retain the fail-closed process latch.

The attempt's authorizer records table names, not SQL or values. Its composing
connection preserves the caller's authorizer verdict, including denials. An
unfamiliar connection is left untouched and uses whole-file confirmation.
Unknown/unresolvable scope widens to the whole file. Scoped checks include
`sqlite_schema`, `sqlite_sequence` when present, and relevant virtual-table
shadow B-trees. The child loads only the tokenizer extension already loaded by
the parent, when needed.

A write must fail at BEGIN or in its callback, have a confirmed rollback, and
release its page cache before replay. COMMIT/post-COMMIT failures are not
replayed because settlement is uncertain. FTS-specific fail-open handling still
precedes structural handling. Readers release the failing pooled connection's
cache without closing it. Already-latched storage does not repeatedly spawn
confirmation children. Logs retain SQLite code, operation, stage/check outcome
and unconfirmed-error counts; read-side warning sentences have regression
assertions.

**Gotchas:** a clean scoped snapshot does not prove the file was never damaged,
check freelist/cross-tree page ownership, or certify concurrent writes. Write
confirmation uses remaining patience capped at 30 seconds; reads use 5 seconds.
Closing a same-process SQLite connection can affect sibling POSIX locks, hence
the separate confirmation process and retained reader connection. This is not
repair and does not clear an existing latch.

### Case-insensitive write safety

**Patch: 1938.**

Write-denied exact paths, denied directory prefixes and approval-controlled paths
use folded path components. Alternate case cannot bypass these predicates or
avoid their approval classification on case-insensitive filesystems.
Component-wise prefix matching keeps similarly named siblings distinct;
explicitly allowed control files remain allowed.

**Gotchas:** this changes shared file-safety predicates, not every possible
filesystem write mechanism. Live tool approval dialogs and shell-mediated writes
are untested here.

### CLI queue display and explicit no-tools selection

**Patches: 1939, 1953.**

The CLI tracks its own `(session_id, submission_id)` from submit through turn
completion and excludes only those `input_id` rows from its published queue dock.
Other queued prompts remain visible, concurrent submissions are tracked
independently, and refusal/exception cleanup removes the identity.

An explicit empty toolset selection remains `[]` through legacy one-shot parsing,
current launch policy, Python TUI environment export and TypeScript canonical
`session.create` construction. Only `None` or an absent environment key means
use the default. The CLI configuration label distinguishes `all`, `none` and
named toolsets.

**Gotchas:** do not replace an explicit empty selection with a truthiness
fallback. Queue filtering is viewer-local presentation, not deletion of the
owner's admission. The six classic CLI live-session harness cases passed;
production interactive use and the live TypeScript TUI remain untested here.

### Queue diagnostics and bounded reservation probes

**Patches: 1941, 1949–1951, 1973.**

Queue failures record allowlisted stages (`pull`, `reserve`, `repin`) and
transport classes (`budget_exhausted`, `timeout`, `disconnected`, `reset`,
`refused`, `other`). Classification uses exception types rather than copying
messages, URLs or arbitrary attributes. `BoardBudgetExhausted` distinguishes an
exhausted shared deadline from an ordinary transport timeout.

Failed reserve/repin calls additionally record `budget_ms` left at call start
and `elapsed_ms` until the exception. Values are finite, nonnegative whole
milliseconds capped at one day. Unusable inputs are omitted, and diagnostic
arithmetic never replaces the original failure. Stage, transport and timing do
not change semantic observation deduplication.

Repeated queued reserve probes are suppressed for 60 seconds while both the
Board revision and local notice identity (requirements/run count) are unchanged.
The cache is bounded to 1,024 entries. Changed revisions/requirements, explicit
placement moves or expiry allow another probe; assigned reservation renewal is
unchanged. Throttled passes still process local repin notices from the stored
observation once per notice revision.

**Gotchas:** registry-only capacity changes arrive at the next keepalive, not
every sweep. The queued reply's own task revision is used rather than an
unrelated post-pull revision. Timing describes the failing reserve/repin call,
not the entire tick; it is not a new retry policy. Live remote Board behavior is
untested here.

### Live sanitized sandbox skills copies

**Patches: 1942–1945.**

Every skills root mounts a stable sanitized copy under
`<HERMES_HOME>/cache/sandbox-skills/`, even if initially clean. Copies survive
process exit/container reuse and are refreshed in place before Docker commands
at most once per five seconds. Refreshes carry allowed edits/deletions while
excluding symlinks, the `.hub` cache, read-denied entries and recursive copying
of the copy store. Thread/process locking serializes refreshes. A refresh
exception is logged without failing the command; capsule environments do not
acquire this refresh path.

POSIX copying resolves the root once, opens each source component relative to
an anchored parent descriptor with no-follow semantics, verifies directory
identity, and reads only regular files from descriptors. A swapped FIFO does
not block the open. Capability detection recognizes builtin names even when
`os` functions are wrapped. The stable copy key uses the same single resolution
as the walker.

Windows uses pinned Win32 handles without delete sharing for the ancestor chain
and files. Reparse points are refused; type, identity and final path are checked
before reading. Binary writes preserve bytes and read-only destinations can be
replaced. Files use temporary siblings and atomic replacement. Platforms with
neither safe mechanism withhold the copy rather than following paths unsafely.

**Gotchas:** this protects managed skills copies, not arbitrary Docker volumes.
A symlinked root is resolved before the guarded walk; links encountered within
the tree are not copied. Emulated handle coverage is not native Windows proof.
Native Windows, a real Docker daemon and production reused containers are
untested here.

### Cron observer recovery across owner restart

**Patch: 1952.**

External canonical cron observers reconnect after eligible transport loss and
recover the same journaled fire identity instead of submitting uncertain work
again. Recovered admissions must be queued, started or terminal; unknown/missing
receipts and owner refusals fail closed. Reconnection has a 120-second window
plus one bounded connection/RPC attempt, with two-second delays. Only successful
status observation resets the outage budget, not merely connecting or recovering
a receipt. Healthy long jobs retain their ordinary inactivity policy.

Restart safety reflects the actual executor: an isolated local script worker
can be restart-safe, while an isolated observer of an agent job executing inside
the gateway remains gateway-dependent and retains drain waits/update reservations.

**Gotchas:** this is observation recovery, not permission to duplicate a fire or
bypass owner refusals. Production scheduler restart/update paths are untested.

### Remote profile use and cooldown accounting

**Patches: 1954–1957, 1976.**

Remote warm-set recency and keep-alive holds are stamped through one validated
use path. Foreground session open/new chat, routed prompt submission, chosen
profile activation, and explicitly user-action terminal dials count. Reconnect
sweeps, background roster/session reads, media loads, passive dials and forced
probes do not count merely because they connect.

Renderer reports are throttled to once per minute per connection/profile and
tolerate unavailable/older IPC handlers. Main accepts only known SSH registry
connections and names accepted by the shared desktop profile validator. A bare
route inherits the window's remote primary connection only for the primary
profile, not unrelated legacy-profile routes. Existing recent-profile prewarm
selection and the 30-minute hold thus reflect use rather than plumbing.

Use wiring is factored into remote-use/warm-set helpers. Dial cooldown accounting
moves into the dial-failures module: background callers use the reconnect
ladder's no-jitter delay for the failure streak; authentication rejection stays
in its own ledger. A bare socket open does not erase an accept-then-close streak.

**Gotchas:** this does not expand the warm-set cap or make all dials user actions.
The shared dial-profile validator replaces the separate stricter regex. Tests
include source-wiring assertions and mocked renderer/main behavior; real Electron
IPC, SSH lifecycle and GUI gestures are untested here.

### Supervision for orphaned manual-repin waits

**Patches: 1959–1961.**

A worker-first no-pool-worker wait is owned by a live recipient/subscriber or an
open creator card. Unknown remote/profile/unreadable state is not evidence of
death. Local recipient lookup follows session resolution across profiles and
uses a guarded read-only SQLite connection.

When ownership is absent, the origin primary creates one idempotent
`repin-owner:<target>` supervisor on the same board, assigned to the configured
orchestrator profile (default otherwise) and pinned to the origin. Recovery runs
on every sweep independently of notice deduplication, in its own transaction;
failure preserves the notice and can retry. Closed supervisors are reported as
closed, never silently replaced or described as live.

The generated supervisor receives only a narrow `kanban_move` tool grant: its
exact listed, still-unstarted, unclaimed ready worker-first target may be repinned
to the origin while the no-pool-worker wait remains current. Generated identity,
board, target, lifecycle and placement conditions are checked. Notice/supervisor
creation itself never repins. Stuck summaries distinguish owned/orphaned waits
and name supervisor status.

**Gotchas:** unknown ownership conservatively retains the wait. This is not
general cross-card move authority or shell permission. Patch 1960 advances the
fixture through the reserve keepalive before asserting that a changed queue
observation invalidates the grant. Live supervisor dispatch is untested here.

### Subprocess diagnostic secret redaction

**Patches: 1962–1963.**

Subprocess exception command bodies remain argv-masked when embedded script text
resembles another diagnostic tail or command head. Masking begins at the earliest
command head of either quote style and continues across disjoint bounded windows.
An unheaded tail in ordinary prose does not initiate command masking.

**Gotchas:** the parser deliberately favors conservative masking once inside a
command diagnostic. This changes diagnostic redaction, not command execution or
general secret discovery. Production outbox delivery is untested here.

### One-continuation release of an active-PR hold

**Patches: 1966–1969.**

An authenticated exact-target owner-wake tool grant can release an `active_pr`
hold on an unclaimed READY card for one continuation. The latest owner run must
have an orderly completed dependency park, a matching authenticated checkpoint
and a claim. Original parents must remain and all dependencies must be satisfied.
Independent post-park review evidence must explicitly name every captured PR and
an exact reviewed head.

Network-capable admission occurs before the write transaction. Inside it, local
authority, target eligibility, supervisor preconditions, evidence and event-tip
fences are rechecked. PR publications must remain in scope and strictly precede
release. The `hold_released` receipt records park identity (portable run UID
preferred), PR scope, review proofs, supervisor and `merge_authorized: false`.

Only the active-PR guard recognizes this receipt. A later run consumes it even
if that run crashes. It cannot be reused for the same park or release a newer/
out-of-scope publication. Generic handoff and recent-success guards remain.
Follow-up patches add malformed-evidence, transport/remapped-ID, admission-race,
publication-time and provenance-boundary cases.

**Gotchas:** this is neither merge approval nor generic unblock. Same-second
publication/release is intentionally insufficient. Foreign replicas cannot mint
the local-authority release; nonportable run IDs are not cross-store proof.
Production Board dispatch and actual PR review/merge operation are untested.

### Profile-scoped Kanban annotation caching

**Patches: 1970–1971.**

Count snapshots are retained in a 32-scope LRU, keeping counts, waiting/parked/
stalled state, local/remote identity keys, fingerprint, timestamps and generation
together under the cache lock. Switching profiles restores a coherent snapshot
but still validates fingerprint, TTL and maximum age; it does not rejuvenate it.
Generation checks are scope-specific so concurrent profiles cannot exchange
counts and identities. Explicitly dropping the active cache clears retained
scopes instead of resurrecting an invalidated snapshot.

**Gotchas:** caching is not permission to return expired or board-changed data.
Patch 1971 adds no new test file; the changed annotation suite is listed below,
not claimed as a newly authored test for every follow-up branch. Live sidebar
polling/performance is untested here.

### Deferred shared client-active metrics

**Patch: 1972.**

The client-active interval latch uses deferred writes when the shared SQLite
metrics store remains busy beyond its short timeout. Deferred work stays in
memory for the next write or exit drain. Timestamp comparison occurs inside the
eventual transaction, so another process's committed latch wins instead of a
stale attempt time producing a duplicate count. A backward clock correction
updates a future latch without counting another interval. A non-busy latch
failure does not discard earlier deferred writes.

**Gotchas:** deferral returns false rather than claiming a committed metric.
In-memory deferral is not crash-durable telemetry. Production upload is untested.

### Worker persistence receipt binding and gap recovery

**Patch: 1974.**

The RPC reader accepts one matching response frame (result/error, not a method
notification) and clears its awaiting identity before queueing it. Duplicate/late
frames cannot become the next call's result. The owner stamps successful
`worker.persist` responses after persistence returns with execution/sequence
identity. The worker checks and strips this receipt; mismatches trigger at most
three attempts before `receipt_mismatch`.

The journal retains up to four acknowledged entries with a 256 KiB serialized
size budget. Structured sequence refusals include `expected_sequence`; a worker
whose journal is ahead can resend a retained replayable gap under unchanged
owner sequence/digest fences. Missing history or identity-changing operations
(`compression.publish`, `child.reserve`, `execution.finish`) fail `lost_receipt`
rather than renumbering or replaying under a different scope. Scope changes
discard acknowledged history.

**Gotchas:** rolling compatibility accepts old-owner replies with no receipt
stamp; binding is therefore not enforced against an old owner. Production
transport disconnects and mixed-version deployment are untested here.

### Unambiguous desktop log timestamps

**Patch: 1977.**

Desktop log lines carry local date/time and a numeric UTC offset derived from
the same `Date`, distinguishing DST folds and preserving half-hour offsets.
`hermes logs desktop --since` accepts offset-bearing and legacy stamps, including
T-separated timestamps. Known offsets are converted to the reader machine's
local wall time for cutoff comparison; legacy naive stamps stay naive. The
gateway menu timestamp stripper accepts both shapes.

**Gotchas:** legacy lines do not identify the writer's timezone and the parser
does not invent one. The gateway-menu regex change has no dedicated new test in
this patch; rendered GUI stripping and live log emission are untested here.

### Settling restart-unknown Desktop queue cards

**Patch: 1978.**

For paused `unknown` queue cards with no live turn, session-list refreshes can
passively read the latest 50 transcript messages, including compacted messages,
on any route. A matching submission ID or normalized text in a sufficiently
recent user message must be followed by a nonempty assistant reply in that same
turn. A later ordinary user row ends the window; `_queued_prompt` rows remain
part of the current turn. Matching allows ten minutes of clock skew and merged
user text.

With reply evidence, the card and pending-submission journal entry are removed,
not retired. A later owner snapshot may restore a card still reported unknown.
Failed reads, no match or a persisted user row without a reply leave it paused.
Work is coalesced by row stamp/in-flight key and skipped for live turns. The red
sidebar stripe names this queue cause and gives “Send again or Discard” guidance,
with localized strings.

**Gotchas:** this is passive REST transcript reconciliation, never
`session.resume` or automatic resend. The bounded message window/text fallback
is not an exactly-once delivery proof. Unchanged row stamps are not repeatedly
fetched, including after a failed read. Real GUI/remote REST operation is
untested here; component/store tests use controlled fixtures.

### Fixture compatibility, isolation and synchronization

**Patches: 1933, 1937, 1940, 1948, 1958, 1964–1965, 1975, 1979.**

These change test harnesses, not production behavior:

- **1933:** gateway fixtures replace `Principal` with upstream `SessionActor`.
- **1937:** preview fixtures implement guest activity leases and cleanup;
  background-interrupt mocks expose request-connection/profile lookups.
- **1940:** lifecycle tests stop before host process discovery; browser tests
  enumerate only owned subprocesses or synthetic fixtures, retaining relevant
  liveness/argv checks.
- **1948:** classic CLI steps require fresh output and fail before further input
  if it is missing. Modal readiness uses the question, not spinner quiescence;
  paste spacing still exceeds the detection window without long padding.
- **1958:** reconnect tests wait for receiver delivery to catch the published
  watermark, including delayed fanout, before establishing an empty-replay premise.
- **1964:** scoped-lock fixtures supply all synthetic process identity probes
  and check that a live lock remains unchanged.
- **1965:** teardown skips notification-park cleanup during module initialization;
  regressions distinguish initializing and initialized states.
- **1975:** profile-build tests isolate unrelated services and join the actual
  build thread before session removal/monkeypatch teardown, retaining profile-home/
  MCP and secret-scope assertions.
- **1979:** killed-shallow-fetch recovery waits for both the server transfer marker
  and the client's `shallow.lock` before killing the fetch. Live-lock preservation
  remains an asserted premise.

**Gotchas:** these are not new runtime lock-recovery, reaper, profile-build or
replay policies. The tmux harness ran in isolated test homes, not production sessions.

## Code

Principal implementation paths by feature (test-only paths are in Tests):

| Feature | Source paths |
| --- | --- |
| Storage confirmation | `hermes_state.py`, `hermes_state_health.py`, `hermes_state_corruption_confirm.py` |
| Write safety | `agent/file_safety.py` |
| CLI queue/toolsets | `hermes_cli/gateway_agent.py`, `hermes_cli/gateway_queue.py`, `cli.py`, `hermes_cli/main_tui_launch.py`, `hermes_cli/oneshot.py`, `ui-tui/src/canonicalGateway.ts` |
| Queue provenance/throttle | `hermes_cli/kanban_queue_reason.py`, `hermes_cli/kanban_sync_client.py`, `hermes_cli/kanban_worker_placement.py`, `hermes_cli/kanban_repin_notice.py` |
| Skills copies | `tools/credential_files.py`, `tools/credential_files_windows.py`, `tools/environments/docker.py`, `website/docs/user-guide/configuration.md` |
| Cron recovery | `cron/observer_reconnect.py`, `cron/scheduler.py`, `cron/scheduler_authority.py` |
| Remote use | `apps/desktop/electron/main.ts`, `apps/desktop/electron/preload.ts`, `apps/desktop/electron/remote-warm-set.ts`, `apps/desktop/src/global.d.ts`, `apps/desktop/src/store/gateway.ts`, `apps/desktop/src/store/profile.ts`, `apps/desktop/src/store/remote-use.ts`, `apps/desktop/src/store/gateway-dial-failures.ts` |
| Repin ownership | `hermes_cli/kanban_creator_repin.py`, `hermes_cli/kanban_db_dispatch_preclaim.py`, `hermes_cli/kanban_db_dispatch_stuck.py`, `hermes_cli/kanban_repin_notice.py`, `hermes_cli/kanban_repin_owner.py` |
| Redaction | `agent/redact.py` |
| Scoped hold release | `hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_hold_release.py`, `tools/kanban_tools_self_heal.py` |
| Annotation cache | `hermes_cli/kanban_counts_scopes.py`, `hermes_cli/kanban_session_activity.py`, `hermes_cli/kanban_session_activity_revalidate.py` |
| Metrics | `hermes_cli/observability/shared_metrics.py` |
| Worker receipts | `agent/runtime_session_store.py`, `gateway/session_controls.py`, `gateway/session_worker.py`, `hermes_state_runtime.py` |
| Log offsets | `apps/desktop/electron/desktop-log-line.ts`, `apps/desktop/src/app/shell/gateway-menu-panel.tsx`, `hermes_cli/logs.py` |
| Unknown queues | `apps/desktop/src/store/owner-queue-landed.ts`, `apps/desktop/src/store/owner-queue-reconcile.ts`, `apps/desktop/src/store/pending-submissions.ts`, `apps/desktop/src/app/chat/sidebar/session-row-stripe.tsx`, desktop i18n modules and `locales/_keys.desktop.json` |

## Tests

Exact changed test files are grouped below. These are **not execution results**.
Python tests belong in the replayed/source Hermes environment, not the mods
repository root. Desktop and TUI tests need their respective JavaScript test
runners/dependencies. Source-wiring assertions, mocked dispatch, native OS tests,
and live production integration are distinct levels of evidence.

### Storage confirmation — 1934–1936, 1946–1947

One-shot/persistent errors, real B-tree damage, rollback/COMMIT safety, FTS paths,
scoped confirmation, authorizer composition, tokenizer forwarding, reader cache
release and warning contracts:

- `tests/hermes_state/test_storage_corruption_confirmation.py`
- `tests/hermes_state/test_storage_corruption_scoped_confirm.py`

### Write safety — 1938

Folded denials/approval predicates and allowed/prefix siblings:

- `tests/agent/test_file_safety_write_case.py`

### CLI queue/toolsets — 1939, 1953

Own-submit filtering/cleanup and absent-versus-empty launch policy across Python
and TypeScript entry points:

- `tests/hermes_cli/test_cli_queue_dock_own_submit.py`
- `tests/hermes_cli/test_empty_toolset_launches.py`
- `ui-tui/src/__tests__/canonicalGateway.test.ts`

### Queue diagnostics/throttle — 1941, 1949–1951, 1973

Allowlisted/redacted provenance, deduplication, revision/expiry/explicit-move
behavior, notice retention, bounded cache and timing extremes; the timing suite
also contains a local HTTP reserve fixture after a slow pull:

- `tests/hermes_cli/test_kanban_queue_error_provenance.py`
- `tests/hermes_cli/test_kanban_queued_reserve_throttle.py`
- `tests/hermes_cli/test_kanban_repin_notice.py`
- `tests/hermes_cli/test_kanban_queue_error_timing.py`

### Skills copies — 1942–1945

Stable copies/refresh/reuse, exclusions, capsule/no-refresh behavior, failure
handling, source-swap races, wrapped OS functions and pinned-handle behavior:

- `tests/tools/test_credential_files.py`
- `tests/tools/test_docker_environment.py`
- `tests/tools/test_docker_skills_mount_live.py`
- `tests/tools/test_skills_copy_race.py`
- `tests/tools/test_skills_copy_windows.py`

Supporting fixture: `tests/tools/pinned_handles_emulation.py` (not a standalone
test suite). The word “live” in a filename is not a claim of a real daemon run.

### Cron recovery — 1952

Same-fire recovery, refusal/unknown handling, bounded reconnection, terminal
receipt bookkeeping and actual-executor restart topology:

- `tests/cron/test_canonical_cron.py`
- `tests/cron/test_canonical_receipt_bookkeeping.py`
- `tests/cron/test_cron_observer_reconnect_bounds.py`
- `tests/cron/test_cron_observer_restart.py`
- `tests/cron/test_cron_owner_restart_pending.py`
- `tests/cron/test_cron_owner_restart_receipt.py`
- `tests/cron/test_cron_restart_topology.py`

### Remote use — 1954–1957, 1976

Main use qualification, renderer reporting/throttling, route attribution,
shared profile validation and wiring assertions:

- `apps/desktop/electron/remote-warm-dispatch.test.ts`
- `apps/desktop/electron/remote-warm-use.test.ts`
- `apps/desktop/src/store/gateway-remote-use.test.ts`

The cooldown extraction is a refactor; these changed files do not imply a newly
added exhaustive cooldown suite or live Electron/SSH coverage.

### Repin supervision — 1959–1961

Owner-state conservatism, supervisor idempotency, transaction failures, exact
move grants/refusals and changed queue observations after keepalive:

- `tests/hermes_cli/test_kanban_repin_notice.py`
- `tests/hermes_cli/test_kanban_repin_owner.py`

### Redaction — 1962–1963

Shell-string/list diagnostics, quote variations, embedded heads/tails, bounded
continuations and ordinary unheaded text:

- `tests/agent/test_redact_outbox_secrets.py`

### Scoped hold release — 1966–1969

Authenticated evidence, portable receipts, single-use consumption, malformed
parks/receipts, admission races, publication/time/provenance boundaries:

- `tests/tools/test_kanban_owner_wake_hold_release.py`
- `tests/tools/test_kanban_hold_release_transport.py`
- `tests/tools/test_kanban_hold_release_safety.py`
- `tests/tools/test_kanban_hold_release_boundaries.py`
- `tests/tools/test_kanban_hold_release_races.py`
- `tests/tools/test_kanban_hold_release_mutation_boundaries.py`

### Annotation cache — 1970–1971

Alternating scopes, original age limits, bounded LRU and concurrent snapshot
identity coherence:

- `tests/hermes_cli/test_kanban_annotation_unchanged_inputs.py`

### Metrics — 1972

Busy-store deferral, non-busy failure isolation and cross-process contention:

- `tests/hermes_cli/test_relay_shared_metrics.py`

### Worker receipts — 1974

Mismatched/duplicate replies, retained/missing gaps, owner stamping, structured
expected sequence and child-store compatibility:

- `tests/agent/test_worker_receipt_binding.py`
- `tests/hermes_state/test_runtime_worker_children.py`

### Desktop logs — 1977

Offsets, DST folds/gaps, midnight and sampled zones; CLI legacy/aware parsing,
continuations and `--since` filtering:

- `apps/desktop/electron/desktop-log-line.test.ts`
- `tests/hermes_cli/test_logs.py`

### Unknown queue cards — 1978

Matching reply/turn boundaries, merged text, submission IDs, passive reconcile,
live-turn suppression and queue-specific stripe explanations:

- `apps/desktop/src/store/owner-queue-landed.test.ts`
- `apps/desktop/src/store/owner-queue-reconcile.test.ts`
- `apps/desktop/src/app/chat/sidebar/session-row-stripe.test.tsx`

### Fixture maintenance — 1933, 1937, 1940, 1948, 1958, 1964–1965, 1975, 1979

- **1933:** `tests/gateway/test_frozen_decision_reset.py`,
  `tests/gateway/test_kanban_class_tier_route.py`,
  `tests/gateway/test_session_kanban_capsule_guard.py`,
  `tests/gateway/test_session_kanban_restart_resume.py`
- **1937:** `apps/desktop/src/app/chat/preview-open-readiness.test.tsx`,
  `apps/desktop/src/app/session/hooks/use-prompt-actions/index.background-interrupt.test.tsx`
- **1940:** `tests/hermes_cli/test_gateway_restart_loop.py`,
  `tests/tools/test_browser_launcher_reclaim.py`,
  `tests/tools/test_browser_orphan_reaper.py`
- **1948:** `tests/e2e/core/cli_tmux/test_classic_cli_live_session.py`;
  supporting harness `tests/e2e/core/cli_tmux/_classic.py`
- **1958:** `tests/gateway/test_authority_reconnect_gap.py`
- **1964:** `tests/gateway/test_status.py`
- **1965:** `tests/test_tui_server_reset_fixture_partial_import.py`;
  shared fixture change in `tests/conftest.py`
- **1975:** `tests/tui_gateway/test_tui_gateway_server.py`
- **1979:** `tests/hermes_cli/test_gitlock.py`

## Applying

These patches extend the curated mirror through 1932. They are not independent
plugin packages or a safe cherry-pick menu. In particular, storage confirmation,
queue notices/throttling/supervision, remote-use wiring, and shared test fixtures
have ordering dependencies.

Use the snapshot-bound replay plan `core/mirror-replay-1979.json` and policy
`core/mirror-policy-1979.json` with `core/verify_mirror.py`. The plan carries
historical bridges/ancestry handling; applying one glob to current upstream is
not equivalent. In a disposable environment with the source objects available,
the verification invocation from the mods repository is:

```sh
python3 -B core/verify_mirror.py \
  --source-repo <source-clone> \
  --source-tip af81aae2d90e2df708f9b4e725e740bcdc4d385e \
  --base 71736c9f32597bbca9d89cede5aa136e55946b2f \
  --patch-dir core/patches \
  --policy core/mirror-policy-1979.json \
  --replay-plan core/mirror-replay-1979.json
```

The [verification report](mirror-1979-verification.md) records this invocation,
inventory, replay, final-tree comparison and the separate source/applied tests.
A clean apply alone does not establish test success, live
GUI coverage or deployed behavior. Do not regenerate published patches to make
a locally incomplete object store pass.

## Gotchas and verification boundaries

- The behaviors above describe the final cumulative patches, not superseded
  intermediate implementations (notably pre-confirmation storage replay,
  Windows copies being withheld, or the original remote-use profile regex).
- No live GUI/production path is claimed tested. Native Windows, Docker reuse,
  real SSH/IPC, actual cron restart/update, production Board dispatch, worker
  disconnects and production telemetry need separate evidence.
- A changed regression file is not proof of execution, nor automatic coverage
  for every named tool/REST/shell entry point. Do not widen predicate or mocked
  dispatch coverage into a live integration claim.
- Test-only synchronization patches retain asserted premises rather than relaxing
  production safety. Their longer bounds/joins do not change runtime timeouts.

## Plugin boundary

These are **core changes**, not a new installable plugin. SQLite transaction
ownership, worker journals/receipts, canonical cron admission, CLI/TUI launch
policy, Docker mount preparation and Electron routing need their native core
entry points. Plugins can consume exposed status/diagnostics or supply UI, but
cannot safely substitute post-hoc hooks for commit/receipt fences, authorizer
composition, owner authority or no-follow source reads. No plugin installation,
enablement or configuration migration is introduced by this feature document.
