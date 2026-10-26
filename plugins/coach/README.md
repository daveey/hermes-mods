# coach

One nudge bus for Hermes, so coaching panels never pile up. It has two channels.

## Agent channel (invisible to you)

The plugin watches the agent's own tool calls. When it spots a known anti-pattern, it attaches a one-line hint to the
tool result the model reads next: a `coach_hint` key on JSON results, otherwise a `[coach] …` line. A hint still
pending at the end of a turn rides the next turn's `pre_llm_call` context. Each rule fires once per key per session.

| Rule | Trigger | Hint |
|---|---|---|
| `repeat_failure` | the same (whitespace-normalized) command or code fails 3× | stop retrying, reproduce, form one hypothesis, test it (systematic-debugging) |
| `poll_loop` | `sleep` inside a `while/until/for` loop, or ≥3 sleep/poll calls in the last 10 | `terminal(background=true, notify=true)` or a Kanban card, then end the turn |
| `no_checkpoint` | 40 tool calls in one user turn | write a state summary before more tool calls |
| `live_checkout` | `write_file`/`patch` under the running Hermes checkout, or `git commit/pull/reset/…`, `sed -i`, `npm run pack` there | use `hermes-core-wt new/ship` |

The live checkout is the tree that `hermes_cli` is imported from. Override it with `COACH_LIVE_CHECKOUT`.
The `live_checkout` hint attributes each mutation to its own directory (`git -C`, the last `cd` including one after a
newline or inside `bash -c '…'`, or `workdir`) or to a live path in the same simple command, so `cd other-repo && git
pull; grep x ~/.hermes/hermes-agent/f.py` no longer fires; `$VAR` redirect targets and whole-line `#` comments don't
count. The hint is checked in `transform_tool_result` too, because core's sequential path runs that hook before
`post_tool_call` and the hint used to land on the NEXT (often read-only) call. Backtest over 102k past terminal calls:
hints 296 → 131 (0 new), blocks unchanged at 61 (one false block gone, one newline-`cd` live merge now caught).

**Live-checkout guard (`pre_tool_call`, blocks before running).** A terminal command that would move the live
checkout's HEAD (`git commit/pull/reset/checkout/switch/merge/rebase/am/cherry-pick/revert`, attributed per
command through `git -C` or the last `cd`), repack the running app (`npm run pack`, `hermes desktop --build`) or run
`hermes update` is refused, and the message points at `hermes-core-wt`. Why: each HEAD move makes every backend
report code skew, Desktop restarts, and in-flight turns die in every chat. On 2026-10-13 the MacBook Desktop quit
about 10 times this way. `git checkout -- <file>` (discarding live edits), read-only git, worktrees and scratch
clones pass. A backtest over 37.6k past Hermes terminal calls blocks 43, and every sampled one was a real live-tree
HEAD move or repack. Escape hatch for a change the user explicitly asked for: start the command with
`COACH_ALLOW_LIVE=1`. Blocks are logged as `agent_block` in `log.jsonl`.

### Reply check (`pre_verify`, before the turn ends)

