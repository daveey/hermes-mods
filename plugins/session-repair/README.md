# session-repair

[daveey 2026-11-20] "a scheduled job that scans sessions for weird or stuck statuses — like tasks waiting for workers
if a worker is available, unexplained waits, etc. I run into a lot of these and keep having to fix them; a
repair-session would help. It can then schedule fix cards as well." And: "make sure there is a concrete skill for
identifying broken sessions, so that when I find examples they go in there."

Three parts:

| part | what |
|---|---|
| `scan.py` | Read-only scanner. Every chat of this gateway with an open linked card (`tasks.session_id` or an unreleased `task_chat_links` row), walked with the session-panel feed (`Feed.compute` over its `CoreReaders`, loaded by path), so cards on each chain are judged too; plus every recent chat in `state.db` for undelivered notices. One rule per pattern of the [`broken-sessions`](../../skills/devops/broken-sessions/SKILL.md) skill, same ids. Prints `{findings, errors, chats, cards, duration_s}`. |
| `run.py` | The cron tick: scan, 24 h dedupe per fingerprint, caps, then log (shadow) or start one turn in the **Session repair** chat (live). |
| `dashboard/plugin_api.py` | `GET /api/plugins/session-repair/findings` (read-only): the tick's last scan, `?chat=<id>` to filter, `?fresh=1` to scan now without storing anything. For a future session-panel pip. |

## Patterns (v1)

| id | detects (variants) | automatic fix (live) |
|---|---|---|
| BS-ready-worker-free | ready with no dispatcher skip recorded in its ready episode (any age; a timed hold whose `until` passed does not count), and: `leftover_claim` (still holds a claim, > 10 min); `pool_could_run` (> 10 min); `free_slot` (core `kanban_dispatch_skip.unrecorded()`: >= 30 min, host `running < limit`); `free_worker` (> 10 min, a fitting active/unheld/undrained machine with `max_card_runs - active_card_runs` above the cards ahead; slots allotted oldest waiter first) | `hermes kanban dispatch --max 1` (`free_slot` only) |
| BS-overdue-wake | scheduled: `clock_overdue` (wake > 30 min late), `probe_overdue` (probe > 30 min past `next_at`), `no_wake` (no wake for > 30 min); excludes core's final-run reservation bound to the current park (an older park's reservation does not suppress it) | `hermes kanban unblock` (`clock_overdue` only; a probe park is a card: run the probe first) |
| BS-unexplained-wait | blocked/triage, never a valid ask: `invalid_human_request`; `no_reason` (no kind, no `reason`/`error`, >= 30 min); `agent_owned_stalled` (> 2 h; core `AGENT_BLOCK_KINDS`) | none: fix card |
| BS-parent-done | `parents_done`: todo / dependency-blocked whose parents (core `parent_states`) are all done/archived > 10 min, with none of core's holds (containment, spend decision, `recovery_held`); `awaited_done`: a park awaiting a card done/archived > 10 min | `unblock` (`awaited_done` only; `parents_done` is a promotion-defect card) |
| BS-stuck-run | running: `claim_expired` / `lease_expired` (> 5 min), `no_claim`, `quiet` (no event, heartbeats count, > 60 min) | none: fix card (never reclaims) |
| BS-review-no-reviewer | review with no running run > 60 min after the newest review event: `idle` (review dispatch on), `idle_manual_review` (off) | `dispatch --max 1` (`idle` only) |
| BS-broken-chain | `missing_parent`, `missing_awaited`, `cycle` | none: fix card |
| BS-notice-undelivered | last message a `kanban_notice` whose close-out ended `error` / `busy`, deferred > 2 h past due, claimed > 2 h with no verdict, or never claimed > 2 h (not human-blocked); `subscription_lag` (a `tui` subscription with an event undelivered > 60 min). Never the repair chat | one close-out turn (`hermes chat --resume <id> --oneshot`) |
| BS-long-wait | external-producer park: CI/deploy 45m, external 3h, card 4h (producer quiet 1h), resource 2h; GitHub event completed >=600s ago overrides age (producer `updatedAt`, never park age; missing timestamp cannot authorize a completion nudge); clock never | `nudge`: one owner comment plus a guarded reassessment wake (not release of a hold) |
| BS-quiet-chat | unresolved/waiting desktop/tui chat quiet >60m within 3d, no linked card, live cron/run or open ask; never repair chat | `nudge`: one resumed chat turn |

