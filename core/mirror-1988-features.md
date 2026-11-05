# Mirror 1988: feature changes in patches 1980–1988

This documents the nine patch bodies after the 1979 snapshot, through frozen
source tip `4e97608f339eeac5d4efb57d1f39ca7fdd0ef5ef`. Each patch's diff
(ignoring `index` lines) is byte-identical to its source commit's diff. Paths
are relative to the Hermes **source** tree unless prefixed `core/`.

| Ordinal | Source SHA | Kind | Feature |
| --- | --- | --- | --- |
| 1980 | `e9e07e29f5ea8f22ad05cc9d3cb4029542311916` | test-only | [Local recovery probes use the RPC hang bound](#local-recovery-probes-use-the-rpc-hang-bound) |
| 1981 | `1979621449923f1c0278a4d48dbdbecf5bee2101` | test-only | [Frozen clock in the notepad byte-stability test](#frozen-clock-in-the-notepad-byte-stability-test) |
| 1982 | `19c289158287593b69b978494261208237285aa5` | code + tests | [Dispatcher health reads flock ownership from fdinfo](#dispatcher-health-reads-flock-ownership-from-fdinfo) |
| 1983 | `5417a9eae1dcfb72c64590f4995092b768c4456b` | test-only | [Contended recovery tick in the dispatcher lifecycle probe](#contended-recovery-tick-in-the-dispatcher-lifecycle-probe) |
| 1984 | `ba6a058cff179cd9e8d970ec0253e2cf09fb34e5` | code + tests | [Kanban `resource` waits for governor/pool gates](#kanban-resource-waits-for-governorpool-gates) |
| 1985 | `2ae1c978d138e8a0580a7f3b5c0c44ff1e07fed9` | code + tests | [Exact named-profile multiplexer homes](#exact-named-profile-multiplexer-homes) (own doc: [exact-profile-multiplexer-homes.md](exact-profile-multiplexer-homes.md)) |
| 1986 | `9561d2b6b6ea96ca833a7280818371068106b034` | code + tests | [Cloudflare Access writes checked with Fetch Metadata](#cloudflare-access-writes-checked-with-fetch-metadata) |
| 1987 | `a9347a785aa5d2ed44b863a242f8bb86d7d88949` | code + tests | [Mid-turn owner storage refusals requeue uncounted](#mid-turn-owner-storage-refusals-requeue-uncounted) |
| 1988 | `4e97608f339eeac5d4efb57d1f39ca7fdd0ef5ef` | test-only | [Committed remount wait in the TUI unmount-compensation test](#committed-remount-wait-in-the-tui-unmount-compensation-test) |

**Verification status.** This document describes patch contents. It does not
report a test run. Inventory, replay, tree comparison and test execution for
this snapshot are reported separately in the 1988 mirror verification
(`core/mirror-1988-verification.md`). Only 1985 has implementation-owner
results, quoted (not reproduced) in its own doc. Live GUI, production Board
dispatch, real Cloudflare Access and native Windows paths are untested here.

---

## Local recovery probes use the RPC hang bound

**Patch 1980** (`e9e07e29f5ea8f22ad05cc9d3cb4029542311916`). Test-only.

**Behavior.** The websocket opened by the shared gateway-daemon probe fixture
keeps the client's ping frames but no longer closes the connection when a pong
misses the websockets library's default 20-second deadline. `rpc()` and its
`RPC_DEADLINE` are now the only hang bound. A CPU-starved daemon that answers
late is no longer reported as a dropped connection. An RPC that never gets an
answer still raises `TimeoutError`. A caller that passes `ping_timeout=`
explicitly (a heartbeat test) keeps that value.

**How it works.** `websocket()` adds `kw.setdefault('ping_timeout', None)`
before `connect(...)`. Nothing else in the fixture changes.

**Code.** `tests/gateway/fixtures/local_recovery_probe.py`. More than a hundred
gateway, hermes_cli, cron and tools tests use this fixture, so they all get the
new default.

**Tests.** New file `tests/gateway/test_local_recovery_transport.py`. It runs a
local websockets echo server and checks that the default gives
`ping_timeout is None` with pings still on. An explicit `ping_timeout=5` is
kept. A silent server with `RPC_DEADLINE=2` still raises `TimeoutError` after
the request arrives.

**Gotchas.** This only changes the test fixture. Production websocket
keepalive is unchanged. Heartbeat regressions in fixture users are no longer
caught unless that test sets `ping_timeout` itself.

**Plugin boundary.** Core test infrastructure; there is no plugin equivalent.

## Frozen clock in the notepad byte-stability test

**Patch 1981** (`1979621449923f1c0278a4d48dbdbecf5bee2101`). Test-only.

**Behavior.** `test_empty_notepad_prompt_byte_stable` compares two cron job
prompts and expects them to be identical. The prompt includes the current
minute, so a build that crossed a minute boundary could fail on CI. The test
now freezes the run time.

**How it works.** The test monkeypatches `cron.scheduler_prompt._hermes_now`
to return a fixed UTC `datetime`, then builds the baseline and the comparison
prompt.

**Code.** No production code changes. The only change is in the test,
`tests/cron/test_notepad.py`.

**Tests.** `tests/cron/test_notepad.py::TestPromptInjection::test_empty_notepad_prompt_byte_stable`.

**Gotchas.** The patch targets the name `_hermes_now` that
`cron/scheduler_prompt.py` imports. If the module renames or re-imports its
clock by removing this name, `monkeypatch.setattr` fails loudly. Keeping an unused alias could instead make the clock override ineffective.

**Plugin boundary.** None (test-only).

## Dispatcher health reads flock ownership from fdinfo

**Patch 1982** (`19c289158287593b69b978494261208237285aa5`).

**Behavior.** Dispatcher-health readers (`available`) and the publisher
(`DispatcherHealth.publish`) both require that the dispatcher process itself
holds an exclusive advisory flock on `.dispatcher.lock`. Ownership is now
checked through that process's own file descriptors instead of the host-wide
`/proc/locks` table. A multi-read snapshot of `/proc/locks` can shift while
unrelated locks change, and it could miss a lock that was still held. That
made healthy dispatcher receipts look unavailable. Only an exclusive `FLOCK`
counts: shared flocks, POSIX `lockf` locks, an unlocked file, another PID, a
child process that inherited the descriptor, and a replaced inode at the same
path all fail. If a descriptor's `fdinfo` cannot be read, the check fails
closed.

**How it works.** `_owns_lock(path, pid)` lists `/proc/<pid>/fd`. It keeps
only descriptors whose `stat()` matches the lock file's `(st_dev, st_ino)`. It
then parses `/proc/<pid>/fdinfo/<fd>` for a
`lock: … FLOCK ADVISORY WRITE <pid> <maj:min:ino>` line. The PID in that line
must equal `pid` and the identity must equal the lock file's. If a descriptor
closes while it is being listed, that descriptor is skipped. An `OSError` from
listing `/proc/<pid>/fd` itself reaches the existing `except OSError` in both
callers, which return unavailable or skip publishing.

**Code.** `gateway/kanban_dispatcher_health.py` (`_owns_lock`). The existing
assertion in `tests/gateway/test_kanban_dispatcher_health.py` now prints a
diagnostic message.

**Tests.** New file `tests/gateway/test_kanban_health_lock_observation.py`
(Linux only, `pytest.mark.platforms("linux")`). It covers:

- exclusive, shared, POSIX and unlocked modes, with `/proc/locks` patched to
  read empty;
- the parent PID is never the owner;
- a child process with the inherited descriptor is not the owner, while its
  parent is;
- `FileNotFoundError` and `PermissionError` on the descriptor's `fdinfo` both
  mean unavailable;
- a replaced inode is not the owned lock.

Also updated: `tests/gateway/test_kanban_dispatcher_health.py`.

**Gotchas.** A reader must be allowed to read the dispatcher's
`/proc/<pid>/fd`. Usually that means the same user without `hidepid`. If it
can't, it reports unavailable even when the dispatcher is healthy. The module
was already Linux-only; other platforms still fail closed.

**Plugin boundary.** Core gateway health evidence. A plugin can read
`available()` results but cannot replace the kernel-ownership check.

## Contended recovery tick in the dispatcher lifecycle probe

**Patch 1983** (`5417a9eae1dcfb72c64590f4995092b768c4456b`). Test-only. Its
source parent is the upstream integration merge `41f4b65b5737…`. That is why
the 1988 replay plan places a bridge before this patch (see
[Applying](#applying)).

**Behavior.** After a worker crash, the real-dispatcher lifecycle probe waits
for an executable recovery tick. A `dispatch_once` that was refused because
another opener held the tick lock (`skipped_locked`) is retried, for up to 60
seconds. A tick that was admitted but recorded the wrong lifecycle outcome is
never retried. A new `crash_contended` mode holds the tick lock on purpose
during the first sweep. It then checks that the sweep was deferred and that
recovery still books the run `crashed` with exactly one failure.

**How it works.** `kanban_owner_probe.py` maps `KANBAN_PROBE_MODE=crash_contended`
to the peer's `crash` mode. It wraps the first sweep in
`hermes_cli.kanban_db_connect._dispatch_tick_lock(...)` when contended, and
records `first_sweep_deferred`. `wait_until(sweep_when_unlocked, ...)` retries
only `skipped_locked` results.

**Code.** No production code changes. Fixture:
`tests/gateway/fixtures/kanban_owner_probe.py`.

**Tests.** `tests/gateway/test_session_kanban.py::test_real_dispatcher_lifecycle`
gains `crash_contended` (`platforms('posix')`). It asserts
`first_sweep_deferred`, `run_outcome == 'crashed'` and `failures == 1`.

**Gotchas.** The 60-second wait applies only to this test. Production tick
locking and dispatch cadence are unchanged.

**Plugin boundary.** None (test-only).

## Kanban `resource` waits for governor/pool gates

**Patch 1984** (`ba6a058cff179cd9e8d970ec0253e2cf09fb34e5`).

**Behavior.** A governor hold is an internal admission gate, for example a
pool-utilization or host-pressure hold. It can now park a card with
`awaiting = {kind: "resource", ref: <gate>, why: <reading and threshold>}`.

Before this patch, such a park was undeclared. Core inferred an external wait
from the host launch command, and the third park escalated into an "Unstick"
owner card. Nobody can act on a transient load reading, and on a pool hold the
escalation spent the quota the hold was waiting for.

A `resource` wait behaves like this:

- **It needs a `wake_check`.** That probe resumes the card once the reading
  drops. Without one the park is refused with `invalid_awaiting`. Both `ref`
  and `why` are required too.
- **The card's own worker cannot declare it.** If `HERMES_KANBAN_TASK` equals
  the task, the park is refused, so a worker can't relabel its own blocker as
  a resource to skip escalation. Agent-action "wall" wording in the text is
  still refused.
- **It never escalates.** Its premise is keyed on `kind|gateway|ref`, not on
  `why`, so "pool at 87%" and "pool at 88%" are the same premise.
- **Repeats back off.** A repeat park on the same gate records `repeat=n` and
  checks the probe less often: `check_every` becomes
  `max(base, min(base·2^min(n,8), 3600))`. The reason gets
  `[resource wait on <ref>: park N in a row …; re-checking every M min. Gate: <why>]`.
- **`wake_at` still wakes the card.** The gate then re-checks admission and
  parks it again if the hold still stands.
- **Genuine external waits still escalate.** An undeclared park, or a declared
  `external` premise, still escalates on its third repeat, including after
  earlier `resource` parks on the same card.

`resource` is now listed as a live producer kind for wait-staleness
reporting.

**How it works.** In `hermes_cli/kanban_awaiting.py`:

- `KINDS` gains `resource`.
- `_check_resource` validates `ref`/`why`, refuses parks by the card's own
  worker, and applies the wall check.
- `resolve` requires a non-empty `wake_check` for `resource`.
- `premise_hash` drops `why` for `resource`.
- `gate` returns early for `resource` with a `repeat` count
  (`prior_same_premise`). It never reaches the clock-window or
  `RepeatedPark` escalation logic.
- New `resource_apply(record, reason, check_every)` computes the backoff and
  the reason note, and stores `backoff_check_every`. Every other park passes
  through unchanged.

`resource_apply` is called in three places, each right after
`kanban_deploy_window.apply`: `continue_task`
(`hermes_cli/kanban_continuation.py`), `schedule_task`
(`hermes_cli/kanban_db_schedule.py`) and the timed `park`
(`hermes_cli/kanban_timed_park.py`).

**Code.** `hermes_cli/kanban_awaiting.py`, `hermes_cli/kanban_continuation.py`,
`hermes_cli/kanban_db_schedule.py`, `hermes_cli/kanban_parser.py` (`--awaiting`
help adds `resource:<gate>`), `hermes_cli/kanban_timed_park.py`,
`hermes_cli/kanban_wait_info.py` (`LIVE_PRODUCER_KINDS`).

**Tests.** `tests/hermes_cli/test_kanban_awaiting.py`. The new cases are:

- `test_gate_denial_parks_as_a_resource_wait_then_auto_resumes`:
  in-process `hermes kanban schedule` with JSON `--awaiting`. The probe fails
  while the hold file exists and then resumes the card to `ready`.
- `test_resource_wait_needs_its_probe`: no `wake_check`, then no `why`, both
  through `resolve`.
- `test_a_worker_cannot_dodge_escalation_by_calling_its_wall_a_resource`.
- `test_governor_resource_parks_never_escalate_and_back_off`: five parks
  give one premise, repeats 0–4, `check_every` 600/1200/2400/3600/3600, the
  reason note text, and no Unstick card.
- `test_repeat_detector_still_fires_for_a_genuine_external_premise_after_resource_parks`.

The test for undeclared parks is renamed to
`test_undeclared_probe_parks_still_escalate_on_the_third_park`.

**Untested paths.** All new resource parks in the tests go through
`hermes kanban schedule` (`schedule_task`) or direct `resolve`. These are
untested for `resource`: the `continue_task` and timed-park/`block` call
sites, the Kanban tool handlers, the `resource:<gate>` CLI shorthand, the
wall-text refusal, and the `wake_at`-driven re-park.

**Gotchas.** Only an operator process such as an admission gate should
declare `resource`. The worker check is based on the environment variable
alone. The backoff counts consecutive parks on one premise. Changing `ref`
starts a new premise. A base cadence above an hour is not shortened.

**Plugin boundary.** The gate that parks a card (for example a governor
plugin or script) is outside core. It declares `resource` through the
existing CLI/tool flags. Premise hashing, escalation exemption and backoff
must stay in core's park path. A plugin hook after the park could not stop
the `RepeatedPark` escalation.

## Exact named-profile multiplexer homes

**Patch 1985** (`2ae1c978d138e8a0580a7f3b5c0c44ff1e07fed9`). Full feature doc:
[exact-profile-multiplexer-homes.md](exact-profile-multiplexer-homes.md).

**Summary.** `multiplexer_root_for(home)` now routes to a multiplexer only
when `named_profile_home(home) == home`, meaning the home is exactly
`<root>/profiles/<name>`. A custom `HERMES_HOME` nested below a profile is
treated as an independent home. An example is a pytest home under
`<root>/profiles/<name>/cache/scratch/…`. Such a home no longer gets a
multiplexer root, implied host or served verdict, and discovery doesn't probe
a multiplexer socket for it. Code: `hermes_cli/gateway_runtime_multiplex.py`.
Tests: `tests/hermes_cli/test_gateway_runtime_multiplex_home.py`. Not a plugin
change.

## Cloudflare Access writes checked with Fetch Metadata

**Patch 1986** (`9561d2b6b6ea96ca833a7280818371068106b034`). This extends
[dashboard-cloudflare-access.md](dashboard-cloudflare-access.md). It restores
the reviewed guard after the upstream `ed1afb…` convergence merge.

**Behavior.** A verified Cloudflare Access login comes from the browser's own
`CF_Authorization` cookie, so it is a cookie-borne credential. A non-safe
request (not GET, HEAD, OPTIONS or TRACE) authenticated this way is now
refused with 403 when the browser's `Sec-Fetch-Site` header says another page
sent it (`same-site` or `cross-site`). The 403 detail is
`Cookie-authenticated writes must come from this dashboard's own page.`, the
same refusal the cookie-session path gives. These still go through:

- reads from any page;
- `same-origin` writes;
- requests without Fetch Metadata (non-browser clients, plain-HTTP origins);
- the Access `/login` bounce.

**How it works.** In `gated_auth_middleware`, after `cf_access_session`
verifies the Access header and before `request.state.session` is set, the
patch adds `if request.method not in _SAFE_METHODS and _browser_says_other_page(request)`.
That check returns the 403 JSON. Both helpers already exist in the module.

The same commit adds a regression test for the extracted gateway
model-context resolver. That test checks that an unknown Bedrock model's
static 128K window is labeled `default`. It touches no production code.

**Code.** `hermes_cli/dashboard_auth/middleware.py`.

**Tests.**

- `tests/hermes_cli/test_dashboard_auth_cf_access.py::test_access_session_write_from_another_page_is_refused`,
  parametrized `same-site` and `cross-site`. A POST to `/api/auth/ws-ticket`
  gets 403 with the exact detail. GET `/api/auth/me` still returns 200, and a
  `same-origin` POST returns a ticket.
- `tests/gateway/test_context_fallback_label_gateway.py::test_extracted_route_resolver_marks_bedrocks_static_default`
  (the related resolver test).

**Gotchas.** The Access path checks only Fetch Metadata. It doesn't apply the
Webapp's own-origin `Origin` allowlist. A browser-triggered request without
`Sec-Fetch-Site` is not judged. Only the ws-ticket POST and the `/me` GET are
exercised. Other write routes are covered by the shared middleware branch,
not by route-specific tests. Real Cloudflare Access is untested here.

**Plugin boundary.** Dashboard authentication runs as core middleware before
any route or plugin handler. A plugin can't enforce this check for core
routes.

## Mid-turn owner storage refusals requeue uncounted

**Patch 1987** (`a9347a785aa5d2ed44b863a242f8bb86d7d88949`). This extends the
host-refusal handling in
[kanban-worker-requeue-and-crash-records.md](kanban-worker-requeue-and-crash-records.md).

**Behavior.** Sometimes the profile owner refuses an admitted worker's
persistence mid-turn with `storage_unavailable`. The worker then exits 1 with
the managed-worker envelope
`Worker failed: WorkerPersistenceError: storage_unavailable`. Before this
patch, those runs counted as crashes against the card's failure budget. Now
the run is booked `owner_unavailable` and the card is requeued without
counting a failure. The run error says `turn interrupted`, and the shared host
notice says `the turn was interrupted` rather than `no turn ran`. The notice
reuses the existing per-host window. The neutral-streak breaker
(`NEUTRAL_STREAK_LIMIT`) still turns a run of such requeues into a counted
crash.

These still count as card failures:

- worker-local persistence errors (`outbox_full`, `unsafe_outbox`,
  `lost_receipt`, `permission_denied`, `stale_generation`, look-alike
  reasons);
- other exception types;
- quoted text or extra trailing output;
- a newer non-matching result.

**How it works.** New `hermes_cli/kanban_owner_storage_refusal.proven(conn,
task_id, run_id, claim_lock)` returns a proof only when all of these hold:

- the run's newest `worker_bound` event has the same `claim_lock` and a PID;
- the newest `worker_result` has the same `claim_lock` and PID, `exit_code == 1`,
  and `last_output` exactly equal to the envelope.

Missing identity on both receipts is not treated as a match. An earlier empty
result for the same run is allowed, but no search goes past the newest
result. The proof is
`{kind: owner_storage_unavailable, phase: midrun, reason: storage_unavailable}`.

In `kanban_owner_unavailable.note`, a `nonzero_exit` with code 1 is checked
against this proof first. A match relabels the dead worker to the
owner-unavailable exit kind and uses this proof in place of the startup
proof, so an admitted run is excused. `kanban_host_refusal.run_error`,
`_cause` and `_notify` use the phase in their wording. The notice body
describes refusals "before or during a turn" and gives a storage-health fix
line.

**Code.** `hermes_cli/kanban_owner_storage_refusal.py` (new),
`hermes_cli/kanban_owner_unavailable.py`, `hermes_cli/kanban_host_refusal.py`.

**Tests.** New file `tests/hermes_cli/test_kanban_owner_storage_refusal.py`
(it reuses helpers from `tests/hermes_cli/test_kanban_host_refusal.py`). It
covers:

- uncounted requeue with the two-receipt shape and no `gave_up`;
- the `no turn ran` wording is absent;
- worker-local errors still count;
- non-terminal or card output still counts;
- the newest result wins;
- each missing or mismatched proof field (12 cases);
- missing identity on both receipts;
- one shared notice without "Again" comments, and the streak breaker on run
  `NEUTRAL_STREAK_LIMIT + 1`.

The tests run through `kanban_db_dispatch.detect_crashed_workers` with
synthetic events. A real worker process is untested here.

**Gotchas.** The match is exact on the envelope string. If the managed
worker's exception formatting changes, the proof silently stops matching and
these runs count as failures again. Only the newest bound result is
considered. The mid-turn proof doesn't prove that no side effects happened,
only that the refusal came from owner storage.

**Plugin boundary.** Crash accounting runs inside the dispatcher's sweep
transaction. A plugin can't reclassify a run before its failure is charged.

## Committed remount wait in the TUI unmount-compensation test

**Patch 1988** (`4e97608f339eeac5d4efb57d1f39ca7fdd0ef5ef`). Test-only.

**Behavior.** The `useVirtualHistory` test "corrects and compensates a
same-layout row measured at unmount" no longer depends on fixed 50 ms
sleeps. It waits until the viewport is measured (height 10, scroll height
40), and after `scrollTo(0)` it waits until row 0 is actually mounted
(`virtualHistory.start === 0`). Only then does it scroll the row out with a
stale height. A new `starved=true` variant blocks the test thread for 100 ms
with `Atomics.wait`, after the old settle timer has expired. This models a
descheduled CI worker. The original compensation assertions are unchanged:
one `adjustScrollTop(1)`, scrollTop 6, not sticky, and offsets corrected.

**How it works.** The test changes `it` to `it.each([false, true])` and
replaces fixed delays with `vi.waitFor` assertions. It keeps the 400 ms wait
after rerender.

**Code.** No production code changes. The test is
`ui-tui/src/__tests__/virtualHistoryOffsetCache.test.ts`.

**Tests.** Both variants of that test case in
`ui-tui/src/__tests__/virtualHistoryOffsetCache.test.ts`.

**Gotchas.** `Atomics.wait` on the main test thread requires a runtime that
allows it (Node/vitest workers do). The fixed 400 ms wait after rerender
remains.

**Plugin boundary.** None (test-only).

---

## Test-file inventory

| Ordinal | Test files (changed or new) | Production code |
| --- | --- | --- |
| 1980 | `tests/gateway/test_local_recovery_transport.py` (new); fixture `tests/gateway/fixtures/local_recovery_probe.py` | none |
| 1981 | `tests/cron/test_notepad.py` | none |
| 1982 | `tests/gateway/test_kanban_health_lock_observation.py` (new), `tests/gateway/test_kanban_dispatcher_health.py` | `gateway/kanban_dispatcher_health.py` |
| 1983 | `tests/gateway/test_session_kanban.py`; fixture `tests/gateway/fixtures/kanban_owner_probe.py` | none |
| 1984 | `tests/hermes_cli/test_kanban_awaiting.py` | `hermes_cli/kanban_awaiting.py`, `kanban_continuation.py`, `kanban_db_schedule.py`, `kanban_parser.py`, `kanban_timed_park.py`, `kanban_wait_info.py` |
| 1985 | `tests/hermes_cli/test_gateway_runtime_multiplex_home.py` (new) | `hermes_cli/gateway_runtime_multiplex.py` |
| 1986 | `tests/hermes_cli/test_dashboard_auth_cf_access.py`, `tests/gateway/test_context_fallback_label_gateway.py` | `hermes_cli/dashboard_auth/middleware.py` |
| 1987 | `tests/hermes_cli/test_kanban_owner_storage_refusal.py` (new) | `hermes_cli/kanban_owner_storage_refusal.py` (new), `kanban_owner_unavailable.py`, `kanban_host_refusal.py` |
| 1988 | `ui-tui/src/__tests__/virtualHistoryOffsetCache.test.ts` | none |

## Applying

These patches extend the curated mirror through 1979. They are not standalone
plugin packages or a safe cherry-pick menu.

Patch 1983's source parent is the upstream integration merge
`41f4b65b5737179508c60f4ee20160cb51711dd4`. The snapshot-bound replay plan
`core/mirror-replay-1988.json` therefore inserts the hash-pinned bridge
`core/bridges/upstream-through-41f4b65b573.diff` before 1983
(`5417a9eae1dcfb72c64590f4995092b768c4456b`). It keeps the 1979 plan's
seventeen bridges and three ancestry-only entries. Applying `1980–1988` as one
glob after 1979 is not equivalent.

Verify with the unchanged verifier and the 1988 policy and plan, in a
disposable environment whose source clone holds the frozen tip. Use the
upstream exclusion SHA recorded in the 1988 verification report:

```sh
python3 -B core/verify_mirror.py \
  --source-repo <source-clone> \
  --source-tip 4e97608f339eeac5d4efb57d1f39ca7fdd0ef5ef \
  --base d8c79e4b6b8c63e6c40167ff95ec28e7b1fee189 \
  --patch-dir core/patches \
  --policy core/mirror-policy-1988.json \
  --replay-plan core/mirror-replay-1988.json
```

A clean apply alone does not show that tests pass or that the deployed
behavior works. Never regenerate a published patch to make an incomplete
object store pass.

## Gotchas and verification boundaries

- Four patches are test-only (1980, 1981, 1983, 1988). Their longer bounds,
  frozen clocks and waits do not change runtime timeouts or production safety.
- Each changed regression file shows what is asserted, not that it was
  executed. Execution is reported in the 1988 verification, except for the
  1985 owner results quoted in its doc.
- These are untested here:
  - the `resource` park entry paths named above;
  - live Board dispatch;
  - a real managed worker hitting `storage_unavailable` mid-turn;
  - real Cloudflare Access;
  - live TUI rendering;
  - native Windows.

## Plugin boundary

All nine are **core** changes: four test-only fixes and five runtime changes.
No new plugin, enablement or config migration is introduced. Gates that park
cards with `resource` (1984) live outside core and use the existing CLI/tool
flags. Lock-ownership evidence, park premises, the auth middleware, crash
accounting and multiplexer routing have to stay at their core entry points.
