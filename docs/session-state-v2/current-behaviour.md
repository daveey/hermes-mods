# SessionState v2 P0 — current behaviour, not the v2 contract

These fixtures freeze existing disagreements. They do **not** implement the approved v2 store,
resolution invariant or event budgets. The approved plan's acceptance criteria remain unchanged.

## Shared input and custody

`tests/fixtures/session-state-v2/scenarios.json` is the sole scenario manifest. It is standalone JSON,
version 1, with a fixed Unix `now`, synthetic owner identity, card defaults and 17 ordered scenarios.
There are no private chats, machine addresses or captured production payloads. Each scenario contains
card lifecycle facts, optional `awaiting`/producer, triage episode age, block/unblock chronology,
replica freshness, or runtime/turn facts. `human_request_from` references another entry by its exact
ID; consumers resolve it without copying a second scenario set. P2 can feed these facts to its reducer.

Core baseline source is e197ca9b07e96cbf58ab4ca76c5f64c8bcbe5ba8; mods baseline source is
 d0cc214b219526a261f9859d8c2c2c63ac1c62a0. They are historical code pins, not deployed-version claims.
Expected JSON was obtained by executing the current producers; tests compare subsequent executions
against committed expectations, never recompute expectations from another classifier.

## Executed boundaries

* Core: `tests/hermes_state/session_state_v2_fixtures.py` runs the real local card selectors,
  wait/detail hydration, card activity projection, `refresh_outcome` → `canonical_status` →
  `reduce_status`, and persisted outcome readback. Block→unblock→reblock refreshes each episode.
  It substitutes source inventories and ambient identity, not status/ask/wait/triage classifiers.
  Process evidence deliberately lacks a host-qualified run identity; this is **unknown**, not a
  claimed live-process proof. Running-card lifecycle without executor observations is kept as such.
* Desktop: `apps/desktop/src/test/session-state-v2-golden.test.tsx` consumes the real Python output,
  executes the dot store, stripe selector, rendered `SessionTabAccent`, native `turnDone` dispatch,
  browser attention/title flashing, and `fetchBlockedTasks` → grouping → rendered Blocked section.
  REST transport, runtime stores and SDK primitives are injected. Browser attention is explicitly
  invoked after accepted dispatch: this does not prove automatic bridge wiring or native pixels.
* Mods: `plugins/session-panel/tests/session_state_v2_fixtures.py` consumes that same core collector,
  runs actual `Feed.compute`/panel reduction, and creates asks through real user-steps
  `ActionStore` + `Mirror._request/_publish/present`. The mounted Panel/Chip test checks actual
  choice POST owner, request revision, choice ID, text and colors. It never delivers a real decision.
* Lanes: real isolated boards and replica → core flow snapshot → attention → collapsed/expanded view.
  Stale replica is stored only in the replica, with its old sync timestamp, and checked through the
  actual replica reader. No local healthy worker is substituted for it.
* Repair: real read-only scan with isolated stores/CoreReaders. Runtime cases also execute quiet-chat
  obligations and outcome audit, with actual canonical collector envelopes. Top-level `chats` counts
  local-card-linked chats; it is not the count of every audited chat. Remote cards are intentionally
  excluded from owner-only repair rules. Findings below reflect a one-hour fixture observation window,
  not immediate panel attention. The card/gateway clocks and each surface's input adapter remain distinct.

## Observed disagreement matrix

`background` is the dot's legacy category, **not** a live executor assertion. Stripe/tab share the
listed paint result. `turnDone` is the native completion dispatch guard's answer to an explicit
completion event, not a claim that a completion event is automatically produced in that state.
Lanes lists nonzero status buckets; attention rows appear only for the `you` cases.