A finding: `{id, variant, card, board, chat, title, summary, evidence, fix: {kind: auto|card|nudge, action, commands,
why}, area: hermes|card, fingerprint}`; fingerprint = `<id>:<board>/<card>` (`<id>:chat/<session>` for a chat-level
one). `area` says who a fix card is for: `hermes` (Hermes machinery misbehaved) or `card` (the card owner's work).

Never judged: the Session repair chat (its `chat.json` id and any chat titled "Session repair"), cards the repair
session created (idempotency key `session-repair:...`, cards its turns reported as `card`, and cards the self-work
intake created for a request they reported as `queued` or that carried a `session-repair:` key; a card only
commented on or fixed stays judged), worker-node copies (core `foreign_ids`). A `card` finding with an open fix card
for its fingerprint (key range `session-repair:<fp>:`, any day, any board, or a card its turns reported creating for
it, not finished) carries `fix.open_fix_card`; the turn comments there. An agent-owned stall that chat-closeout's
owner wake already handled for the same premise (`owner-wake.db`: `started`/`routed` in 24 h, or a live claim younger
than its `CLAIM_TTL_S`) is skipped. Chats over
`MAX_CHATS` (1000) are counted in `truncated_chats` and logged.

Only this gateway's live board files are judged. A replica card (another gateway's) is skipped: its owner's scanner
judges it. Read-only: every handle is a session-panel `CoreReaders` connection (`mode=rw` + `PRAGMA query_only=ON`),
so a core helper that tried to write raises instead (`tests/test_readonly.py`).

## Modes and the repair chat

`SESSION_REPAIR` env, else `$HERMES_HOME/session-repair/config.json` `{"mode": ...}`, default **shadow**:

- `off`: no scan or repair (still records a tick summary).
- `shadow`: findings not seen in 24 h go to `actions-shadow.jsonl` as `would: fix|file`; no turn, no write.
- `live`: those findings (under the caps) go to ONE turn in **Session repair (routed)**.
  `consumer.py` uses the canonical gateway store to create/resolve that stable title with explicit
  `model: class:standard, provider: auto`, then reads back its exact identity and frozen launch policy.
  Only a policy-verified session is resumed:
  `hermes chat --resume <verified-id> --oneshot -Q --source cli --model class:standard --provider auto --query-file last-prompt.md`.
  Legacy titled CLI creation discards launch selectors, and resuming cannot change frozen policy; the old
  repair chat is retained without mutation. Existing-title policy mismatch refuses the turn, never migrates
  it. Normal dynamic route eligibility still applies; this is not a quota override. Action/delivery ledgers
  and unknown-delivery identities stay unchanged. Pinning uses the verified ID, never a newer title collision.
  The prompt is `prompt.md` + the findings JSON, each with an `idempotency_key` `session-repair:<fingerprint>:<UTC
  YYYYMMDD>`; it names the skill (a resumed chat refuses `-s`). The turn applies the `auto` fixes, files fix cards
  by the card policy (look for open work first and comment instead; name open work on the subsystem; `area: card` =
  product work, never `self_work`; `area: hermes` = Hermes is the exception: comment on the area's open card, a new
  card only on a dispatcher/credential/quota/money path or when it blocks the card, `self_work` only then and only
  when the schema exposes it; a queued acknowledgment is kept, never retried undeclared), never deletes or archives,
  and ends with a `session-repair-result` block giving each fingerprint's outcome. After the turn the chat is pinned
  and its id written to `chat.json`.
- Seen only when handled: the result block's `fixed`, `card`, `queued`, `commented`, `moved_on`, `false_positive`,
  or `deferred` with a `reason` (24 h). `over_budget` returns next tick with no attempt, only when the turn created its
  whole card budget; an unknown outcome, a reasonless `deferred`, an unearned `over_budget` or a finding left out of
  the block is a failed attempt. The block's `card` is only a card the turn created (or a queued request id);
  comments go in `commented_on`.