When the agent stops with its final message, `reply_check.py` looks at it once per turn. A regex prefilter
finds candidates. The aux task `coach_reply` then judges them, given the user's ask, the turn's tool log and a
private environment profile (`<HERMES_HOME>/coach/reply_profile.md`: which credential stores, hosts and browsers
this user's agents can reach). A hit keeps the turn going with a nudge the user never sees.

| Rule | Fires when | Nudge |
|---|---|---|
| `handoff` | the reply hands the user a step the agent could do itself (log in, click, run, paste...) | try it first with shell/ssh/browser/vault/token-broker; hand off only what needs hands or judgment, and say why |
| `confirm` | the reply ends with "Want me to…?" about a reversible, in-scope step | proceed, verify, report; ask only before irreversible or external actions |
| `unwatched` | the reply promises to continue after outside work finishes ("I'll write the report once the runs finish", "waiting on CI") and `wake_sources()` finds nothing armed: no live background process for the session or its compression ancestors, no open kanban card, no active goal, and no delegate/kanban/cron call this turn. A row like this gets painted done and its follow-up never runs | arm one `terminal(background=true, notify=true)` watcher on the real completion signal with a time cap (a kanban card for multi-hour work), or say plainly that the user owns the follow-up |
| `unverified` (off by default) | "done/fixed" with no check on the user's real path | verify on the user's path or say what's unchecked. With `diff_review` on it is also judged on turns that changed a git repo, and a hit there runs an Opus review of the diff (see Second opinions) |

Measured on 360 real replies against hindsight labels (the labeler also saw the user's next message). At
threshold 0.7: `handoff` precision 0.91, recall 0.33; `confirm` precision 1.0 on 4 fires, recall 0.11. Held-out
samples are small (n=120), so treat these numbers as early. `unverified` showed no usable signal.
`unwatched` checks wake sources deterministically before the judge runs, so a reply with a live watcher costs no LLM call. Of 3,260 recent Desktop replies, 299 matched the prefilter. On three real cases the judge scored the orphaned "once the runs finish, I'll…" reply 0.85 and scored the user-owned next step and the reply that armed a watcher below threshold. Watchers die when their backend restarts, so a promise made before a restart can become orphaned later; the rule only catches this at turn end.
Settings: `COACH_REPLY_RULES` (comma list or `none`) and `COACH_REPLY_THRESHOLD`. Needs core
`agent.pre_verify_every_turn: true` (fork commit `4c93d9c632`), because `pre_verify` otherwise fires only after
file edits. Nudges are logged as `reply_nudge` in `log.jsonl`; a turn the check ran on without a hit logs `reply_checked` (with the prefilter candidates).

### Goal disposition (`pre_final_gate`, experimental and default off)

`goal_disposition.py` checks diagnosis-only endings that do not contain an ask, promise or
completion claim. It uses the separate `coach_goal_disposition` auxiliary task (15-second
request timeout), not the reply regex prefilter or System One screener. A strictly parsed,
cited `abandoned` verdict at confidence ≥0.85 silently holds the candidate and asks the agent
to continue **only already-authorized** work. The hook never repairs anything, creates tasks,
retries approvals, sends messages, or changes self-heal configuration.

Profile-local `<HERMES_HOME>/coach/goal-disposition.json` accepts `{"mode":"off"}` (default),
`{"mode":"shadow"}` (judge/log only), or `{"mode":"enforce"}`. Reload the plugin after enabling
enforcement: buffering is selected at registration so the disabled hook does not buffer every
answer. Switching off takes effect on the next check; reload also removes buffering. A runtime
switch to enforce without a reload logs `reload_required_for_buffering` and does not hold.
Background agents remain excluded by core.

Evidence includes bounded past turn context captured at `pre_llm_call`, explicit user steering,
the candidate, and paired tool calls/results retaining call IDs and executor status. Structured
post-tool observations survive a later steering message. Orphan tool results cannot authorize work.
The schema rejects nonfinite scores, invented references, missing user citations and inconsistent
abandonment. Structured success/error fields are checked for remediation/capability claims;
HTTP 503 is not proof that the agent lacks repair capability. Capability boundaries require
`proof: {source_ref, action}` tied to a cited paired failed command or HTTP(S) URL. Later user
steering or another result for that operation invalidates the failure, including changed tool
names/options. Missing or unsupported proofs are UNKNOWN; after a confirmed hold they cannot
clear the unresolved fallback. Tests: `test_goal_capability_proofs.py` covers bindings and
freshness, `test_goal_capability_gate.py` covers verdict/recheck validation, and
`test_goal_capability_turn.py` exercises a real discovered multi-plugin agent turn: a superseded
credential error cannot release held text into returned, streamed, interim or persisted output.
This validates freshness, not goal relevance or an unblock plan.
Authorization and remediation now
require an additional `proof` object binding cited source to exact action/path. Authorization
currently supports a latest literal user restriction (`Do not`/`Never`, explicit-user-direction
path), or the native terminal pending-approval envelope with its exact command and `/approve`
path. Other policy/denial formats remain UNKNOWN, not permission to retry. Remediation currently
supports a requested exact HTTP(S) URL shared by user scope, tool arguments, result URL/status
and candidate; a newer readback invalidates the older citation. Non-URL verification and other
tool result formats remain UNKNOWN. Semantic judgment still decides whether that path fulfills
the goal; these narrow adapters do not complete general action/approval/path acceptance. Analysis-only, plan-first, status-only,
conceptual, hypothetical and genuine permission boundaries must not become repair mandates.

**Limits / not rollout-ready:** a conservative local running-task adapter reads the exact
hostname/profile/board/task from authoritative stores. It requires exact request/body and goal/title
matches, a live matching process claim, and an origin-linked TUI result subscription. It additionally
requires a native registered consumer and a recent successful poll of the exact board, bound to
profile home and session key. A subscription, thread name or attempted-but-failed poll is not enough.
The draft core's `kanban_consumer_health` publishes in-memory read-only receipts; there is no foreign
thread-frame introspection. This is point-in-time local consumer health, not eventual delivery.
Local scheduled ownership additionally requires a future native wake, the draft core's fresh
Linux dispatcher receipt for the exact board/profile/ready lane, and the same scoped return
consumer. Disabled/zero-capacity ticks do not certify; each board pass retains its own timestamp.
The native runtime root is resolved independently of the model. Same-UID runtime files are trusted
state, not authentication against malicious local writers. Tests: `test_goal_scheduled_ownership.py`
composes a real isolated board tick with the native consumer and verifies close/scope/return failures.
Local dependency waits also require every unfinished ancestor to have a verified live
worker, future scheduled wake, or bounded dependency chain to such owners. Both scheduled
and todo children are parent-gated: a timer cannot certify blocked or stale ancestors.
The child still needs the fresh dispatcher and result-consumer proofs. Review-lane wakes,
archived/ambiguous ancestors, and foreign-profile ancestors remain UNKNOWN.
`test_goal_dependency_ownership.py` covers native links, transitive/shared ancestors,
missing/stale proofs, SQL/graph limits and the public gate without database writes.
Remote, cross-profile, paraphrased-goal and non-TUI-return cases remain UNKNOWN. Native wake/dependency rows alone do not prove dispatcher health;
no cached tool assertion certifies ownership. Native dependency waits come from actual links,
not a `block_kind` label: `link_tasks` can gate a task without setting one. The bounded
ancestor readback rejects dangling links, cycles and oversized graphs, and never executes
wake commands. A valid wait snapshot still does not certify a live dispatcher.
The current origin request is read in a consistent state transaction and must not be observed,
synthetic/display-only, a compression summary, or marked as provenance-lost. Malformed
metadata is UNKNOWN, not trusted user scope. Tests: `test_goal_waiting_readback.py` uses
native wait producers and release operations; `test_goal_owner_user_provenance.py` covers
these origin exclusions and the public gate's UNKNOWN telemetry.
Scope recovery reads only real user rows through bounded, profile-qualified compression ancestry.
Rotation transfers captured restrictions/tool pairs; restart requires a unique current request anchor.
Native boolean `_queued_prompt` receipts are excluded from both history and durable recovery:
they are unexecuted queue placeholders, not current anchors or new authority. False markers
and ordinary executed repeated requests are not excluded. `test_goal_scope_queued.py` verifies
queue duplicates cannot poison the anchor or add unexecuted permission.
Before core persists that request, its runtime-stamped message timestamp bounds recovery instead.
Unknown profiles, ambiguous anchors and unsupported lineage fail UNKNOWN. Captures are isolated by
profile home and session; persistence metadata is not mistaken for synthetic content. Complete
evidence is limited to 200 rows / 32k serialized characters and 128 captures.
Oversized evidence fails open with telemetry. This is not a hard completion guarantee: judge errors,
missing evidence and timeouts fail open. No live-model precision/recall or latency claim is made.
Do not enable enforcement until owner/lineage support and a labeled semantic holdout are validated.

Each held candidate is rechecked. At the core hold cap, the plugin appends **Unresolved work**
and logs `abandonment_exhausted`, without claiming a continuing owner. Holds also supply the
structured `unresolved_fallback: {goal, evidence_refs}` contract for cores supporting safe budget
fallback. Older cores ignore that field and can release the rejected candidate on budget exhaustion;
the cap append alone does not fix that branch. Core cancellation/Stop retains precedence.
Telemetry uses `goal_disposition`, `goal_disposition_unknown`, and `abandonment_exhausted` in the
coach log, without storing full tool results in those events.

Tests: `test_goal_disposition.py` covers strict schema/provenance, structured result validation,
mode/buffering, scope-preserving capture and bounded errors. `test_goal_proofs.py` covers
exact action/path/source bindings, invented approval, untrusted printed status, URL-prefix
mismatches and superseded evidence. The manager suite proves unsupported approval cannot clear
a confirmed hold through the complete turn. `test_goal_disposition_integration.py`
uses scripted model responses through complete `AIAgent.run_conversation` text stops for `cli` and
`desktop`, including isolated normal plugin discovery, streaming holds, cap warning and ephemeral
history stripping. It also loads coach with ask-gate/chat-closeout in two configured orders and
force-reloads discovery. `test_goal_manager_acceptance.py` additionally registers plugins in genuinely
reversed orders, exercises individual unload/reload during held complete turns, and verifies no
rejected text leaks to streams, interims, returned history or SQLite. It verifies pre_verify runs
once only after acceptance and persisted history restrictions survive pre-persist capture.
`test_goal_scope_gate.py`, `test_goal_scope_recovery.py` and `test_goal_scope_rotation.py` cover
profile isolation, bounded ancestry, restart and rotation recovery through real isolated SessionDBs.
`test_goal_owner_gate_liveness.py` and `test_goal_ownership_liveness.py` require native successful
poll receipts and exercise isolated completion collection to the exact origin test sink. Failed
board polls cannot certify return. This is not actual Desktop socket delivery acceptance.
`test_goal_restriction_gate.py` and `test_goal_restriction_turn.py` verify a deliberately narrow
whole-turn veto: an entire latest user row such as "Stop.", "Cancel.", "Analysis only.",
"Status only.", "Plan first.", "Do not continue.", "Do not change anything." or
"Do not execute anything." cannot be overridden by a mistaken abandonment verdict. Quotes,
partial action restrictions and compound instructions do not match this literal veto; broader
scope remains semantic, and lack of a veto is never permission.
`test_goal_serve_acceptance.py` runs actual isolated `hermes serve` startup on loopback,
with synthetic token authentication and a network-fenced local primary/judge HTTP endpoint.
It verifies missing/wrong tokens are denied, held candidates stay off wire/logs/persistence,
and ordinary plus deterministically queued analysis-only next turns are assessed and complete.
These are backend tests, not Electron UI, public OAuth or production activation proof.
Semantic judges are injected; scope negatives test routing, not model classification accuracy.
`test_goal_ownership.py` also exercises real isolated Kanban tables through discovered agent text
stops, scope/liveness/return-route rejection, and read-only WAL fallback without creating databases.

Additional native process-return certification: `test_goal_process_profiles.py` exercises
real temporary named-profile homes and concurrent multiplex profile scopes, real child exits,
native watcher/busy-queue admission and the public ownership gate. It verifies same-chat profile
isolation and targeted Stop/new-user/adapter-loss revocation. Adapter maps and watcher descriptors
are fixture-assembled in that suite. `test_goal_process_native_terminal.py` additionally
exercises registered terminal dispatch on worker threads, native watcher registration and
loop arming, and shared-bot satellite/own-bot routing through real profile scopes and busy
admission. Receiving-bot loss revokes proof even when another bot remains alive. Adapter
startup state remains fixture-installed; no authenticated transport, model turn, service
restart or cross-gateway delivery is certified. `test_goal_process_run21_review.py`
verifies route-component source replacement at
admission/readback fails closed, while equivalent copies and nonrouting changes preserve proof.
These tests do not establish production activation or satisfy the historical incident replay gate.

### Fix loops across turns (`pre_llm_call` + `post_llm_call`)

`post_llm_call` remembers each turn's final answer and tool log. On the next user message, `fix_loop.py`
prefilters it (still / again / I don't see it / a bare screenshot ...), and the aux task `coach_fixloop` judges
whether the user is reporting that the agent's claimed fix failed. Confirmed failures count within 8 user turns:

- 1st: hint to reproduce on the user's actual path, root-cause, and verify it the way the user will see it.
- 2nd and later: hint to stop patching, list what was tried plus hypotheses, check the distinguishing evidence,
  consider a read-only second opinion or a clean-restart brief.

The user-channel `still_broken` panel now counts these judge-confirmed failures (2 = Anthropic's "after two
failed corrections") in place of 3 regex hits. Set `COACH_FIXLOOP=0` to go back to the regex.
Backtest on 313 real (reply → user) pairs against hindsight labels (the labeler also saw the next turn), at
0.8: precision 0.84, recall 0.35. The old regex scored 0.47 / 0.15. Held-out fired only 3 times, so its 0.33
says little; most of the disagreements were cases the hindsight labeler excused, like "it turned out to be the
old GIF".

### Second opinions that run (`second_opinion.py`)

The two checkpoints above used to be advice only ("consider a second opinion", "verify it"). With these settings on,
each one now starts a strong-model review itself. Both are off by default, which keeps the old behaviour; the shared
config turns them on.

- **Fix loop → review card.** On the 2nd judge-confirmed failed fix inside one window (8 user turns), the plugin opens
  ONE Kanban card through the `kanban_create` tool handler. That is the path that stamps this session as the card's
  origin and subscribes the chat, so the card shows in the chat's task strip. The card is pinned to
  `model: claude-opus-5-5`, has a 30-minute runtime cap, and is assigned to `default`. Its body holds the original
  ask, each "fixed" claim the agent made and the user's reply to it, the files touched in those turns (from the tool
  log), and the instruction **"trace and report what you observe; do not edit"**. The agent's hint names the card and
  says not to patch again until it has read the handoff (`kanban_show`). Idempotent: the key is
  `coach-fixloop:<session>:<first failure time in the window>`, the card id is cached in the loop state, and a 3rd
  failure in the same window reuses the card. The loop state now persists in `sessions/<id>.json`, so a window
  survives backend restarts and `hermes chat -q --resume` (one process per turn). A refused create, for example from a
  Kanban worker's fenced subprocess, is logged as `fixloop_card_error` and leaves the hint as it was. Logged as
  `fixloop_card`.
- **Unverified + repo diff → diff review.** At `pre_verify`, if this turn changed a git repo (core's
  `changed_paths`, `patch`/`write_file` in the tool log including V4A multi-file headers, or a `git commit` under
  `git -C X`/`cd X`), `unverified` joins the judged rules for that turn only. If the judge then flags it (threshold
  0.7), one review runs on the strong model (aux task `coach_diff_review`). The reviewer sees the ask, the final
  message, the tool log, and the turn's diff: commits made since the turn started, the uncommitted diff, and new
  untracked files the turn wrote. It is prompted neutrally: trace the diff against the claims and report what it
  observes. Its findings come back through the existing `pre_verify` continue path, so the agent keeps working.
  Bounds: at most one review per turn, the diff capped at 60k chars, and a 120 s call timeout. If the review fails
  or the diff is empty, the turn falls back to the next real rule over threshold, or nothing. Logged as `diff_review`
  (repos, findings, diff size, seconds).

Settings (env wins, then config `plugins.entries.coach.settings.<key>`): `fixloop_review` / `COACH_FIXLOOP_REVIEW`,
`fixloop_review_model` (default `claude-opus-5-5`), `fixloop_review_provider`, `fixloop_review_assignee` (default
`default`), `diff_review` / `COACH_DIFF_REVIEW`, `diff_review_max_chars` (60000), `diff_review_timeout_s` (120), and
`diff_review_threshold` (default: the reply threshold). The reviewer model is `auxiliary.coach_diff_review.{provider,model}`
(shared config: anthropic / claude-opus-5-5). Without that key, `fixloop_review_model` is passed.

**Cost, measured (MacBook `state.db`, 14 days to 2026-10-19, 6,717 non-cron turns in 2,484 sessions).** Method:
`eval/second_opinion_replay.py --judge` rebuilds every turn and replays the shipped detectors, including the real
aux judges.
- Fix-loop cards: the prefilter hit 429 user messages. The judge confirmed 16 failed fixes, and **2** of them were a
  2nd failure in a window, so **2 Opus review cards in 14 days**.
- Diff reviews: 579 turns had a "done/fixed" reply and changed a git repo (desktop 249, kanban 328, other 2). Repos
  are checked on today's filesystem, so deleted scratch workspaces don't count, and this undercounts Kanban work. The
  judge scored `unverified` ≥ 0.7 on **1** of them, so **1 Opus diff review in 14 days**. The larger cost is the
  judge itself: up to 579 extra Sonnet `coach_reply` calls (~41/day). Some of those turns already reached the judge
  through `handoff`/`confirm`/`unwatched`.
- The replay eval doesn't regress: `coach_eval.py detector` with the new modules against HEAD gives identical
  numbers on every rule (replyset handoff P 0.91 / R 0.33, confirm 1.00 / 0.11; fixloop 0.84 / 0.35). Its verdict is
  "REJECT: no gain", because these changes add actions, not detection.

### Lessons from corrections (`lessons.py`)

Every real user message is judged by the aux task `coach_lessons`. Bare acks ("go", "do it", "ok"), slash
commands and image-only sends are skipped. The judge runs in a background thread, so the turn never waits, and its
hint arrives with the next tool result. A keyword gate caught only 51% of the 138 corrections in 700 mined
exchanges. The skip list misses none of them and judges ~85% of messages. When a message is a correction, the judge
extracts a standing rule (`rule`, `slug`, `scope`), or nothing if it was a one-off. It sees the known rules, so a
restated rule reuses the existing slug instead of starting a new candidate. Each rule lands in
`<HERMES_HOME>/coach/lessons.json` as a **candidate**. When its slug recurs in a 2nd session it is **promoted**.
A rejected slug stays rejected. A `saved` rule (already in memory) only produces a reminder. The agent gets the rule as a hint right away. Once a rule is promoted, the hint
also tells it to save the rule with the memory tool unless memory already has it. `COACH_LESSONS=0` turns this off.

### Self-heal detectors (`self_heal.py`, shadow by default)

Spots an agent/Hermes defect as early as possible, so a fix can be filed without you asking.
Two entry paths, one result `{p_defect, kind, component, symptom, fingerprint, source}`:

- **Complaint** (`pre_llm_call`): every real message except bare acks, slash commands and Hermes-written notices
  goes to the aux judge `coach_defect`, with the agent's previous reply and that turn's tool log. It counts
  corrections ("you buy the credits"), failed fixes ("nope, still broken"), needless handoffs ("you have ssh"),
  frustration, Hermes or product misbehaviour, and nudges ("resume", "what's going on") after a stalled turn.
  A first-stage score below 0.6 is sampled once more when it is borderline (>= 0.3) or the message asks to fix /
  root-cause something (flash at temp 0 scored one identical turn 0.7, 0.6 and 0.05); the higher sample counts.
  A first-stage score >= 0.6 escalates: a second flash opinion on a stricter prompt and, when
  `auxiliary.coach_defect_confirm` (or `COACH_DEFECT_CONFIRM_MODEL`) is set, a stronger model on the same prompt
  (on the hook path and the digest backstop alike); `p_defect` is the mean of the stage scores. The strict prompt
  counts a fix/root-cause request for a Hermes defect the agent's own message described, and a product problem the
  agent's own work caused; Hermes itself is never a `product_bug`. Outside-world problems (`kind: external`) never count.
- **Turn end** (`post_llm_call`): machine signals first, no judge (a provider-error reply, an interrupted turn, one
  tool error repeated 3×). Then, when the reply admits a mistake, hands you a step, or the turn's tool log shows
  errors, a turn-end audit by the same judge; only self-admissions file (the other audit kinds cost more false alarms
  than they catch), at `COACH_SELFHEAL_AUDIT_THRESHOLD` (default 0.9, after the confirm model when set).

Both judges append the private overlay `COACH_REPLY_PROFILE` / `<HERMES_HOME>/coach/reply_profile.md` (shared with
the reply check), so they know your standing rules. The overlay is rules and environment only: output-format lines
("Return ONLY JSON …") are stripped on load, because a stray reply-check schema line at its end made the self-heal judge
answer in the wrong keys, and `parse` takes only an object with `p_defect` (another schema = no vote, never 0). The
digest backstop also shows the judge the agent's reply to the turn, so a screenshot-only report reads as what it was.
Replay of the week-of-10-12 Hermes fix requests: 6/25 -> 12/25 caught; a 150-turn control fired on 33, nearly all
real complaints. With the resample, the strict-prompt changes and the confirm model (`claude-sonnet-5`) also on
the backstop: 19/25 and 16/25 on two runs (13/13 without), control 28 and 30 (32 without).

Both run in a background thread (the turn never waits) and are fail-open. They skip cron, subagents and Kanban
workers. The complaint prefilter also rejects cross-session courier messages, generated blocked-card notices and
standalone chat-closeout messages before any judge call. Those are coordination, not fresh user complaints;
underlying failures remain eligible through machine signals. Real user text before an appended notice is retained. `COACH_SELFHEAL=off|shadow|live` (default `shadow`): shadow logs a `selfheal_detect` event to
`coach/log.jsonl`; live also appends the defect to `coach/selfheal_queue.jsonl` for the self-heal lane, once per
fingerprint per UTC day (`coach/selfheal.json` holds the fingerprints). The fingerprint is a hash of the component
and the symptom's stemmed content words, so "sidebar sessions reshuffle" and "session sidebar reshuffles" match.
`COACH_SELFHEAL_THRESHOLD` (default 0.6). Set the judge model with `auxiliary.coach_defect.provider` / `.model`
(evaluated on `gemini-3.8-flash`) and the confirm model with `auxiliary.coach_defect_confirm` (evaluated on
`claude-sonnet-5`).

Replay evals:
- `eval/selfheal_incident_replay.py` (`run`, then `score --relabel --report`): the production code over an
  incident-labeled turn set (episodes + five-whys verdicts + every normal turn), scored like the self-correct replay:
  caught before any fix ask, caught counting the fix ask, false positives per normal turn, every miss with its quote,
  every false positive, catch per root-cause cluster.
- `eval/selfheal_replay.py`: per-turn catch rate / precision on a hand-labeled set (`--mode admission` for the audit).
The real sets hold your own words, so they live in the private coach data repo.

### Self-heal lane (`self_heal.py file_defect` → investigation → Inbox proposal → Do it → fix)

Every new fingerprint (first time that UTC day) goes to `file_defect(d)`, from any entry path: the two hooks above
(complaint, admission, turn-end machine signal), the digest backstop, and direct callers (machine-signal sweepers,
monitors). `d` = `{fingerprint, component, symptom, source, kind, p_defect, session_id, quote, host, at, details}`.

- **Where it decides.** On the hub (`coach/selfheal-config.json`: `hub_host`, `hub_ssh`, `board`, `bb_board`; no hub
  configured = this host) it decides locally; on any other host, live mode pipes `d` to `self_heal.py file --mode live`
  on the hub over ssh, so dedupe, recurrence, suppression and budget live in one place (`coach/selfheal_lane.json`).
- **Shadow** (`COACH_SELFHEAL=shadow`, else `mode` in `coach/selfheal-config.json`, default shadow) logs `selfheal_would_file` with the full card args and creates
  nothing. **Live** creates the investigation card.
- **Investigation card**: key `selfheal:<fp>`, created_by `self-heal`, model `claude-opus-5-5`, 1 h, skills five-whys +
  hermes-mods-repo (Baby Bear: board `bb`, five-whys + bb-self-fix). Its brief makes it investigate live, with a
  per-component checklist (Kanban events/runs, surface writer + backing state, processes/launchd/logs, git log), write
  the five-whys chain, and post ONE Inbox card of type `selfheal` (key `selfheal:<fp>`): **What happened** (symptom,
  PT time, `@session` link, quote), **Why** (the chain with evidence), **Plan** (changes, regression test, detection
  guard, live check), cost/risk, undo. It never ships the fix. Not a defect → `event not_defect`, no Inbox card.
- **Do it** resolves the Inbox card; the hub cron (every 5 min) sees the outcome and runs `file_fix` (also `self_heal.py fix <fp>` by hand): fix card `selfheal:<fp>:fix` whose acceptance is the
  regression test red before / green after, ship under the standing approval, verify live, and re-post the same key
  as a `selfheal-result` card "Fixed: …" with the proof (OK / Undo). **Not now** snoozes the fingerprint 14 days,
  **Never** suppresses it. (Do it is not a Kanban `task` action because a worker is fenced from creating cards via the CLI.)
- **Recurrence**: a fixed fingerprint that comes back files `selfheal:<fp>:r<n>`, whose brief says "previous fix <id>
  didn't hold" and requires a detection-level guard.
- **Budget**: engine_budget lane `selfheal` ($25/UTC day, its own cap outside the $20 engine total; spend = these
  cards' worker sessions), ≤ 3 running, ≤ 1 filing per fingerprint per UTC day. Over the $ cap the card is created
  and scheduled to the next UTC midnight; at 3 running, 1 h out. Each card body carries a `Budget for this card` line
  ($8 investigation, $15 fix) for the engine watchdog.
- **Hub cron** (`~/.hermes/scripts/selfheal_lane.py` → `self_heal.cron`, every 5 min, no agent): Inbox sync, queue drain, and every 25+ min the digest backstop (the complaint detector over
  new user turns of interactive sessions in the coach digests, per-session cursor; the judge gets each turn's `prev`
  agent reply from digest v6 plus the session title and the 3 earlier user turns, since without them the strict stage
  scored context-free reports like "why didnt this work [screenshot]" as new issues), Inbox sync. In the cascade a
  stage whose answer is empty or unparseable is retried once and then left out of the mean, not counted as a 0.
- Events (coach `log.jsonl`, read by `coach_weekly.py`): `selfheal_would_file`, `_filed`, `_recurrence`, `_skip`,
  `_proposed`, `_approved`, `_fixed`, `_reverted`, `_not_defect`, `_declined`. Cards report theirs with
  `self_heal.py event <kind> <fp> --card <id>`. `self_heal.py status` prints the lane state and budget.
- Tests: `tests/test_self_heal_lane.py` (each entry path, dedupe, recurrence, budget, bb routing, shadow vs live,
  Do it → fix card, Not now / Never). `COACH_SELFHEAL_NO_EXEC=1` (set by the test conftest) refuses any real
  `hermes`/ssh call.

### Machine-signal sweeper (`signals.py`, no agent, no LLM)

Raw machine signals become `file_defect` calls with `source: signal` and stable fingerprints:

| kind | source | fingerprint (host in it?) |
|---|---|---|
| `errors` | new tracebacks in `<home>/logs/errors.log` (any level), minus tool output (`agent.tool_executor`) and provider transients (429/529/overloaded/timeouts) | exception type + innermost 4 frames as `file:function`, no line numbers (no) |
| `hook` | `Hook '<hook>' callback <cb> raised: …` (plugin hook exceptions) | hook + callback + exception type (no) |
| `cron` | a job whose sticky `last_failure.at` is new (`cron/jobs.json`), even if it has recovered since | job name (yes) |
| `kanban` | a card with ≥ 2 runs `crashed` / `timed_out` / `spawn_failed`, every board db; self-heal's own `selfheal:` cards are skipped | board + card id (yes) |
| `gateway` | `gateway.previous_unclean_exit`, `asyncio.run.exception`, `gateway.exit_nonzero` in `logs/gateway-exit-diag.log` (a clean exit or a code-75 restart is not a crash) | tag + exception type (yes) |
| `coach` | coach `fixloop_card` (2nd confirmed failure of a fix) and `reply_nudge` with rule `handoff` | session (+ issue) (no) |
| `monitor` | a propose-only monitor's finding (`route-monitor`, below) | the monitor's own dedupe key |

Profiles (`<home>/profiles/*`) are swept too. Cursor `<home>/coach/signals-cursor.json` (offset + inode per log, last
run id per board, last failure per job); first sight looks back 7 days; a rotated log is re-read; ≤ 25 signals per
kind per pass, same fingerprint folded into one (`seen Nx`).

- **Hub**: cron `self-heal signals (hub)` (`~/.hermes/scripts/selfheal_signals.py` → `signals.py --host hermes-hub
  sweep`, every 30 min, from the scheduler since a Kanban worker can't create cards). It also drains
  `<home>/coach/signals/spool.jsonl`, where the other hosts' signals land.
- **Macs / mini**: `signals.py forward` ships new raw signals over ssh to the hub's `signals.py ingest` (the hub
  files them); the cursor only moves when the hub accepted the batch. Every pass sends a heartbeat first (even with
  no signals), recorded in the hub's `coach/signals/hosts.json`; the hub sweep files a `plugin` defect for a host
  whose forwarder has been silent > 6 h. It rides on the existing launchd coach feed
  (`hermes-coach/scripts/coach_feed.py`, every 30 min) and is a no-op without a `hub_ssh` in
  `coach/selfheal-config.json` or on the hub itself. The ssh to the hub pins the key (`hub_ssh_key` in that file,
  default `~/.ssh/id_ed25519`) with `IdentitiesOnly=yes`, because launchd jobs offer different default identities
  than a shell and the hub refuses them preauth. Each outcome prints one line, which the feed records as
  `<host>/feed-status.json` in the coach data repo (`signals`: ok / no-hub / hub / error, plus the error text and
  `since`; rewritten only when that state changes). That file is the hub's view of a forwarder that has never
  reached it, which the silent-host check can't see.
- **Monitors**: `signals.py route-monitor` (JSON `{monitor, key, title, body, prompt, component}` on stdin) files the
  finding and prints `{"owned": …}`. `owned` = the lane filed it or already has it open; then the monitor posts no
  Do-it card and the investigation posts the What happened / Why / Plan proposal. In shadow mode (or on any error)
  it is not owned and the monitor posts its old card. Callers: mac-worktree-gc (leaks, GC abort, stale report),
  guard-conformance, and `coach_askgate.py --post --selfheal` (each hit the lane has no filing for; the weekly card
  keeps its review prompt only for hits the lane did not take).
- Tests: `tests/test_signals.py` (one per source with fixtures in `tests/fixtures/signals/`, cursor, rotation, hub
  sweep in shadow, forwarder success/failure, ingest, monitor routing and `lane_owns`; also passes under Python 3.9,
  the Macs' `/usr/bin/python3`).

## User channel (Desktop panel above the composer)

Each panel has one primary button and Dismiss. No menus.

| Rule | Trigger | Button |
|---|---|---|
| `still_broken` | 3rd user turn like "still not working / same error / didn't work" | **Restart clean**: a new session whose first message is a brief (Goal / Context / Tried so far / Repro / Done when) + an `@session` link |
| `long_session` | over 250 messages (counted across compressions) | **Spin out**: runs the spin-out plugin's planner, and its panel takes over. **Compact** (secondary): runs `/compress` in the session |
| `screenshot_midturn` | an image with fewer than 3 words, sent while a turn runs | **Add a line**: prefills "About the screenshot: " |
| `move_to_board` | the 10th subagent in the session, or a job over 10 min in chat (a tool call that ran ≥10 min, or a background job still being polled 10 min after it started) | **Move to board**: a handoff card (brief + `@session`) on the active board, made with spin-out's `kanban_cards` |
| `hermes_bug` | the same Hermes failure signature (an exception in a Hermes tool, a traceback from Hermes code, or a Hermes-infra tool error such as `cross_session`) in 2+ sessions within 7 days | **File it once**: one card on the `hermes` board (idempotent per signature, never offered again) |

### Guardrails (one gate, server side)

- At most 1 nudge per session per 15 user turns.
- A dismissal mutes that rule for the session.
- 3 dismissals of a rule within 7 days demote it to digest-only. It is then logged as `digest` and not shown.
- Everything goes to `<HERMES_HOME>/coach/log.jsonl`: every shown nudge, every choice (`accepted` / `dismissed` / `expired`), every digest-only suppression and every agent hint. `GET /api/plugins/coach/stats?days=7` reports precision per rule as accepted ÷ (shown − expired), which is KPI 4.

### Feature curriculum (tips)

`curriculum.yaml` lists features to learn: each entry has a live `trigger`, a one-line `tip`, an optional `try`
drill, the `commands` it names, and an `adopted` test. A private overlay at `<HERMES_HOME>/coach/curriculum.yaml`
adds entries or overrides them by `id` (`enabled: false` drops one). The file header documents both mini-languages.

| Tip | Trigger | Retires when (14 days) |
|---|---|---|
| `project_bot` | 6th session in the same repo in 14 days | 3 interactive sessions in a non-default profile (a Bot) |
| `kanban_max_runtime` | a job over 10 min in chat | `kanban_create` with `max_runtime_seconds` in 3 sessions |
| `todo_checklist` | a turn with 3+ list items, and no todo use yet | todo used in 3 sessions |
| `cron_whenever` | "every day / whenever / each time" | a cron job created in 3 sessions |
| `worktree_parallel` | a 2nd live session in the same repo | 2 sessions in a linked git worktree (sibling `../repo-x` or `.worktrees/`), or 2 worktree cards |
| `session_mention` | "as we discussed / in the other session" | `@session:` typed in 3 sessions |
| `kanban_l1_boards` | "follow-ups / next steps / backlog" | 5 cards created from your own chats |
| `kanban_l2_fanout` | 5 subagents, or 3 `delegate_task` calls | 3 cards with `parents`, or one `kanban swarm` |
| `kanban_l3_review` | "deploy / ship / you said it was done" | 3 `request_review` / `request_changes` |
| `kanban_l3_goal_mode` | 2nd "still not working" | 3 `goal_mode` cards |
| `kanban_l4_triage` | a message from a phone platform, or "at some point / no rush" | 3 triage cards, or 2 `specify` / `decompose` runs |
| `kanban_l4_specialists` | 3 `kanban_create` calls in the session | 3 cards with a non-default assignee |

How a tip shows: tips rank below every bus rule, pass the same gate (rule name `tip:<id>`), and appear as one panel
with **Try it** (pre-fills the drill in the composer) or **Got it**. Spacing lives in `coach/curriculum_state.json`:
after a show, a tip waits 20 h before it can show again anywhere. An accept waits 3 days, and each dismissal doubles
the wait (2, 4, 8 … days, capped at 30). At most 2 tips show per day. A text trigger counts for 2 user turns.
`COACH_TIPS=0` turns tips off.

Retiring: the hermes-coach miner (`coach_mine.py --write-adoption`, a daily no-agent cron on the coach Bot)
evaluates every `adopted` test over the digests from all hosts and writes `reports/adoption.json` into the data repo.
Adoption is sticky. The plugin reads `~/code/hermes-coach-data/reports/adoption.json` (override with
`COACH_DATA_DIR` or `COACH_ADOPTION`), and an adopted tip never shows again. The Mac's coach feed copies the private
overlay to `curriculum/<host>.yaml` in the data repo, so the Bot checks those entries too. Those adopted tests read
the digest's `features` (content-free counts from tool-call args, digest v4), `tools` and `signals`.

### prompt-coach, topic-shift and spin-out share the gate

Decision: they **share the rate limiter but are not folded in**. Their panels offer a real choice (New session /
Fork / Send here; Run new / Replace / Keep), and squeezing that into a one-button nudge would lose it. Each one calls
`window.__coach.claim(stored, rule)` before showing and `window.__coach.report(stored, rule, event)` afterwards.
Both calls fail open when coach isn't loaded.

- Only one bus panel is visible per session at a time.
- Their dismissals mute them for the session, and the weekly demotion applies to them too.
- Their shows use up coach's 15-turn budget. They aren't blocked by that budget, because they respond to a message you just sent.
- spin-out: only its automatic panel goes through the gate. ⇧⌘F always shows.

## Files

- `__init__.py`: the hooks (`pre_llm_call`, `post_tool_call`, `transform_tool_result`).
- `self_heal.py`: the complaint and self-admission detectors, fingerprints and shadow/live handling;
  `eval/selfheal_replay.py` replays them over a labeled set.
- `signals.py`: the machine-signal sweeper, forwarder and monitor router (stdlib, Python 3.9+).
- `agent_rules.py`: the agent-channel rules, plus the signals the user channel needs (subagents, long jobs, bug signatures).
- `core.py`: the file store under `<HERMES_HOME>/coach/` (`sessions/<id>.json`, `bugs.jsonl`, `log.jsonl`), the gate, the user-rule evaluation, the briefs (spin-out's transcript reader + aux task `coach`) and stats.
- `second_opinion.py`: the fix-loop review card and the unverified-diff review (settings, repo/diff detection, the
  neutral reviewer prompt); `eval/second_opinion_replay.py` counts how often each would fire over `state.db`.
- `curriculum.py` + `curriculum.yaml`: the tips (loading/overlay, live triggers, spacing, adoption read).
- `dashboard/plugin_api.py`: `/state`, `/offer`, `/event`, `/restart`, `/board`, `/file-bug`, `/stats`.
- `desktop-plugins/coach/plugin.js`: the panel. It checks after each finished turn, every 60 s while a turn runs, and on screenshot sends.

The brief model is set with `auxiliary.coach.provider` / `.model` (default: the auto aux model).

## Tests

```bash
cd plugins/coach
uv run --with pytest --with fastapi --with httpx python -m pytest -q -p no:cacheprovider tests
PATH=~/.nvm/versions/node/v20.20.2/bin:$PATH bash tests/desktop/run.sh   # desktop flow, mocked SDK + real spin-out
# miner adoption over real data (Hermes venv has ruamel.yaml):
~/.hermes/hermes-agent/venv/bin/python ../../skills/productivity/hermes-coach/scripts/coach_mine.py --data ~/code/hermes-coach-data --top 0 --write-adoption
```


### Bounded goal evidence

`goal_evidence.py` keeps the goal judge's **32,000 serialized-character** evidence
budget. It preserves human scope, candidate text, call arguments, pair IDs and
ordering. On overflow only tool-result and recalled-memory bodies become prefix
previews, with original character count, SHA-256, omitted/retained counts and an
explicit uncertainty flag. Re-budgeting cached observations preserves the original
digest. Both direct gate input and `capture`/`observe` paths use this budget;
`observe` no longer poisons the entire capture solely for a large result.

Textual `<memory-context>` markers are not authenticated provenance. Legacy rows
containing them remain UNKNOWN, even if balanced. Native core `pre_llm_call` now
passes a canonical inbound-content snapshot anchored to the current row's message
UID. Coach consumes that snapshot through normal plugin discovery; it does not
accept history's self-declared trust labels. Final-gate capture matches that UID,
so adapter preambles and multimodal memory parts cannot become a second user turn.

For historical string rows, the native `content`/`api_content` split supplies human
scope without copying the API-only recall into user evidence. Durable recovery
checks sidecar presence in SQL rather than loading large recall bodies. Native
replay preserves literal human-authored tags in canonical content. A local-only
core marker preserves uncertainty when the legacy replay sanitizer erased content;
those rows remain UNKNOWN. Historical multimodal rows lacking a native split are
also UNKNOWN; current-turn multimodal snapshots retain the full human payload.

Authenticated literal tags reach the judge verbatim, including their restrictions,
but still cannot support an abandonment hold: the prior conservative tag veto is
retained against mistaken semantic verdicts. Oversized genuine user rows remain
UNKNOWN; restrictions are never trimmed. This is a transport-provenance bridge,
not a new authorization source, a legacy migration, or proof of semantic relevance.

Partial cited evidence cannot certify remediation, capability/authorization
boundaries, or ownership. Any omitted body also prevents an abandonment hold,
including when the verdict does not cite it: an unseen denial or missing-access
boundary could change the decision. The guard emits `omitted_boundary_evidence`
rather than relying on the semantic judge to notice that risk. This conservative
path is a safety measure, NOT a solution to original-incident recall. A digest
identifies omitted bytes but cannot establish their meaning.

Limits remain: 200 input rows, 32,000 serialized evidence characters, and UNKNOWN
when full human scope/candidate/call arguments or required metadata do not fit.
This does not remove capture/recovery lineage limits, recover image pixels, or prove
original-incident semantic acceptance. It does not execute evidence tools.

Tests: `tests/test_goal_bounded_evidence.py` covers direct long-result input,
`capture`/`observe`, pairing/duplicate rejection, JSON escaping and cumulative
budgets, repeated budgeting, memory provenance, restriction retention, and rejected
closure proofs; `tests/test_goal_disposition.py` retains the gate's existing tests.

`tests/test_goal_omission_safety.py` exercises mistaken high-confidence abandoned
verdicts with cited/uncited omitted denial and missing-access evidence, plus
human-authored balanced memory tags. `tests/test_goal_omission_turn.py` covers
those refusal paths through the real discovered complete-turn gate.

`tests/test_goal_native_provenance.py` exercises normal discovered hooks in both
registration orders, native persistence/replay, read-only scope recovery, literal
human restrictions, oversized scope, self-declared trust labels and legacy
sanitation ambiguity. `tests/test_goal_native_capture.py` exercises adapter persist
overrides and current multimodal memory injection through complete turns. Provider
and semantic judge responses are controlled; these are transport/safety contracts,
not real-model semantic acceptance or live CLI/Desktop transport acceptance.

Legacy replay uncertainty survives compaction in native display metadata; scope recovery rejects it rather than treating sanitized text as complete authorization. This is a safety control, not recovery of missing permission.


`tests/test_goal_transport_controls.py` exercises actual stdio/WebSocket interrupt RPC,
WebSocket disconnect/reconnect during a turn, fresh-process persisted fallback hydration,
and interactive CLI PTY Ctrl-C. Together with `test_goal_disposition_transport.py`, all
13 transport cases pass with isolated homes and loopback-only provider fixtures. PTY
keypresses respect the native 50ms paste debounce; no input-handler bypass is used.
These do not cover Electron, production serve auth/startup, real-model semantics or
actual-profile activation. The original private incident replay remains unresolved.


### Electron and approval consistency acceptance

`tests/test_goal_approval_consistency.py` rejects pending-approval evidence that
simultaneously claims success, a zero exit, or a successful HTTP status. Its core
recheck test proves this cannot clear a confirmed hold. Exact literal action
binding alone is not semantic proof that the action is necessary for the requested
goal. After structural validation, `goal_relevance.py` independently judges the
operation's necessity from user/evidence scope, without the main verdict. It requires
the latest real user steering and operation-source citations, finite high confidence
and a supported dependency. Unrelated, uncertain, omitted or malformed evidence
becomes UNKNOWN; it cannot clear an existing hold or authorize bypass. Initial
UNKNOWN remains fail-open. This is semantic defense in depth, not deterministic
entailment, and adds one bounded auxiliary call for a structurally valid boundary.

`test_goal_boundary_relevance.py` and `test_goal_relevance_review.py` exercise the
real core recheck, latest versus stale user citations, timeout, synthetic scope,
omission, and genuine necessary boundary controls. The opt-in
`test_goal_boundary_relevance_live.py` checks actual-model necessity judgments;
these authored cases are not a blind holdout.

`tests/test_goal_electron_acceptance.py` drives a built Electron renderer under
Linux Xvfb through its composer and actual production serve/token-auth transport.
Its four cases cover initial rejection, composer Stop, Ctrl+Enter queueing, and
reconnection of the exact remote session socket. Each checks rejected-candidate
DOM observation and persistence, one shared session, and a second analysis-only
turn. Stop produces interrupted status; the other first turns produce unresolved
error status. The deterministic loopback model is not a live-model evaluation. Prepare a core worktree with its locked
Desktop dependencies, installed Electron binary and `npm run build --workspace
apps/desktop`; provide Node, `xvfb-run`, a C compiler and the same Python transport
dependencies used by `test_goal_serve_acceptance.py`. Run both files using that
core's `scripts/run_tests.sh`. Missing Electron prerequisites skip the optional
renderer test; a skipped run does not meet Electron acceptance.

The test seeds only sandbox-local connection settings, supplies the source Python
interpreter for ancillary local roster backends, and waits for both Backend ready
and restored remote route before submitting. `electron_loopback_fence.c` is inherited by test subprocesses to reject
non-loopback connect/sendto and signals to processes with a different HERMES_HOME.
`test_goal_electron_fence.py` verifies the foreign-process and network denials using
an owned disposable process. This is a regression-fixture guard, not a security
sandbox for hostile executables or direct syscalls. No production profile should be
used for these tests. Full app reload, backend restart and all-gateway socket loss
are not covered by the session-socket reconnect case. Production activation,
remote owner adapters and exact-original replay remain separate requirements.

`test_goal_process_ownership.py` verifies that cached process handles, notify flags
and forged consumer assertions cannot certify ownership or release a prior hold.
Those conservative UNKNOWN regressions are retained. The native local adapter in
`goal_process_ownership.py` accepts only exact `{gateway,profile,process_id}` routes,
full process handles, immutable canonical message-UID/profile/session spawn bindings,
a live local pipe Popen with matching kernel identity and reader, and a fresh native
process-completion consumer receipt. It compares the latest canonical stored human
row, not command text or cached ownership assertions. Guard reads never spawn, poll,
drain, adopt, signal, repair, or write state.

The deliberately narrow supported path requires an **actually admitted process
completion within 15 seconds** on the same live TUI/serve session, profile and registry
generation, or the native push-gateway path described below. An empty poll, Kanban receipt or a live thread does not qualify. Stop,
teardown, lost/released lease, disabled notifications, retirement, stale receipts and
late pre-Stop dispatch results reject ownership. This is point-in-time continuation,
not a promise of eventual success or delivery. One-shot, other unproved consumers, detached,
retained, sandbox, PTY, systemd-wrapper, WSL, persistent and handed-off processes
remain UNKNOWN. Steering/rotation does not rebind an old process to new scope.

`test_goal_process_adapter.py` composes real native spawns/queue consumption and an
isolated canonical state DB through the public gate (the admission result is substituted
in its focused fixture). `test_goal_process_serve.py` additionally runs unpatched
production serve and token-authenticated WebSocket requests with deterministic primary
and judge replies over loopback HTTP: two real terminal spawns, exact-command fixture
approval through the native request protocol, actual completion admission, positive
ownership at the discovered coach gate, and the owned process's subsequent completion.
No production configuration or external provider is used. It does not exercise Electron
or certify other process consumers.

Process-proof race checks re-read the canonical user row and require the same
consumer receipt/revocation version after the final running-process snapshot.
Stop, a new canonical request, or changed admission evidence observed during those
reads rejects ownership. This is bounded point-in-time evidence, not an atomic
transaction across process state, SQLite and the transport, or a future-delivery
guarantee. The native batch consumer retains refused/unknown/exceptional process
completion admissions for retry rather than relying on delegation-only release.
Tests: `test_goal_process_independent_review.py` exercises native Stop, new canonical
steering, and retry after false/unknown/exceptional admission; `test_goal_process_serve.py`
exercises actual isolated serve, approval protocol, spawn and completion admission.


### Native messaging-gateway process return proof

The next supported non-TUI path is the existing native push-gateway process watcher,
not CLI one-shot or the stateless API self-post. Its proof requires a live native
watcher for the exact running process and immutable request UID/home/session/route,
plus an actual `admit_internal_event` success for a process completion within 15 seconds.
Registration, a configured watcher, an empty poll, a watch-pattern event, and a handler
that merely returns without accepting never qualify. The native source and adapter,
running gateway loop, current in-memory session route, and notification configuration
must still match at readback. Reads never drain queues, load retained jobs or repair state.

Stop and new-user ingress (including a message queued behind a busy turn) permanently
revoke old watcher proof and forget the route's old spawn bindings. This does not kill
jobs or change their normal result delivery. Late admission and delayed watcher startup
cannot resurrect pre-boundary proof. Adapter ingress is conservative: even input later
rejected by runner authorization may remove proof; it cannot grant it. The public guard
still rechecks the exact latest canonical DB row and the receipt version after its final
process snapshot. TUI receipts cannot certify a gateway routing key.

Tests: core `tests/gateway/test_process_consumer_health.py` covers real busy-queue
admission, freshness, Stop/new-user/replacement/shutdown/task loss, late results, refused
admission, exact snapshot fields, bounded locks and nonconsuming readback. Mods
`test_goal_process_gateway.py` composes the public guard with an isolated SQLite store;
its native integration starts a real child, uses the terminal helper to arm the real
watcher on the gateway loop, observes actual exit/completion batching/native admission,
and validates another still-running process. Only the external transport is a no-I/O
adapter using the production base admission/queue. No model or external Telegram service
is exercised. Positive coverage is default-profile Linux native local pipe processes;
named-profile/multiplex transports are not certified by these tests. Cross-profile,
cross-gateway, recovered/adopted/detached, PTY, sandbox and wrapper paths remain unproved.