| Shared scenario | Core kind / stored stripe | Desktop dot; stripe/tab | Panel rows | Ask / Blocked section | Lanes buckets | Repair findings | DISAGREEMENT today |
|---|---|---|---|---|---|---|---|
| running_card | running / waiting | background; waiting | Waiting, status unknown: no execution observations | none / 0 | run | none | Lifecycle says running; panel will not assert a live executor |
| human_brief | decision / stuck | background; stuck | Needs you, brief and bound options | bound / 1 | you | notice-undelivered | Dot is not needs-input despite red stripe/ask |
| human_no_brief | decision / stuck | background; stuck | Needs you, fallback copy and bound options | bound / 1 | you | notice-undelivered | Red is allowed without the v2 situation/recommendation invariant |
| waiting_card | waiting / external | background; external | Waiting on parent card | none / 0 | dep + run (prerequisite) | none | Parent execution makes stripe external; panel retains a dependency row |
| waiting_ci | waiting / waiting | background; waiting | Waiting on CI, ci category | none / 0 | ext | long-wait | Lanes external bucket versus declared-wait canonical state |
| waiting_clock | waiting / waiting | background; waiting | Waiting on a declared window; no delivery category | none / 0 | park | none | Legacy clock adapter does not produce panel clock color category |
| waiting_deploy | waiting / waiting | background; waiting | Waiting on deploy, deploy category | none / 0 | ext | long-wait | Lanes external bucket versus declared-wait canonical state |
| triage_599 | waiting / waiting | background; waiting | Waiting, agent category; reason/actor unknown | none / 0 | triage | none | Lanes does not encode the panel's age threshold |
| triage_600 | waiting / waiting | background; waiting | Waiting, agent category; reason/actor unknown | none / 0 | triage | none | Exactly 600 seconds does not escalate |
| triage_601 | waiting / stuck | background; stuck | Stuck, overdue triage; no invented ask | none / 0 | triage | none | Operational red has neither a human resolution nor Blocked section row |
| stale_replica | waiting / none | background; attention | Waiting, status unknown, last known running | none / 0 | run (reported lifecycle) | none; owner-only rules exclude it | Desktop unknown becomes attention; lanes retains reported running category |
| unblocked_after_block | waiting / waiting | background; waiting | Ready; execution reason unknown | none / 0 | worker | none | Ready differs from generic waiting; old ask is not retained |
| reblocked | decision / stuck | background; stuck | Needs you again with current bound ask | bound / 1 | you | none | New block episode restores the ask; repair's older-notice window differs |
| done | done / done | idle; done | Done history | none / 0 | no open card; done throughput 1 | none | Panel history remains; active-task chips correctly omit it |
| subagent_running | waiting / waiting | background; waiting | Running subagent | none / 0 | none | none; live obligation true | Canonical waiting versus panel Running |
| process_running | waiting / waiting | background; waiting | Waiting, unknown (no run identity) | none / 0 | none | none; live obligation true, progress baseline | Registry running does not establish executor identity for panel |
| turn_failed | unresolved / attention | idle; attention | No rows; mounted Panel hidden | none / 0 | none | quiet-chat | Core failure has no panel/card/lanes row |

`turnDone` is rejected for all card-open cases, including stale observations and both triage sides.
It is accepted for `done`, `subagent_running`, `process_running`, and `turn_failed`: the existing
notification guard checks card release, not runtime/turn completion. This is explicitly pinned as a
current disagreement. These tests authorize no transition and intentionally fix none of these behaviors.

## Running and reviewing

From an isolated core checkout:

```sh
scripts/run_tests.sh tests/hermes_state/test_session_state_v2_goldens.py
# With Desktop's lockfile-pinned dependencies installed:
cd apps/desktop
pnpm exec vitest run src/test/session-state-v2-golden.test.tsx
```

The isolated `session-state-v2.vitest.config.ts` permits a dependency-light targeted run from the
core root. Normal Desktop collection also discovers the test. Production fixtures run once in a
bounded setup hook; each assertion keeps its ordinary timeout.

From the companion mods checkout, selecting this isolated core explicitly:

```sh
PYTEST_ADDOPTS='-k session_state_v2' scripts/test-all --exact \
  -k plugins/session-panel -k plugins/lanes -k plugins/session-repair --hermes-core /path/to/core
HERMES_CORE_SOURCE=/path/to/core HERMES_DESKTOP_DIR=/path/to/desktop-deps \
  node --test desktop-plugins/session-panel/test/session-state-v2.test.mjs
```

Absent compatible core, the public mods suites skip with an explicit reason; a skip is not P0
acceptance. No-core CI cannot fetch private core. Run composed coverage with a compatible core and
require zero scenario skips. Golden JSONs are beside their owning Python tests; lanes/repair allow
explicit `SESSION_STATE_V2_UPDATE_GOLDENS=1` regeneration. Never regenerate merely to silence a
regression: inspect every changed output and amend this matrix. Native Desktop proof, network
latency, deployment, and 24-hour reconciliation are **not** claimed by these source fixtures.