- Caps: at most 5 fix cards per run (the prompt's budget includes cards for safe fixes that did not take) and 20 per
  24 h, counted from the cards the turn reports (`ledger-<mode>.jsonl`); safe fixes 20 per run.
- Failures: a turn that exits non-zero or reports no result block counts one attempt for each finding it was given
  (retry after 15 min, doubling, at most 6 h) and backs off the next turn the same way; after 5 attempts a finding is
  given up for 24 h (ledger `gave_up`). The tick exits 1, so the cron job's failure target (`bot-chat` by default,
  never `local`) shows it.
- `ledger-*.jsonl` and `actions-shadow.jsonl` keep their newest 2000 lines.

State (`$HERMES_HOME/session-repair/`): `config.json`, `seen-<mode>.json`, `ledger-<mode>.jsonl`,
`actions-shadow.jsonl`, `attempts-<mode>.json`, `last.json` (the route's source), `last-prompt.md`, `last-turn.txt`,
`chat.json`, `run.lock`, `ticks.jsonl`, `ticks.jsonl.1`, `ticks.lock`.

Every tick appends an fsynced summary to `ticks.jsonl`, including silent/deduped scans, off/lock skips,
scanner exceptions and failed turns: `ts` (epoch seconds), `mode`, `chats`, `cards`, `findings` (count),
`errors`, `error_count`, `duration_s`, `scanned`, `skipped`, `handed`, and `build` (SHA-256 of scan/run sources).
A separate lock serializes journal appends. Whole segments rotate at 4 MiB, retaining one previous segment
(about 8 MiB bounded retention; normal 15-minute ticks retain far longer than 24 hours). An unwritable journal
fails the cron tick rather than claiming an auditable success. No cron output-history retention is needed.
For rollout, require a full 24-hour shadow window since the last detection/action change with no errors,
  no unexplained gaps/skips, and
adjudicate every would-act row in `actions-shadow.jsonl` (including any still-deduped findings in `last.json`).
Do not infer clean shadow from a missing journal or from zero repair turns. Test/doc-only changes do not
restart this window; compare the source diff when a build fingerprint changes, including rule and action
files, before treating different fingerprints as equivalent.

A scheduled future backstop with a fresh healthy core probe receipt is not a stuck wait. The scanner reads
same-episode `wake_check_alive` or `external_watch` evidence without executing probes. Health expires at
its cadence (remote alive receipts are rate-limited to three hours); failures, mismatched commands, stale
receipts and overdue deadlines remain eligible. A merged PR is not deployment completion when a healthy
installation probe is still waiting. The owner-wake transaction rechecks this guard as well.
When core widens a completed successful workflow to its commit's remaining workflows,
a same-episode `ci_wait_widened` must bind the original run and independently read head,
then a fresh healthy `github_run` receipt must bind that exact repository/commit.
That is a monitored aggregate wait, not completed-producer evidence. Missing, stale,
ambiguous, adverse or mismatched evidence stays eligible, as does a failed component.
The bounded read uses immutable timestamps rather than replica-local arrival IDs;
no source event, reassessment certificate or execution authority is created.

A missing nudge comment receipt remains `delivery_unknown`, is never resent, and makes the tick fail.
Only a fresh exact-target read showing a dispatcher `condition_met` with `external_result` after the
pending write, in the same parked episode, and an owner now ready/running/review/done resolves it as
`superseded_by_dispatcher` (non-error). The original frozen write and dispatcher receipt remain in
`superseded_delivery`; no comment delivery is claimed. Old errored ticks are never rewritten. A new park,
missing readback, probe health alone or mere scanner ineligibility cannot clear the uncertainty.

Unexplained-wait detection excludes canonical pending spend decisions in triage, including retained HOLDs:
these need an explicit owner decision, not a generic unblock. For other waits, an old generic park and the
panel's projected age cannot predate the latest run's admission/end boundary. This scanner-side guard avoids
reusing core projections from an earlier episode; it does not change the card, its budget or its block kind.

## Canonical age-only owner reassessment (WIP; not rollout proof)

`BS-long-wait` can omit only its `too_long` result while a Board-certified owner
reassessment remains current and `now < reassess_at`. It calls core's read-only
`kanban_owner_reassessment_replica.verified_for_event(conn, event, task, now=...)`
on the exact raw event, not a pending journal or a caller-supplied JSON receipt.
The task/event/receipt/adverse-event/evidence reads share a SQLite read snapshot;
an already-borrowed transaction is preserved. There are no writes, renewals or
capability/coverage advertisements. Existing watcher-receipt logic is unchanged.

On older cores, a missing optional reassessment module (including its submodules,
identified by `ModuleNotFoundError.name` starting with
`hermes_cli.kanban_owner_reassessment`) is **silent non-authority**: it cannot
suppress `too_long` and does not add a scanner error. Missing unrelated
dependencies, including other `hermes_cli` modules or an unknown module name,
and runtime/evidence failures on a capable core still leave `too_long` eligible
and report the failure in scanner errors. Exception message text is not capability
identity. `tests/test_reassessment_optional_core.py` exercises these paths through
the scanner, including misleading messages.

The scanner independently resolves **GitHub external targets**, encoded as
`{kind: external, identity: <exact producer URL>, revision: <full lowercase head SHA>}`.
`gh run view` supplies `headSha`; `gh pr view` supplies `headRefOid`. It also supports
opt-in **queue/update source readers** through `target_reader.py` and core's
`hermes_cli.kanban_owner_target`. They establish exact target identity/revision
and a bounded pending observation, **not producer health, a reassessment receipt,
or execution/release authority**. Target, structured producer and current park
must all agree with the certified receipt.

Unknown/unavailable readback, missing required portable evidence, successor runs,
expired retained leases, adverse watcher events, failed checks and completed
producers cannot use this age exemption. Containment, spend and recovery hold
exclusions remain; no other rule is exempted. No receipt is inferred from prose
or a future wake. Opaque external targets without an independent reader still
fail closed. Equal immutable park timestamps fail closed rather than using
remapped local arrival IDs; the receipt reader supports local execution tables
without tombstone columns and sync-shadow tables with `deleted_at`.

### Source registry and scanner adapter schemas

These are **administrator-owned**, separate files under
`$HERMES_HOME/session-repair/`: `target-sources.json` lives on the source host;
`target-readers.json` lives on the scanning gateway. Neither is installed or
activated by importing the reader. The awaiting `ref` is only
`owner-target:<authority>/<key>`; each label must match `[A-Za-z0-9_.-]{1,64}`.
It cannot supply a path, command or identity override. Examples below use generic
placeholder paths and repository names; they are not deployment configuration.

`target-sources.json` schema: an object with **exactly** `version` (integer `1`),
`authority` (the selector's authority) and `sources` (an object mapping selector
keys to one of the following exact-key objects). Paths must be absolute after
user expansion. Refs must be explicit valid Git refs; the update implementation
currently accepts only the main-channel values shown here.

```json
{
  "version": 1,
  "authority": "source-a",
  "sources": {
    "release-queue": {
      "kind": "queue",
      "queue_path": "/srv/example/queue/queue.json",
      "repository": "example/project",
      "pr": 1,
      "observation_path": "/srv/example/queue/pr-observations/1.json",
      "train_path": "/srv/example/queue/train.json",
      "pause_path": "/srv/example/queue/PAUSE"
    },
    "installed-core": {
      "kind": "update",
      "repo_path": "/srv/example/core",
      "repository": "https://example.com/example/core.git",
      "channel": "main",
      "remote_ref": "refs/heads/main",
      "target_ref": "refs/remotes/origin/main",
      "observation_path": "/srv/example/cache/target-observation.json",
      "stamp_path": "/srv/example/installed/install-stamp.json",
      "stamp_sha_key": "commit"
    }
  }
}
```

| Source | Field types and required independent evidence |
|---|---|
| `queue` | All fields are strings except positive integer `pr`. `repository` is `owner/repo` (each segment 1–100 letters, digits, `_`, `.`, `-`). Exactly one live `queued` entry for that PR must have a valid enqueue generation and reviewed full lowercase SHA. A fresh authenticated producer observation binds repository, PR, enqueue generation and head. Queue/train files must be fresh; pause markers, queue/entry pauses, hold/vet reasons, held candidates, single-candidate train failure, active train skips or vet state other than `ready` veto pending status. Historical terminal entries may coexist; missing/ambiguous live entries fail closed. |
| `update` | All fields are strings. `repository` must exactly equal the checkout's origin URL. `stamp_sha_key` is one of `commit`, `core_sha`, `full_sha`, `serves_sha`, `sha` and must read an **installed** full lowercase SHA, not an offered/prebuilt build. A fresh updater-owned fetch observation binds actual host, resolved repo path, origin, main channel, both refs and full target SHA. Local Git ancestry gives `pending` only when installed is a strict ancestor of target, `satisfied` when target is already installed or an ancestor of installed, and `failed` for divergence. Origin, target ref, stamp and observation are re-read after ancestry evaluation; changed/unreadable evidence fails closed. |

Queue producer observations have exactly `version`, `repository`, `pr`,
`enqueue_generation`, `head`, `observed_at`, `state`; update observations have
exactly `version`, `host`, `repo_path`, `repository`, `channel`, `remote_ref`,
`target_ref`, `head`, `observed_at`, `state`. Both use version `1`, state `current`,
and an acquisition timestamp in epoch seconds no older than 1,200 seconds and
not in the future. Source file mtimes must also be current where required.
Scanning or touching a file cannot renew acquisition evidence. Queue vetting has
no guaranteed refresh cadence: old evidence expires. The updater's fact comes
from its own successful fetch, not shared `FETCH_HEAD`; optional observation
failures do not stop ordinary updates. Even if invalidation fails completely,
an old matching fact retains only its original 20-minute lifetime.

`target-readers.json` schema: an object with **exactly** `version` (integer `1`)
and `authorities` (an object mapping authority names to fixed argv arrays).
Each argv has 1–32 nonempty strings, each at most 4,096 characters without NUL;
its executable must be absolute. A local example for the source above:

```json
{
  "version": 1,
  "authorities": {
    "source-a": ["/opt/example/venv/bin/python", "-m", "hermes_cli.kanban_owner_target"]
  }
}
```

For `owner-target:source-a/release-queue`, the scanner appends only
`observe --selector owner-target:source-a/release-queue` to that fixed argv.
**No shell is invoked and no command/path interpolation is performed.** A remote
adapter must separately provide existing authenticated transport with a fixed
administrator-approved destination/command and preserve this argument contract;
the observation JSON is not itself transport authentication. The source CLI
accepts only `observe --selector`, not a registry path override.

The response has exactly `version`, `observed_at`, `selector`, `target`,
`disposition`. Only version `1`, the exact selector, and disposition `pending`
can qualify. `target` has exactly `kind`, `identity`, `revision`: kind is `queue`
or `update`; identity is `<selector>@<64 lowercase hex digits>`, hashing actual
source hostname plus the normalized whole source config. Reusing a key for a
new component/path/channel invalidates old receipts. Queue revision is
`<enqueue-generation>:<40 lowercase hex digits>`; update revision is a bare
40-digit lowercase SHA. A pending observation still needs the separate canonical
same-episode receipt; it never authorizes suppression by itself.

The scanner bounds config/response JSON to 16 KiB and acquisition to 10 seconds,
rejects duplicate keys/nonfinite JSON constants, nonregular config files, failed
commands, wrong schemas and stale responses. Response age is at most 1,200
seconds, with up to 30 seconds of future tolerance for acquisition after the
frozen scan clock; this never extends a certificate deadline. Source JSON reads
are bounded regular-file reads (normally 1 MiB, with smaller observation limits),
reject changes during acquisition and use bounded read-only Git commands.
Only the configured source is consulted; unknown authority/key, missing facts,
`satisfied`, `failed` or `unavailable` responses cannot suppress. The scanner
shares its existing 20-target per-scan producer-read budget and caches reads;
there is no new poller. Awaiting gateway/board overrides are rejected.

### Proof boundary and remaining rollout work

`tests/test_reassessment.py` uses the real certifier and durable receipt verifier
with an **explicitly simulated** Board transport and the production SQL reader;
these are scanner adversarial/snapshot tests. Separately, isolated composed
fixtures exercised four fresh same-lifecycle opt-ins through genuine outbox,
authenticated Board HTTP/DO admission/evidence, durable cache, replica close/reopen
with remapped IDs, and the scanner. All four aged waits suppressed; sixteen paired
no-opt-in/expired/target-change/failure controls returned `too_long`. Queue fixtures
used real vet paths with a closed API fixture; updater fixtures used the actual
updater with local Git fetches. Controlled clocks/mtimes simulated aging.

This is **isolated proof, not production deployment, live credential validation,
historical receipt grandfathering or review approval**. No production source
registry, adapter transport or capability advertisement is activated by this WIP.
Exact-head review, publication/CI, installation and authenticated production
source configuration remain separate gates. Historical prose is not an opt-in.
Before per-variant promotion, complete the approved at-least-six-hour fixed-variant
shadow audit with every would-act a true positive and zero errors; nudges remain
shadow until independently certified. Existing spend/budget/block-kind, safety,
storage, dependency and unknown-delivery/no-resend boundaries are unchanged.

The canonical suite command is
`scripts/test-all --hermes-core <core-WIP> -k session-repair`; set a generous
per-suite timeout and retain `--output-dir`/`--json` receipts. Source-reader schema
and fail-closed cases are also covered in `tests/test_target_reader.py`.

## Independent nudge lane

`config.json` additionally accepts `nudge_mode: shadow|live` (default shadow). This does not enable the older
repairs; `mode=off` stops both lanes. `nudge.py` performs safe-auto nudges directly through normal CLI comment/chat
paths, after re-reading the target and all exclusions. No raw board SQL writes. A comment alone does not wake
scheduled cards: core's `unblock_task` API performs a guarded owner reassessment wake after the exact receipt,
with transactional same-park/event checks, human/live-run/containment/spend/safety/recovery exclusions and parent
re-gating. Pure clock waits are never woken. Record the `unblocked` event with `session_repair_nudge=<comment id>`;
recovery reads that event rather than waking twice, including on a tick with no fresh nudge findings (global
`mode=off` still does no work). A moved event or park cancels the action.
Legacy parks without `awaiting` use core's derived wait/probe reader: opaque probes are external (3h),
with deploy/CI text selecting 45min. A future probe backstop is not a clock; explicit clock declarations and
probe-less clock waits stay excluded. The current-episode park fences projected ages, and the same resolver
runs inside the guarded wake.
Each send has a durable pending identity and exact persisted comment/message receipt;
an uncertain delivery remains pending for readback, never blindly repeated. Comments are capped at 1,000 chars
including the receipt marker; producer state is a plain allowlisted summary, never the raw check/task JSON.

The same fingerprint is eligible again after 2h, not 24h. Later messages include nudge count/times; any non-nudge
owner event resets unanswered history. After three unanswered nudges the normal area-card fix path handles one
escalation instead of a fourth nudge, with its usual card caps and open-fix dedupe. Nudge caps are separate:
15/run, 100/rolling 24h (uncertain attempts reserve room). `nudges-{shadow,live}.json` stores cadence/pending state;
`nudges-{shadow,live}.jsonl` stores outcomes/receipts. Shadow actions also appear in `actions-shadow.jsonl`.

Nudge-only rollout: install merged main with `nudge_mode=shadow`, inspect all would-nudge rows from two consecutive
15-minute ticks (about 30 minutes), record each verdict, then set `nudge_mode=live`. Verify one persisted real
nudge and its owner's run after the recorded nudge wake (not an independent `condition_met`). Keep the older fixes' 24-hour shadow gate unchanged. Reverse by setting
only `nudge_mode=shadow`. The tick build hash includes scan/run/nudge/producer sources.

## Scheduling: Hermes cron, no-agent script

The tick is a Hermes cron **no-agent** job every 15 min (`hermes cron create --no-agent --script`, core
`cron/AGENTS.md`: "`no_agent=True` makes the script the whole job"; empty stdout = silent). Cron has no fixed-session
option for agent jobs (each run gets its own session; `--continuity` only injects the previous output), and a
`wakeAgent` gated agent job would also run in a new session each time, so the script starts the agent turn itself
with `consumer.py`'s canonical class-routed creation/readback followed by exact-ID resume. The scan runs only
when the tick runs; the turn only when something is new.

## Install (hub)

Not installed by this change. After the commit is on `main` and hermes-sync has linked
`~/.hermes/plugins/session-repair` on the hub:

```sh
ssh ubuntu@hermes-hub
cd ~/.hermes/plugins/session-repair
~/.hermes/hermes-agent/.venv/bin/python install.py            # shows the plan
~/.hermes/hermes-agent/.venv/bin/python install.py --apply    # wrapper script + config (shadow) + cron job
                                                               # (--failure-deliver <target> to change bot-chat)
hermes cron list | grep -A8 session-repair
tail -f ~/.hermes/session-repair/actions-shadow.jsonl         # what live would do
~/.hermes/hermes-agent/.venv/bin/python install.py --apply --mode live   # when shadow looks right
hermes plugins enable session-repair                           # only for the /findings route
```

`install.py --apply` writes `~/.hermes/scripts/session_repair_tick.py` (runs `plugins/session-repair/run.py`), writes
`config.json` (`shadow` unless `--mode`), and runs `hermes cron create "every 15m" --name session-repair --no-agent
--script session_repair_tick.py --deliver local --failure-deliver bot-chat --catch-up off` unless a job of that name
exists (`--failure-deliver local` is refused: failures must show). For `bot-chat[:profile]` the plan first checks,
with core's own helpers, that the profile has a canonical Bot Chat authority (`find_canonical_live_owner`) and
warning notifications on (`warning_notifications_enabled`, as cron's failure delivery does); otherwise it prints
`refuse:` and exits 2 without installing anything (the other steps print as `would ...`): pass another
`--failure-deliver`. An existing job whose `failure_deliver` differs is reconciled with `hermes cron edit <id>
--failure-deliver <target>` after the same check. Run it again: it
does nothing. To stop: `hermes cron pause <id>` or `--mode off`.

## Tests

```sh
uv run --no-project --python 3.14 --with pytest --with pyyaml --with fastapi --with httpx --with ruamel.yaml --with psutil python -m pytest -q -p no:cacheprovider plugins/session-repair/tests
```

- `tests/test_patterns.py`: every pattern and variant, positive and negative, on a board built with core's
  `kanban_db` in a temp `HERMES_HOME` (`tests/fixtures/patterns.py`): a skip recorded in the episode explains a wait
  however old, core's 30-min unrecorded rule, `pool_could_run`, probe-first `probe_overdue`, `no_reason` threshold and
  `error` reasons, core's wait constants, cancelled parents and core's holds (breaker, containment), worker copies
  skipped by every rule, `lease_expired` / `no_claim`, review dispatch off, `missing_awaited`, the repair chat and
  its own cards excluded (key, ledger ids, self-work intake receipts), open fix cards named on day two (prefix
  match, finished ones ignored), chat-closeout's handled stalls skipped, no `human` kind, `MAX_CHATS` truncation, the notice re-delivery command parsed by core's
  parser, the machine lane's slot allotment (registry and queue view stubbed), a chain card judged through the
  linked card, a healthy board with no finding.
- `tests/test_readonly.py`: a scan that finds every rule leaves the board and `state.db` byte-identical; every
  connection refuses writes; a rule that tries to write is a counted error, not a write.
- `tests/test_run.py`: modes, 24 h dedupe, caps (5/run, 20/day, cards for failed safe fixes counted, a new day),
  shadow logs and starts no turn, live starts one turn (fake `hermes`) and marks only what its result block handled,
  outcomes (`deferred` with a reason seen, `over_budget` next tick, unknown/reasonless/left out = attempts, `gave_up`
  rows), an open fix card reaching the prompt, exponential backoff and give-up after 5, no result block = failed, bounded
  logs, pinning, the lock, the turn argv parsed by core's own parser, and the prompt's card-policy text.
- `tests/test_routes.py`: `/findings` through FastAPI (last scan, chat filter, `fresh` scans and stores nothing,
  GET only).
- `tests/test_install.py`: the plan, `--apply`, idempotence, `--failure-deliver` (never `local`), the Bot Chat check
  (no owner, authority not ready, warnings suppressed: refused), the cron argv parsed by core's cron parser.

Untested: a real `hermes chat` turn and what the agent does with the prompt; the cron job firing on the hub; the
scanner against a WAL board on Linux under load (it was dry-run once on the hub's live data, read-only).
