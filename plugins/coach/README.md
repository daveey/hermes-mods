# coach

One nudge bus for Hermes, so coaching panels never pile up. It has two channels.

### Deleted SQLite sidecar monitor

`stranded_sqlite_watch.py` scans Linux procfs for retained deleted database WAL/SHM descriptors.
It excludes entire absent profile homes (including tombstoned profiles) as retired-reader cleanup,
not live-database restart incidents. Missing databases inside existing profiles still alert, as do live
board databases. Descriptor evidence alone does not establish corruption, read failures or POSIX lock loss;
new cards require ownership/read verification before an idle-only restart. No database files are changed.
Coverage: `tests/test_stranded_sqlite_retired.py` checks missing/tombstoned/live/live-with-missing-db profiles;
`tests/test_stranded_sqlite_watch.py` retains real child-process deleted-sidecar detection and mock-only filing.


## Traceback attribution

Bug detection decodes JSON tool envelopes before inspecting frame paths and exception tails.
Bundled Python under `tools/python-*/lib` is not Hermes source. For `execute_code`,
executor frames above the last `<cell>` frame do not establish a core defect; real Hermes
frames below that boundary still do. Successful cell output quoting a traceback is ignored.
Qualified exception names are retained instead of collapsing distinct failures to `traceback`.

Regression coverage: `tests/test_bug_traceback_attribution.py` exercises raw/dict/JSON/guidance-suffixed
results, three JSON parsing failure shapes, true core failures, and observer recording.
`tests/test_agent_channel.py` retains the existing raw traceback and infrastructure/refusal checks.
This changes defect attribution, not the execution or parsing of caller code.

## Agent channel (invisible to you)

The plugin watches the agent's own tool calls. When it spots a known anti-pattern, it attaches a one-line hint to the
tool result the model reads next: a `coach_hint` key on JSON results, otherwise a `[coach] …` line. A hint still
pending at the end of a turn rides the next turn's `pre_llm_call` context. Each rule fires once per key per session.

| Rule | Trigger | Hint |
|---|---|---|
| `repeat_failure` | the same (whitespace-normalized) command or code fails 3× | stop retrying, reproduce, form one hypothesis, test it (systematic-debugging) |
| `poll_loop` | `sleep` inside a `while/until/for` loop, or ≥3 sleep/poll calls in the last 10 | `terminal(background=true, notify=true)` or a Kanban card, then end the turn |
| `no_checkpoint` | 40 tool calls in one user turn | write a state summary before more tool calls |
| `live_checkout` | `write_file`/`patch` under the running Hermes checkout, or `git commit/pull/reset/…`, `sed -i`, `npm run pack` there (HEAD moves, repacks and non-ignored file writes are refused up front by the guard below) | use `hermes-core-wt new/ship` |
| `kanban_db_write` | a `terminal` command or `execute_code` script that names a `kanban*.db` and runs `UPDATE`/`INSERT`/`DELETE` on `tasks`, `task_runs` or `task_events` (reads never count). Once per session, on the same call (also checked in `transform_tool_result`) | use the `kanban_*` tools; a PR contract on a repo with no required checks completes with `kanban_complete` `metadata.contract_waiver`; a damaged board: `hermes kanban recover` |
| `widget_send` | a `write_file` (content) or `patch` (new_string) to an `.html`/`.htm`/`.xhtml` file that calls `hermes.send(` and either has a call whose result is never read (no `await`/`return` just before it, no `.then(` within 400 chars after) or uses `localStorage`/`sessionStorage`. `data-hermes-send` attributes alone never count. Once per file, on the same call (also checked in `transform_tool_result`) | the widget rules: 500 characters and one send per second, await the `{ok}` ack, retry when throttled, show other failures, chunk large data; no browser storage in the sandboxed frame, keep state in the widget's file |
| `plan_first` | the user's message is a new build ask (build/implement/extend/create… plus 25+ words, not a fix/bug report, not a "go"), the user has seen no reply in this session (no final reply, no answered `clarify`), and the agent makes its first project edit: `write_file`/`patch` outside scratch (`/tmp`, `$TMPDIR`, `~/.hermes/cache`, `~/.hermes/plans`; `COACH_SCRATCH_ROOTS` overrides), or a terminal `git commit` / `git worktree add` / `git apply` / `sed -i` / output redirect into a git repo, or the same `write_file`/`patch`/`terminal` helper calls inside `execute_code` (literal strings, string assignments, concatenation, f-strings, `os.path.join`/`Path(...)`/`/`, `expanduser`, `$TMPDIR` lookups, loops over string literals, imported aliases). A path with a dynamic tail is judged by its statically known prefix: `R + k` in a loop with `R` outside scratch fires, the same under `/tmp` or `$TMPDIR` does not. A path with no static prefix at all (a function's return value) is not inferred and stays quiet. Comments, code quoted as content and calls hidden behind user-defined functions do not count. Never for cron runs or subagents. Once per session | post the plan as the reply and end the turn for the user's go |

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

**Live-tree file writes are refused too.** `write_file`/`patch` (incl. V4A `*** Update File:` paths) on a path in
the live checkout, `execute_code` that writes a tree path (or hands `terminal()` such a command), and terminal writes
there: output redirects, `sed -i`/`perl -pi`, `cp/mv/install/rsync/ln` destinations, `tee/rm/truncate/touch`,
`patch`, `git stash/apply/rm/mv`, and Python heredocs / `python -c` scripts that have a write call and name a tree
path (relative paths resolve against the last `cd`/`workdir`/`os.chdir`). Why: backends lazily import from the tree,
so even a transient edit can load live, and `hermes update` stamps the version `.dirty` (2026-10-28: a heredoc
`open(p,'w').write(...)` on `hermes_cli/kanban_wake_check.py`, reverted a minute later). Gitignored paths (`logs/`,
`venv/`, build output) and reads (`read_file`, `cat`, `grep`, `sed -n`, `git log/status/diff`, `git stash list`,
`git apply --check`) pass. `node_modules/` at the tree root or one level down (`web/node_modules`) is gitignored but
refused: `cp/mv/rsync/ln` into it (terminal), Python `shutil.copytree`/write calls naming it (terminal heredoc and
`execute_code`), and `npm/pnpm/yarn/bun install|i|ci|add|update|remove|...` run with cwd (`cd`/`workdir`) or
`--prefix`/`-C`/`--dir` in the tree (`-g` passes). It is the live dependency install the backends and the
`hermes update` tail resolve from (2026-10-29: a hand-copied `@rolldown/plugin-babel` not in package-lock.json broke
the web typecheck and the update tail failed 6x); the refusal points to a worktree with its own `npm ci`. Escape hatch: `COACH_ALLOW_LIVE=1` at the start of the command or in the `execute_code`
source; for `write_file`/`patch`, in the process env. Tests: `tests/test_live_checkout_writes.py`.
**Compute envelope (`compute_envelope.py`; `pre_tool_call` on `kanban_block` + `pre_verify`).** The standing rule
(rent-machine skill, `~/AGENTS.md`): after the user's go on a job that needs a GPU, an agent may rent one single-GPU
machine up to $25 total without asking. A deterministic classifier (no model call) catches a `kanban_block`
(direct or through `tool_call`) or a final reply with a sentence that asks for a decision (approve, may I, want me
to, reply "…") about compute or money (GPU, rent, Vast/RunPod, outside Slurm, budget, $N), in a text about compute.
It stays silent when any total is above the envelope (hourly rates don't count), the machine is multi-GPU /
multi-node, or the sentence is about a credential (token-broker grant, SSH key, password, 2FA, sudo). The first hit
per session and surface holds the block / the turn once with a note to rent within budget and carry on (logged
`agent_hint` / `reply_nudge`, rule `compute_envelope`); a repeat goes through and is logged as `agent_fault`.
Origin: defect agent-962f3f10ff, where three blocks and an "approve $20 / use metta1 directly" menu left a ~$5-10
experiment blocked for 5 days; `tests/test_compute_envelope.py` replays both verbatim. Env:
`COACH_COMPUTE_ENVELOPE=0` turns it off, `COACH_COMPUTE_ENVELOPE_USD` changes the cap (default 25).
**Compute authority (`compute_envelope.py`; same hook, rule `compute_authority`).**
The other side of the envelope holds agent-declared GPU/rental caps above $25 or multi-GPU plans once,
including `kanban_create.body`, direct/deferred `kanban_block`, the reply's `pre_final_gate` and its
`pre_verify` backstop. User-only conversation scope is recovered with `goal_scope.recover_scope` (native
capture and verified compression ancestry); assistant/tool claims, dispatched briefs and unknown/remote
origin evidence do not grant an exemption. Matching user dollars or explicit multi-GPU permission exempt
the declaration. A bare yes ("approved", "yes", "go ahead") to a priced ask exempts up to the figure
that ask named (`answered_asks`): the ask must be an asking sentence ("Approve or decline $36 total ...",
"May I rent ... up to $40?") in the assistant text the reply directly answers (tool rows skipped, another
user row breaks the pair). Declarations ("I will rent with a $3,000 cap"), the assistant's own or retold
"approved", an unpriced question in the same message, conditions ("approved, but ...") and runtime rows
inherit nothing; "Approve $50 or $100?" + yes credits $50. Only typed user text counts anywhere: notice
and relay rows (`[chat-closeout] ...`, `⏸ [hub:default] ...`) never grant dollars, even when they quote
the ask. Questions, denials and injected task/skill wrappers are not approval; teardown caveats
in a separate clause do not erase it. Genuine approval requests and historical spend reports pass.
The nudge quotes the standing single-GPU/$25 rule and directs calibration inside it, then a blocked card
asking for a number. A repeat logs `agent_fault` and passes: this is deterministic coaching, not a hard
purchase or budget authorization boundary. Ambiguous language can still require agent judgment.
Tests: `tests/test_compute_authority.py` covers all three surfaces, deferred creation, the reply backstop,
$215 approval, $20 caps, past spend, provenance, numeric/word GPU counts, one-shot behavior, and the
answered-ask flow with its negatives;
`tests/test_compute_envelope.py` preserves the original under-use behavior.

**Block-time handoff (`pre_tool_call` on `kanban_block` / `kanban_create` / `tool_call`).** A Kanban ask to the user
ends the worker's run before the reply check below ever runs. So a `kanban_block` with kind `needs_input`/`capability`
or a `human_request`, or a `kanban_create` with a `human_request` (direct or through `tool_call`), is judged with the
reply judge's `handoff` rule (same threshold), using the block text and the turn's tool log. On a hit it is held ONCE
per session and card with a nudge to try it first (terminal fetch of a private URL, the public site's tab, vault,
token-broker); the same block again goes through, so it can't loop. Logged `agent_block` / `block_checked` /
`block_handoff_repeat` (rule `block_handoff`). Judge errors fail open. Origin: defect agent-19492bff76, a worker that
asked David to open a tailnet URL and click Create after one refused browser call. Off: `COACH_BLOCK_TIME=0` or
`plugins.entries.coach.settings.block_time: false`; it also follows `COACH_REPLY_RULES` (no `handoff`, no check).
Tests: `tests/test_block_handoff.py`.

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
| `report_only` | the reply describes or diagnoses a problem the agent could fix and ends there: no fix, no started fix (card, worker or PR this turn) and no concrete proposal ("Not fixed. No action is needed from you.", "relayed to the existing owner", an "Open:" list). An explicit scope limit in the user's ask ("analysis only", "just tell me why", "plan first", "don't change anything", "no edits", "Read-only:", "Diagnosis only.", "do not change any cards") drops it before the judge runs. The limit is read in the user's whole request (up to 20000 chars, preloaded-skill blocks dropped; for a Kanban worker only the card's title, `## Body` and standing instructions, not other cards' summaries), so a "Diagnosis only." at the end of a long card body counts; the judge prompts carry the request's head plus the body's last 500 chars. Two-stage: the Sonnet screen at 0.75 (0.60 on Desktop human turns) hands the reply to a second opinion on claude-opus-5-5 (aux task `coach_report_only`, real tool arguments); only the second opinion nudges (see below). Never for subagents (platform `subagent` or state.db `sessions.source='subagent'`): their reply is a report to the parent agent, whose own reply is judged; same for `status_only`, and a subagent's nudge outcome files no self-heal defect (`nudge_outcome_subagent` in `log.jsonl`; `tests/test_subagent_scope.py`, agent-ee1b1162bc) | fix it now if small or reversible; otherwise start it (`kanban_create` with acceptance criteria, linked to the chat) and name the card; if it really is the user's call, propose the exact fix |
| `unverified` (off by default) | "done/fixed" with no check on the user's real path | verify on the user's path or say what's unchecked. With `diff_review` on it is also judged on turns that changed a git repo, and a hit there runs an Opus review of the diff (see Second opinions) |
| `status_only` (deterministic, no AI call) | the user asks about progress or to finish ("why are we waiting", "what are you doing", "whats going on", "can you just finish it", "make the corrections") and this turn's tool calls were only card bookkeeping (`kanban_show`/`list`/`comment`/`unblock`, `cross_task`/`cross_session`, `hermes kanban ...`/`hermes-hub request ...` in a terminal) plus reads, and the reply's last 1,500 chars name no wall only the user can clear (billing, a sign-in, "only you") | do the next real step on the work now (edit, test, commit, merge, deploy, prove), or name the one specific wall; don't reopen, re-review or add another watcher card |
| `denied_ask` (deterministic, no AI call) | the reply says nothing needs the user ("no new approval request", "nothing needs you", "no action required on your side"; code and quotes ignored) while a card linked to the chat (core `session_tasks`: created, shepherded, subscribed or remote-origin) is blocked/triage with a validated `human_request` | correct the reply: say in plain words what is stuck, the one thing the user must do and where (the nudge quotes each card's `human_request.action`), and what happens after; do the step if an agent can |
| `credit_ask` (deterministic, no AI call) | the reply asks the user to add or buy API credit (an Anthropic/OpenAI billing link, "add about $250", "add credits", "money ask") and this turn never ran the pool helper (`hermes-pool-llm` / `pool_llm` in the tool log) | run the work on the Hermes subscription account pool first (`hermes-pool-llm ask` or `serve`); ask for money only if the pool fails too, quoting its error |
| `laptop_job` (deterministic, no AI call) | one sentence of the reply proposes a recurring unattended job (cron, launchd, "every 10 hours", hourly, a refresher/watcher/poller) on the MacBook, MBP, laptop or mini, with no hub target and no Mac-only reason in that sentence | put it on hermes-hub; if the hub lacks a credential, plan the hub job plus a one-time grant card |

`laptop_job` (defect agent-60ca102b39, 2026-10-29): a chat offered "a small job on the MacBook every 10 hours" to rotate a Slack token because only the MacBook could write the secret that day; David: the MacBook doesn't stay on. It runs right after `credit_ask` and holds the reply once (attempt 0). A sentence is held only when it has all of: a creation verb or offer (build, set up, add, create, install, schedule, deploy, put, move; "plan:", "want me to", "shall I"), a recurring-job word, and a Mac as the job's location ("on/from the MacBook", "on the mini", "on your Mac", or "MacBook cron/job/watcher"; a bare "mini" is a model name such as gpt-4o-mini). A 30-day backtest over the hub's 16,105 replies (2026-10-30) holds exactly one: msg 381513. It passes when the same sentence names the hub (hermes-hub, "the hub", "hub cron", worker-N), says the job is absent ("add no LaunchAgent, cron or MacBook job"), or a Mac-only reason (Keychain, Chrome/browser, Apple apps, iCloud, Xcode, osascript, Screen Sharing, per-host, local state, Hermes Desktop, macOS-only, TCC, hermes-sync, disk). Code spans, `>` quotes, double-quoted text and sentences naming `laptop_job` are ignored, so reports about the rule and quotes of the old reply are not held. `COACH_REPLY_RULES` without it turns it off. Tests: `tests/test_laptop_job.py`.

`denied_ask` (defect agent-8cecfe01cb, 2026-10-28): a chat told the user "no new approval request" while its setup card had asked him for a GitHub passkey; a todo card waiting on it kept the chat's batch unsettled, so nothing reported the ask for 18 h. It runs first (before the owner and status checks), reads the board only when the reply contains a denial phrase, fails open on any read error, and holds the reply once (attempt 0). A reply that already tells the ask (names the card id or title, or quotes most of the action) is not a denial, so "the other jobs don't need you" after presenting the ask passes. `COACH_REPLY_RULES` without it turns it off. The chat-closeout ask relay fixes the silence itself. An ask whose card this turn's own call took off the user without an `<- ERROR` (kanban unblock/complete/archive/schedule/move as a tool, a deferred `tool_call`, the `hermes kanban` CLI incl. over ssh, or a `cross_task` action; short and long ids match) no longer counts, even when the board copy read (a hub replica on another host lags ~60 s) still says blocked (defect agent-5c9b041010). The `reply_nudge` and `reply_nudge_outcome` records carry `asks` (the card ids the check saw), so a false hit names its card. Tests: `tests/test_reply_check.py` (`*denied_ask*`, `test_reply_check_denies_ask_while_card_human_blocked`).

`credit_ask` (defect agent-fe419d4901, 2026-10-29): an eval chat asked David for ~$250 each of Anthropic and OpenAI API credit and parked 8 h, while the subscription pool could have run it. It runs before the owner/status checks and holds the reply once (attempt 0); the `pool-llm` plugin holds the matching `kanban_block` once. Only this turn's tool log counts as having tried the pool. `COACH_REPLY_RULES` without it turns it off. A sentence saying credit is not needed is not an ask (live false positive 2026-10-29: "So no credit top-up is needed."). Only explicit not-needed forms are skipped: "no credit (top-up) is needed", "no need to buy API credits", "don't need to add credits", "a top-up isn't needed". So "No credits left, so a top-up is needed" and "No need to wait; add $250 of credit" are still held. Only LLM credit counts, since the pool can't pay other bills (live false positive 2026-10-29: "raise the spending limit" for the GitHub Actions billing watchdog). The ask's sentence, then its paragraph (never other paragraphs), decides, and the billing target nearest the ask wins: LLM credit wording (an LLM provider next to credit/billing/quota, e.g. "Anthropic credit", "credit on OpenAI", or `insufficient_quota`, "credit balance is too low", "API credit") holds it; non-LLM billing wording (GitHub Actions, Actions minutes, "AWS budget", "credit to the Vast.ai account", "Cloudflare balance"...) lets it through. A bare name is not billing context: "Pushed to GitHub. Please add $250 of credit" is held, and "Claude Code couldn't start; raise the spending limit for Actions" is not. A bare "add $250 of credit" with neither named is still held. With no billing context, only strong money wording counts: credit(s), a $ amount, a top-up, or a billing URL. Bare budget/balance/funds/billing/spend limit need LLM billing context ("raise the Anthropic budget"), so engineering budgets ("raise the retry budget", "load on the tmpfs and memory budget", "the 30 s budget") pass (defect agent-ddabb16633, 2026-10-30). The noun-first form counts only as a stated need ("credit needs to be added", "a top-up is needed"). Denials are skipped: "no credit or purchase ask", "not a credit ask", "no purchase", "I'm not asking you to buy API credit", "I didn't ask you to buy credit". So are another actor's reported asks ("the eval chat asked you to add $250", "a card asking you to buy credit") and attribution ("a line to get credit"). "I asked you to..." still holds. Backtest a change against real replies with `python3 reply_check.py backtest credit_ask --days 14`, which lists every state.db reply and held draft the rule would hold. `tests/fixtures/credit_ask_corpus.json` has every hit from 14 days of hub replies, labelled ask/not_ask. The test requires every ask to fire and no not_ask to fire except the listed `residual` ones (3: credit used as a verb, a tool that "can buy credits", "treat it as a money ask"). Tests: `tests/test_credit_ask.py`.

`status_only` (defect agent-38a817308d, 2026-10-28): four replies in one chat said "reopened the card / verified
**ready**" to "why are we waiting for 8h?" and similar asks while the chat's rollout card sat unstarted for 4 days, until
the user wrote "can you just finish it please?". The report_only owner check counts exactly that bookkeeping
(`kanban_show`, `cross_task`) as acting, and the judge scored none of the four. Any other tool call (patch, write_file,
a git commit, a test run, `kanban_create`, `delegate_task`) counts as work and keeps it quiet, as does an explicit scope
limit. It runs before the judge, logs `reply_nudge` with `rule=status_only`, and `COACH_REPLY_RULES` without it turns
it off. Tests: `tests/test_status_only.py` (the four real asks and replies; their tool logs are rebuilt from what each
reply says it did, because the coach digest keeps replies but not tool logs). Hermes-written prompts are never the
user asking now (`automation_prompt`): notice envelopes ([Kanban close-out], [chat-closeout], owner-wake ...) and the
dispatcher's Kanban worker prompt (`# Kanban task <id>: ...`, re-sent unchanged on every wake), whose card body quoting
"unblock the task and finish it" held a shepherd that had parked correctly (agent-e80aa5e107). report_only and the
judge still review those turns; `test_notice_turn_recognizes_every_prompt_producer_text` renders core's real header.

Measured on 360 real replies against hindsight labels (the labeler also saw the user's next message). At
threshold 0.7: `handoff` precision 0.91, recall 0.33; `confirm` precision 1.0 on 4 fires, recall 0.11. Held-out
samples are small (n=120), so treat these numbers as early. `unverified` showed no usable signal.
`unwatched` checks wake sources deterministically before the judge runs, so a reply with a live watcher costs no LLM call. Of 3,260 recent Desktop replies, 299 matched the prefilter. On three real cases the judge scored the orphaned "once the runs finish, I'll…" reply 0.85 and scored the user-owned next step and the reply that armed a watcher below threshold. Watchers die when their backend restarts, so a promise made before a restart can become orphaned later; the rule only catches this at turn end.
`report_only` cascade (2026-10-27). Gold set: 1,190 human-question reply endings from the MacBook (Oct 1 to Oct 27, 762-row
labeled set plus 716 newly sampled rows) and the hub (96 rows); 175 positives. Labels: two blind labelers (Sonnet and
Opus, same rubric, counts-only tool log) where they agree, and claude-opus-5-5 with the real tool arguments deciding the
91 disputed rows. Gold is mostly Opus-defined, so the numbers below are agreement with that rubric, not human truth.
Split 70/30 by session hash; the operating point was chosen on train only, held-out scored once
(`eval/report_only_cascade_eval.py`; files in `~/.hermes/kanban/evidence/report-only/v2/`). The prefilter keeps 81% of
the gold positives, which caps recall. The prefilter also passes leftovers named by likeness ("O-7 through O-11 have
the same ... problem", "are also affected") and workarounds handed to the user ("if it holds one, pressing 'Post now'
will work", "you can click Retry to ..."); added for bot-dc58243931, they change no row of the 762-row MacBook set
(identical candidates, so identical judge precision) and replays of that turn through `pre_verify` nudged 4 of 5. Production path: prefilter, scope-limit guard, Sonnet `coach_reply` screen; when
the screen's report_only p >= 0.75 (`CONFIRM_FLOOR`; 0.60, `HUMAN_CONFIRM_FLOOR`, for Desktop turns that are not notices: held-out on typed Desktop asks P 0.93 / R 0.46 vs 0.94 / 0.27 at 0.75; Kanban, CLI, TUI and subagent turns keep 0.75) one call to `coach_report_only` (claude-opus-5-5, labeling rubric
plus the real args of kanban_create, delegate_task, patch and terminal calls) labels the outcome; a report_only +
agent_fixable label is p 0.9 and nudges (`report_only=0.5`). Capped at 250 confirm calls per host per day (events
`report_only_confirm` and `report_only_confirm_capped` in `log.jsonl`; `COACH_REPORT_ONLY_CONFIRM_CAP`, set
`COACH_REPORT_ONLY_CONFIRM=0` to turn it off); with no confirm wired the screen alone must reach
`report_only_screen=0.85`. When a confirm was due but unavailable (cap hit, error) the reply is NOT nudged: the
audit row says `passing: false, suppressed: "unconfirmed"` (agent-f668487724: past the cap the screen agreed with the
second opinion on only 39 of 68 hits, and each false nudge became a self-heal card). `COACH_REPORT_ONLY_FALLBACK=nudge`
restores the old screen fallback, held to `report_only_capped=0.92`. Since that leaves a capped day unchecked, any
`report_only_confirm_capped` row makes `signals.confirm_cap_check` file one self-heal card per day (later days comment
on it). A `kanban_block(kind=capability, needs=[...])` with no `human_request` opens an agent owner card and is not
counted as a human ask (`human_block`).
Held-out human turns (59 positives; precision / recall, 95% Wilson CI): cascade at screen floor 0.75: 0.957 (0.79-0.99) /
0.373 (0.26-0.50); Sonnet screen alone at 0.75: 0.647 / 0.373, at 0.85: 0.562 / 0.153. Train (116 positives), cascade at
0.75: 0.811 (0.66-0.91) / 0.259 (0.19-0.35); floor 0.5: 0.679 / 0.474. Pooled train + held-out (175 positives): floor 0.75
0.867 (0.76-0.93) / 0.297 (0.23-0.37); floor 0.7 0.836 / 0.320; floor 0.5 0.750 / 0.514; screen alone at 0.75 0.594 /
0.326. Held-out was much kinder than train (floor 0.5: 0.897 / 0.593 vs 0.679 / 0.474), so trust the pooled figures.
Acceptance (P >= 0.80, best recall, aiming at R >= 0.55): precision is met at floor 0.75; the 0.55 recall aim is NOT
(0.37 held-out, 0.30 pooled). Lowering the floor to 0.5 buys recall (pooled 0.51) at precision 0.75, below the 0.80
bar, so it was not shipped. Misses are backstopped by the Opus default chat model, the weekly report_only KPI and the
Oct 30 re-measure; the live check stays with t_01a17f79ade9cbdd. Event turns were not re-scored in this pass.
Earlier passes (same day): the original target (precision >= 0.85, recall >= 0.6 on the 472 human rows of the first set)
was not reached by a single judge; Sonnet 0.67 / 0.43, Opus 0.82 / 0.39 at 0.75 on original labels, and the prompt rule
about named gaps in finished work moved Opus to 0.69 / 0.47 at 0.75 on consensus labels. Eval spend was not metered per call: the labeling passes alone used 1.17M input tokens (0.51M Sonnet, 0.66M Opus), the
cascade eval added one Sonnet screen call per candidate and one Opus call per screen p >= floor, so no dollar figure is claimed.
Notice-turn audit (2026-10-29, PT): 229 automated rows were traced to actual stored messages. Excluding 24
non-final/interrupted rows leaves 205 final replies and 22 labelled report-only positives (7 of the original 29
slip texts were not final replies). All 22 positives pass the regex; the screen is the bottleneck, not System One
(which only screens handoff/confirm). Historical `reply_checked` events did not retain judge probabilities, so a
missing confirmation cannot distinguish a below-floor score from an unlogged judge error.

The confirmation prompt now recognizes anchored completion/closeout/recovery/owner-wake envelopes, retains both
ends of long handoffs, and distinguishes each unattended leftover, unverified owner and live-check gap. Human
confirmation prompts remain byte-identical. The production screen floor stays **0.75**: lowering it fails the
precision gate on train. Full replay uses subscription-pool Sonnet/Opus calls, notice text as the ask, and reconstructed
real tool arguments. These are offline model scores, not a simulation of daily caps, hook deadlines or nudge delivery.

| Automated replay, pooled 205 finals / 22 positives | Baseline precision / recall | Notice-context precision / recall |
|---|---|---|
| Screen floor 0.75 (shipped) | 0.000 / 0.000 (0 TP, 1 FP) | 0.000 / 0.000 (0 TP, 1 FP) |
| Screen floor 0.60 (not shipped) | 0.583 / 0.318 | 0.533 / 0.364 |
| Screen floor 0.50 (not shipped) | 0.474 / 0.409 | 0.440 / 0.500 |
| Confirm every candidate (not shipped) | 0.188 / 0.591 | 0.220 / 0.909 |

On train (137 rows / 15 positives), notice context at floor 0.50 gives P 0.529 / R 0.600; confirming every candidate
gives P 0.226 / R 0.933. Neither reaches P 0.85. No threshold relaxation ships. This is an observability/context
correction, **not a claim that automated recall improved at the deployed operating point**. The requested automated
P >= 0.85 / R >= 0.60 targets remain unmet. Cached regression of production gates on the original held-out human
set (357 rows / 59 positives) stays 22 TP / 1 FP, P 0.957 / R 0.373; all 357 human prompts are byte-identical.
No fresh human model calls were needed; the original human recall target remains unmet, not newly regressed.

`report_only_scored` now records `screen_p`, `final_p`, `floor`, `threshold`, `confirm_attempted`, `source` and `passing`,
plus session, turn number and notice flag, even for below-threshold verdicts. It records no ask/reply/evidence text.
Telemetry fails open and adds no model call. It covers parsed judge decisions, not early deterministic/guard returns,
System One skips or judge failures. Separate host logs still govern whether a hook timed out or a nudge reached the
agent: a nudge event is not delivery proof. Tests: `tests/test_report_only_notices.py` (eight synthetic cases: anchored
recognition, long-notice instruction retention, human prompt invariance, low-score and confirmation/fallback telemetry,
scope limits, telemetry failure, real hook wiring). Together with `test_reply_check.py`, `test_report_only_drafts.py`,
`test_system_one_screen.py`, and `test_status_only.py`: **83 passed** under the Hermes venv. The existing scope fixtures
remain unfired.

Unverified owner (deterministic, runs before the screen and judge, no AI call): when the reply's last 1,500 chars hand
the problem to another owner ("I'm leaving them to that session", "not from my change", "another session is landing
it", "the owner has it", "that's that card's to fix") outside a quotation, and this turn's tool log has no check of or
message to an owner (`cross_session`, `cross_task`, `kanban_show`/`comment`/`create`/`list`, `delegate_task`, a process
poll, or `hermes kanban ... show|comment` / `hermes sessions` in a terminal call), report_only fires with
`reason=unverified_owner` (logged on the `reply_nudge` event) and the nudge says: rerun once, report green, or fix it /
look the owner up and send it the exact failure. Built from a hub turn (2026-10-27) that handed 6 red tests to "that
session" without checking it; the judge scored that reply 0.60 and 0.75 against a 0.75 bar. Fixtures:
`tests/fixtures/report_only_unverified_owner.json`; `eval/report_only_eval.py --no-judge` scores the check alone
(762-row labeled set: 1 fire, a gold positive, 0 held-out fires; 30 days of hub replies: 1 fire, the incident). The
explicit scope-limit guard and `COACH_REPLY_RULES` without `report_only` turn it off.

**Previous turn and honored deferrals (agent-1f34642d7f).** A relay asked a chat to wrap up; its no-tool reply summed
up the card reads and withdrawal it had made one turn earlier (6 s before) and named the user's own decision to defer
enforcement. Both judges saw only this turn's empty tool log, so the second opinion quoted the deferral as a leftover
and the revision's outcome check failed on "no tool calls". Now, when this turn made fewer than 2 tool calls and began
at most 10 min after the previous reply (`PREV_TURN_*` in `reply_check.py`; `AgentCoach.prev_turn`, recorded at
`turn_end`), the screen, the second opinion and the nudge outcome see a `PREVIOUS TURN TOOL LOG` section (that turn's
real-argument log, at most 2,000 chars, plus the first 800 chars of its reply). Both rubrics score an item the user
chose to defer, rescope or skip LOW / not agent-fixable only when the reply names the decision and a shown tool log
records it; an agent's own choice to wait, a schedule, a bare "the user deferred it", or a latest ask to finish it does
not count. Tests: `tests/test_reply_check.py` (section on/off by gap and tool count, prompt wiring through `check`,
`nudge_outcome` and the live hooks), fixtures `tests/fixtures/report_only_honored_deferral.json` (1 negative,
2 positives). Live replay of the incident draft: second opinion 0.9 → 0.1 (3/3), outcome 0.75 → ≤0.4; old vs new on
11 fixtures + 32 recent confirmed drafts: recall 1.0 both, precision within run-to-run noise.

**Owner checks show their result (agent-9516de5af8).** A relay said a fix card was in progress; the agent's
`hermes kanban show --json` found it running with a heartbeat 48 s old, and the reply said so. Both judges held it
(second opinion 0.9, outcome 0.95: "no successful owner-check result") because `AgentCoach.observe` logged only failed
results. After a successful owner read (`kanban_show`, `cross_task`, `hermes kanban ... show`; direct or through
`tool_call`) the turn log and the real-argument log now carry one line, at most 200 chars:
`   <- OK task=<id> status=<task status> run=<latest run status> last=<newest event> @ <UTC> (<age> ago)` (unparseable
output: the first 160 chars). The `<- ERROR` readers ignore it. Both rubrics count a hand-off as started when that line
shows the named owner running or claimed with activity in the last 24 h (or it was created this turn); a line showing
it ready/todo/blocked/done or idle longer, or no line, stays report_only. Tests: `tests/test_report_only_owner_verified.py`;
fixture `owner-checked-running-20261031` in `tests/fixtures/report_only_unverified_owner.json`. Live replay of the
incident through the real aux judges: second opinion 0.9 → 0.1, outcome 0.95 → 0.02; the same turn with the card
`ready`, its run crashed and 4 days idle stays held (0.9 / 0.99).

**Closed cards (agent-a806f203ab).** A Kanban worker called `kanban_complete`, then an operator note reopened the work
in the same turn; it pushed a fix and ended with CI unwatched, a proof never run and a done card named as "owner".
Three changes: (1) when this turn's tool log shows a `kanban_complete` that did not error (tool, deferred `tool_call`
or `hermes kanban complete`), report_only nudges carry the closed-card text (`REPORT_ONLY_CARD_CLOSED`: nothing wakes a
closed card, finish now with a foreground `wait_for_ci.py --timeout` and the proof, or `kanban_create` a follow-up and
name it); the `reply_nudge` event logs `variant=card_closed`. (2) "owned by / belongs to / lives on card t_…" and
"owned by card/task/session" count as owner handoffs for `unverified_owner`. (3) `closed_owner`: a card named as
owner ("owned by card t_…", "card t_… owns it", "tracked on card t_…") that is `done`/`archived` on this host's boards
(read-only lookup) fires report_only with `reason=closed_owner`, even after a `kanban_show`. Cards on another
gateway's board are unknown and never fire.
Six explicit scope-limit fixtures (`tests/fixtures/report_only_scope_limits.json`) never fire; the deterministic
`scope_limited()` guard drops them and the judge alone scores them <= 0.1.
Scheduled-wait exemption (deterministic, no AI call): a pure status question ("is this session done? it was green but
not anymore", "what's left?", "status?", with no action verb such as "fix/finish it", "can you", "please") answered
by a reply whose last 1,500 chars name a timed or scheduled wait (scheduled, parked, wakes at/on, retention, soak
window, cooldown, by design) and NO failure-shaped word (fail, error, broken, timing out, stuck, stale, blocked,
not merged...) drops report_only and status_only before the judge; `reply_checked` logs `guard=scheduled_wait`. The
judge and second-opinion prompts also score such a legitimate wait low. Built from live audit t_01a17f79ade9cbdd
(a nudge on a scheduled retention card sent the agent into a needless investigation); fixtures in
`tests/fixtures/report_only_scheduled_wait.json`, where the failed-soak "Desktop app is still timing out" and
failing-tests-left-in-the-queue positives keep firing. Thresholds were not retuned.
Judged drafts (`drafts.py`): every `reply_nudge` and `report_only_confirm` event carries a `draft_id`; the judged
reply and the user's ask behind it are in `<HERMES_HOME>/coach/drafts.jsonl` (one record per judged turn, shared by
both events), so a nudge stays auditable after later turns overwrite the session's last reply. Only the ask (1,500
chars) and the reply (6,000 chars) are kept, never tool calls or outputs, both through core's
`redact_sensitive_text(force=True)` plus a local pattern fallback. Capped at 2 MB and 30 days
(`COACH_DRAFTS_MAX_BYTES`, `COACH_DRAFTS_MAX_AGE_DAYS`; oldest dropped first); `COACH_DRAFTS=off` disables it. Read
one back: `drafts.Drafts(<coach dir>).for_event(<log record>)`, or `grep <draft_id> ~/.hermes/coach/drafts.jsonl`.
Settings: `COACH_REPLY_RULES` (comma list or `none`), `COACH_REPLY_THRESHOLD` and `COACH_REPLY_THRESHOLDS`
(`rule=th,...` per-rule overrides; defaults `report_only=0.5` for the second opinion, `report_only_screen=0.85`, `report_only_capped=0.92`). Needs core
`agent.pre_verify_every_turn: true` (fork commit `4c93d9c632`), because `pre_verify` otherwise fires only after
file edits. Nudges are logged as `reply_nudge` in `log.jsonl`; a turn the check ran on without a hit logs `reply_checked` (with the prefilter candidates). A turn it did not run on logs `reply_skipped` with a `reason`: `turn_failed` (core's "This turn did not complete" / "Your request was not processed" reply, so the host never called `pre_verify`), `pre_verify_not_called`, `empty_reply`, `no_session`, `rules_off` or `error:<type>` (cron turns are never checked and never logged). A nudge whose outcome re-check could not run logs `reply_nudge_outcome_skipped` (`turn_changed`, `error:<type>`). `report_only_scored` carries the turn's `platform`.
Fork turns (background review, side questions) are never judged: core skips `pre_verify` when the agent has
`_turn_origin` set (fork commit "skip the pre_verify user-reply gate for fork turns"), and `pre_verify` /
`envelope_gate` return early on a `turn_origin` kwarg without touching the session's pending nudge
(`tests/test_fork_turns.py`, agent-0fe2ee028b).

Latency budget (`reply_check.Budget`). The host runs `pre_verify` on a worker it abandons at a deadline, so the whole
check runs inside one explicit budget: `COACH_PRE_VERIFY_BUDGET_S` (default 75s) and the coach declares host timeout
= budget + `COACH_PRE_VERIFY_SLACK_S` (default 10s, so 85s) via `pre_verify.hermes_hook_timeout`. Every model call
(judge, cap 60s; report_only confirm, cap 90s; diff review, `diff_review_timeout_s`) gets `min(cap, time left -
COACH_PRE_VERIFY_MARGIN_S)` (2s) and is not started with less than `COACH_PRE_VERIFY_MIN_CALL_S` (5s) left (no
verdict, fail-open). The deadline is the host's own (`hermes_cli.plugins.current_hook_call()`, fork commit
`f5271c08c0`); on an older core that ignores per-callback budgets it is the global `plugins.hook_callback_timeout`
(30s) from the call's start, so the check still fits. Delivery is recorded, not assumed: `reply_nudge` is logged only
when the nudge is returned before the deadline (with `elapsed_s`, `budget_s`, `host_timeout_s`);
`reply_nudge_abandoned` when the host already gave up (result discarded, no continuation); `reply_nudge_delivered`
once the continuation reaches `pre_verify` again in the same turn (attempt >= 1, or attempt 0 on a different reply:
core restarts its verify attempts after a mid-turn compaction); `reply_nudge_undelivered` (with `reason`) when the turn
ends without that check (`turn_failed`, `turn_ended`, `empty_reply`) or a later turn starts first (`next_turn`: a
notice or the user superseded the continuation). Dispatch-level tests: `tests/test_pre_verify_deadline.py`.

Every `reply_nudge` (any rule, compute_envelope/compute_authority included) carries a `nudge_id`, and ends with
exactly one delivery record (`reply_nudge_delivered` | `_undelivered` | `_abandoned`) and exactly one outcome record
(`reply_nudge_outcome` | `nudge_outcome_lost` | `nudge_outcome_untracked` for rules no judge re-checks |
`nudge_outcome_subagent` | `nudge_outcome_missing`), each carrying that `nudge_id`. A session can hold several open
nudges at once. A plugin reload (hermes-sync `mods-reload` re-runs `register()` in running gateways/serves) keeps
them: `register()` hands the process's live per-session coach state (turn counter, ask, tool log, open nudges,
outcome slot, once-per-session sets) to the new build, one entry per coach home, and logs `coach_state_adopted`
(`sessions`, `open_nudges`), so a nudge whose continuation runs on the new build is still delivered and judged
(`tests/test_nudge_reload.py`). Reloads into this version from an older `__init__.py` start fresh once: the old build
never published its state. Nudges whose backend never checks the session again (process exit) are closed by
`signals.collect_coach`: at `HANDOFF_GRACE_S` when the turn provably died, else after `NUDGE_OPEN_S` (3 h) with
`reason: never_rechecked`; a late in-process close after that logs `reply_nudge_record_late` instead of a second
record. Tests: `tests/test_nudge_records.py`.

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
answer. Switching off takes effect on the next fresh check; reload also removes buffering.
An already held reply is never accepted merely because configuration disappears,
becomes invalid/unreadable, or changes to off/shadow: its recheck is unavailable and
core retains the explicit unresolved fallback (Stop still wins). A runtime
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
and candidate; a newer readback invalidates the older citation. The bounded native terminal
HTTP-availability contract below also consumes actual exit/output envelopes. Non-URL verification and other
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

### Stalled goals (`pre_llm_call`, `stalled_goal.py`)

A chat's card graph can look busy while the card that delivers the user's result never moves: watcher, review and
recovery cards spawn, comment and reschedule, and every supervisor checks that an owner is *alive*, not that the
delivery card *moved*. Defect agent-38a817308d: the rollout card of a 5-card graph sat in `todo` for 4 days
(Oct 23 to 28) while the chat's other cards started 168 runs.

`stalled_goal.py` reads the board read-only. A chat's graph is every card with an unreleased chat link to the session
(any `task_chat_links` kind) plus everything downstream of them (`task_links` children; not parents or siblings,
because boards share parents and an undirected walk merges unrelated graphs). A delivery card is an open sink of
that graph with at least one parent. It is stalled when its status is `todo`/`ready`/`triage`/`blocked`/`review`
(not running, not `scheduled`, not a human wait: `block_kind` `needs_input`/`time`, or `blocked` with a
`human_request`), its last status event and last run start are older than `COACH_STALLED_GOAL_HOURS` (default 24),
and the graph's other cards started at least `COACH_STALLED_GOAL_RUNS` (default 20) runs since then.

A delivery card whose only remaining wall is a human is not a stall. The open-ancestor frontier is the nearest
ancestors that are not done or archived and whose own parents are all done or archived (open intermediates are
walked past; done cards are walked through). If every frontier card is a human wait, the record is
`waiting_on_human` and names those cards. The hook stays quiet: there is nothing for the chat to take over. A
frontier card that is `running`, `scheduled`, `ready` or `todo` is agent work, so the delivery card stays
`stalled_goal`. Liveness is not progress.

- **Hook.** On the next user turn of a linked chat (lineage-aware), `pre_llm_call` adds one context line naming the
  card, its idle hours and the busy cards, and telling the agent to take the next step over itself or name the one
  wall; it says not to reopen, re-review or add another watcher. One line per chat per incident (`card@since`), and
  one Kanban comment per incident on the card (local boards only, in a background thread). `waiting_on_human` is
  not a takeover: no context line and no comment. Ledger:
  `<coach root>/stalled_goal.json`; logged as `agent_hint` with `rule=stalled_goal`. Board reads are cached per chat
  for 10 minutes. An idle chat gets it on its next turn (no push into an idle chat). `COACH_STALLED_GOAL=0` turns it
  off; cron turns are skipped.
- **CLI (read-only).** `python3 stalled_goal.py [--db PATH ...] [--replicas] [--session SID] [--now TS] [--json]`.
  Without `--db` it scans this home's boards; `--replicas` adds `kanban/sync-shadow/*.db` (other gateways' boards).
  Text and `--json` include both kinds (`stalled_goal`, `waiting_on_human` with the frontier cards). `--comment`
  comments once per `stalled_goal` on boards this home owns, never a human wait.

Measured 2026-10-28 on the hub (its own boards plus the MacBook replica), before the frontier split: 8 distinct stalled delivery cards, among them this defect's rollout card (122 h, 168 runs by its own chat's cards). After the split, a card whose open-ancestor frontier is entirely a human wait is `waiting_on_human` instead; a frontier card that is running, scheduled, ready or todo keeps it a stall. Tests: `tests/test_stalled_goal.py`, on the real graph's rows copied from the replica (`tests/fixtures/stalled_goal_balancer.json`, titles scrubbed) plus synthetic frontiers (human wait walked past an open intermediate, blocked with a human_request and no block_kind, mixed frontier, scheduled/todo/ready/running agent parent).

### Checkout-local sibling cache

The sibling loader keys modules by canonical source directory as well as module name. Discovering an installed
copy while another checkout has a review in flight no longer evicts that checkout's judge/reviewer. Symlink
aliases of one directory still share modules, and changed files still reload within that directory.

Failure chain: intermittent committed-change tests returned `None` → `pre_verify` caught the auth-store test
seat belt → the real judge ran instead of the patched judge → discovery of another checkout replaced the
process-wide `_coach_reply_check` entry → the next mtime check re-executed the test copy and lost its collaborator.
The root was cache identity based only on the sibling name; timestamp equality could also reuse the *wrong*
checkout's module. Single-checkout tests and a swallowed exception hid the interleaving. The earlier long-command
retention fix remains necessary but did not address this independent cause.

Tests: `test_second_opinion.py` forces another checkout's load before reviewing a real commit, for both short and
long command paths, retaining the original diff assertion. `test_module_cache.py` covers equal timestamps,
symlink identity and same-source reload. `test_nudge_reload.py` retains registered-hook handover coverage.
The new interleaving cases fail on the old loader; disabling commit-directory detection still fails all four
committed-change cases. No retries, increased deadlines or skipped variants are used.

### Hermetic coach tests and lesson scheduling

Disabled lessons are checked **before** scheduling `_lesson_bg`, not only when its worker eventually runs.
Tests default both collection and each test to an owned `HERMES_HOME`, isolate `HERMES_KANBAN_HOME`/DB and
`HERMES_RUNTIME_DIR`, and disable lessons unless a test explicitly supplies a fake judge. Coach threads are
joined before the shared monkeypatch fixture restores its judges/environment. The existing lazy-install,
gateway-reaping and System One protections remain.

`tests/home_guard.py` audits `open`, `sqlite3.connect` and executed module files. Real home state and installed
foreign plugins are refused, with a ledger checked even when production catches the exception. Late worker
violations propagate to the xdist controller. Test-owned directories remain writable; the selected core,
interpreter and installation manifest are read-only source/dependency exceptions, never SQLite exceptions.
This is a Python-process seatbelt, not an OS sandbox or a subprocess audit hook.

Failure chain: a hook scheduled a lesson while its flag was off → the worker ran after monkeypatch restored the
flag → the real auxiliary call resolved task concurrency → core lazily discovered live plugins. The earlier
checkout-cache fix prevented module eviction, not the work that triggered discovery. The missing home default
also let ordinary state/config readers consume real data. Not caught: fixtures isolated only the board pin,
background work had no test owner, and fail-open readers hid side effects. The scheduling check, isolated homes,
thread teardown and independent audit ledger remove those causes rather than retrying the flaky assertion.

State readers use core's task-aware home; `stalled_goal` board enumeration follows the shared kanban root
(`HERMES_KANBAN_HOME`), not a profile home. Explicit home arguments remain authoritative. Like core's board
enumerator, a worker's one-board `HERMES_KANBAN_DB` pin does not relabel that board as the default.

Tests: `test_home_isolation.py` covers disabled/enabled scheduling, the audit ledger, URI/symlink/foreign-plugin
paths and read-only runtime exceptions. `test_home_guard_lifecycle.py` runs real serial and `-n 2` subprocesses
with a swallowed post-test violation and clean controls. `test_state_paths.py` exercises supplied/task-override
state homes and shared kanban enumeration. `test_worker_isolation.py` checks inherited pins are replaced by
fixture-owned homes; `test_lessons.py` explicitly opts into its fake judge. The disabled-scheduling regression
fails on the prior implementation. No behavior assertions are relaxed; the old assertion that HERMES_HOME
remains inherited is replaced by the stricter new isolation contract.

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
  tool error repeated 3×). "Interrupted" is read from the start of the reply only (core's placeholders: `Operation interrupted`,
  `turn lease lost`, `Turn stopped by`, `the turn stopped while a tool result was still pending`); the tool log counts
  only for an empty reply whose last line is a cancelled tool, since its `-> tool(args)` lines carry command text.
  A repeated tool error files as `repeated <tool> error: <normalized error>` (card/session ids, paths, hex and numbers
  stripped; a JSON tool result is unwrapped to its error/output string), so each distinct failure is its own
  fingerprint, and its evidence (the tool, error line, count and last call) leads the defect quote. `handle()` adds it
  itself, so a gateway that hot-reloaded `self_heal.py` under an older `__init__.py` still records it. Owner adoption for any machine signal takes a session card only when its title or
  body names the tool (unless generic, like `terminal`) or the error's words; otherwise it files
  (`selfheal_adopt_skipped`). Origin: agent-6eb3a51e94, one fixed label lumped every repeated error together.
  A backend that stops mid-turn never reaches this hook: the next user turn, core's `[System note: Your previous turn
  was interrupted mid-run` resume note, files `turn interrupted (backend stopped)` with the gap since the last activity.
  Then, when the reply admits a mistake, hands you a step, or the turn's tool log shows
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

Interrupted model waits: core records why a turn was stopped while waiting for the model
(`display_metadata.interrupt = {reason, waited_s, prompt_tokens}` on the interrupted row; reasons `user_stop`,
`new_message`, `backend_exit`, `ws_orphan`, `lease_takeover`, `session_close`), and the digest carries it on the next
user turn as `prev_interrupt`. The backstop (`interrupt_triage`) then skips the judge for a bare nudge ("resume",
"status?") after a human Stop: it logs `selfheal_slow_model` (waited >= 60 s, with the prompt size) or
`selfheal_user_stop` instead of filing an agent fault. A `backend_exit` the user had to nudge is a lost turn
(auto-continue did not resume it) and is filed directly as a `gateway` defect, no judge. Other reasons, real complaints
after a Stop, and rows from older cores (no reason) are judged as before. Count slow first tokens by prompt size with
`grep selfheal_slow_model <coach>/log.jsonl`.

Both run in a background thread (the turn never waits) and are fail-open. They skip cron, subagents and Kanban
workers. The complaint prefilter also rejects cross-session courier messages, generated blocked-card notices and
standalone chat-closeout messages before any judge call. Those are coordination, not fresh user complaints;
underlying failures remain eligible through machine signals. Real user text before an appended notice is retained.
A deliberate detector probe, a session whose first user message (`state.db`, else the turn's own ask) starts with
`[selfheal-probe]` or "Live test of a detector", is logged (`selfheal_detect` with `probe: true`, then
`selfheal_skip why=probe`) and never filed: `handle`, `file_defect` and the deferred retry all check it, and a payload
from another host can carry `"probe": true` (agent-771fbd2b85; tests `tests/test_self_heal_probe.py`). `COACH_SELFHEAL=off|shadow|live` (default `shadow`): shadow logs a `selfheal_detect` event to
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

### Self-heal lane (`self_heal.py file_defect` → investigation → proposal → auto-approve → fix → Inbox "Fixed:")

Every new fingerprint (first time that UTC day) goes to `file_defect(d)`, from any entry path: the two hooks above
(complaint, admission, turn-end machine signal), the digest backstop, and direct callers (machine-signal sweepers,
monitors). `d` = `{fingerprint, component, symptom, source, kind, p_defect, session_id, quote, host, at, details}`.

- **Where it decides.** On the hub (`coach/selfheal-config.json`: `hub_host`, `hub_ssh`, `board`, `bb_board`; no hub
  configured = this host) it decides locally; on any other host, live mode pipes `d` to `self_heal.py file --mode live`
  on the hub over ssh, so dedupe, recurrence, suppression and budget live in one place (`coach/selfheal_lane.json`).
- **Shadow** (`COACH_SELFHEAL=shadow`, else `mode` in `coach/selfheal-config.json`, default shadow) logs `selfheal_would_file` with the full card args and creates
  nothing. **Live** creates the investigation card.
- **Complaint turns:** filing stores `complaint_at` (explicit turn timestamp, else detector `at`) and a short
  SHA-256 hash of the first 300 normalized quote characters. Before filing another fingerprint, the hub adopts
  an existing lane card for the same session if timestamps are within 120 seconds OR quote hashes match.
  This bridges backstop/hook wording differences and delayed queue delivery. Aliases keep original turn identity,
  not copied lifecycle verdicts: only the canonical owner can launch a fix, and an alias does not suppress other
  sessions' complaints. Even a fixed owner counts for the same old complaint. Declined owners and `not_defect` decisions
  older than the existing seven-day window do not count. Legacy lanes without either field remain distinct.
  Different session IDs and different turns with different quotes stay separate. Equal repeated quotes in one
  session intentionally count as the same complaint. Tests: `tests/test_self_heal_complaint_turn.py` exercises the
  backstop/hook pair (red before the guard), distinct turns, legacy state, timestamp boundaries, quote normalization,
  cross-session isolation and closed-owner policies. Shadow mode remains non-mutating (no simulated lane state).
- **Already-landed fixes without a digest:** if a defect has no session ID or no readable session digest, a
  `fix…` commit on the core fork's `origin/main` can close it without an investigation. The search covers detection
  minus 24 hours through filing time. The diff must edit the last traceback frame's repository-relative file and
  literal-message raise statement, or that raise's directly enclosing `if` condition. The function must match when
  named by the frame; unrelated same-file edits and duplicate messages in other functions do not count. The lane
  records `fixed` and logs `selfheal_adopted_owner via=commit_no_session`. Readable-digest session matching keeps its
  existing named-SHA/window/component policy. Tests: `tests/test_no_session_commit.py` uses actual temporary git
  histories for message/guard edits, absent/broken digests, wrong file/function/message, time bounds and session
  policy isolation. This intentionally does not infer fixes from arbitrary nearby code, dynamic messages or outer
  guards.
- **SQLite incidents:** `database disk image is malformed`, `KanbanDbCorruptError`, and `file is not a database`
  share a fingerprint by source host, normalized DB path and UTC event-hour. Explicit `db_path` wins, then a path
  in evidence; pathless Kanban tracebacks default to `~/.hermes/kanban.db`. Other pathless SQLite errors are not
  merged. Hub filing is locked across concurrent corruption detections. Later hits comment on the incident card;
  the card of a live `hermes kanban recover` (or `swap_db.py`) holding that DB's recovery lock, read from the
  `<db>.recover.pid` record, is adopted first (also when it sits on the damaged board itself); otherwise
  a running local card with `corrupt` and that DB in its title is adopted (oldest matching owner first). Different
  source hosts, DB paths and hours remain distinct. Remote-board owner lookup is not implemented.
  `event duplicate <fp> --card <duplicate-id> --owner <owner-id>` adopts real-defect ownership instead of imposing
  the seven-day `not_defect` suppression. Its brief directs investigators to this event.
  `tests/test_sqlite_corruption.py` replays six sanitized actual traceback tails (one card, five comments), concurrent
  filing, path/hour/host isolation, real owner-query filtering, comment failures, shadow safety and duplicate CLI.
- **Root families:** some exceptions are faces of one root. `kanban-db-view` = `disk I/O error`,
  `KanbanDbCorruptError`, `KanbanDbUnreliableViewError` or `database disk image is malformed` from Kanban code or a
  Kanban DB (a stale per-process view of a board). Before filing (after session adoption), a defect in a family
  joins the oldest open lane (filed/proposed/approved, not suppressed) of the same family: the lane gets that card
  as `fix_card`, `family_owner=<fp>`, and `selfheal_adopted_owner via=family` is logged. A fixed or closed family
  lane is not adopted, so a recurrence still files. Lanes record `family` when filed (`root_family()`). Tests:
  `test_self_heal_lane.py` replays the disk-I/O-then-corrupt pair (`test_replay_kanban_disk_io_then_corrupt_...`).
- **Investigation card**: key `selfheal:<fp>`, created_by `self-heal`, model `claude-opus-5-5`, 1 h, skills five-whys +
  hermes-mods-repo (Baby Bear: hub board `bb`, assignee `default`, five-whys + bb-self-fix). A missing `bb_board`
  is deferred (`selfheal_deferred`, payload kept for the cron retry), not dropped and not auto-created. The brief makes it investigate live, with a
  per-component checklist (Kanban events/runs, surface writer + backing state, processes/launchd/logs, git log), write
  the five-whys chain, and write `coach/selfheal/<fp>/proposal.md`: **What happened** (symptom, PT time, `@session`
  link, quote), **Why** (the chain with evidence), **Plan** (changes, regression test, detection guard, live check),
  cost/risk, undo; then `event proposed`. It posts nothing to the Inbox and never ships the fix. Not a defect →
  `event not_defect`.
- **Auto-approve** (default; `auto_approve` in `selfheal-config.json`, env `COACH_SELFHEAL_AUTO_APPROVE`): the hub
  cron (every 5 min) runs `file_fix` for every `proposed` fingerprint (also `self_heal.py fix <fp>` by hand): fix
  card `selfheal:<fp>:fix` whose acceptance is the regression test red before / green after, ship under the standing
  approval, verify live, and record the proof quietly with `event fixed --result result.md` (stored in
  `<coach>/selfheal-results/<fp>.md`, logged as `selfheal_fixed`). Results are never posted to the Inbox (it is for
  decisions only; archived "Fixed:" cards got the poster muted). A legacy open `selfheal` proposal card is resolved
  as Do it by the same pass; an explicit Not now / Never on it is still honoured. Budget/concurrency denials leave the
  fingerprint `proposed`, so the next pass retries.
- **`auto_approve: false`**: proposals wait in `proposed` until `self_heal.py fix <fp>`; Inbox outcomes on an existing
  `selfheal` card still decide (**Do it** → fix card, **Not now** snoozes 14 days, **Never** suppresses). (Do it is
  not a Kanban `task` action because a worker is fenced from creating cards via the CLI.)
- **Recurrence**: a fixed fingerprint that comes back files `selfheal:<fp>:r<n>`, whose brief says "previous fix <id>
  didn't hold" and requires a detection-level guard.
- **Rollout window**: "fixed" is stamped when the fix is pushed, not when every host runs it (core updates defer up
  to 3 h while cards run; long-lived apps keep old code until restart). So recurrence is judged by when the
  occurrence happened (`d["at"]`, else processing time): one inside `deploy_grace_hours` (selfheal-config.json,
  default 6, `0` = off) after `fixed_at` logs `selfheal_recurrence_held`, is stored in the lane's `held` list
  (deduped by `at`), and files nothing. The 3rd held occurrence files "came back" even inside the window, and the
  first one after the window files as before; either brief lists the held occurrences. `status` prints held counts,
  and the weekly report has a "Held in rollout window" line. Tests: `test_self_heal_lane.py` (rollout window block).
- **Budget**: engine_budget lane `selfheal` ($200/UTC day, its own cap outside the $20 engine total; spend = these
  cards' worker sessions), ≤ 3 running, ≤ 1 filing per fingerprint per UTC day. A known spend/concurrency denial
  creates **no card**: investigations persist in `coach/selfheal_pending.sqlite`, retried through admission by the
  live cron after midnight / one hour; failed retries retain their full evidence. Fixes remain proposed so Inbox
  sync retries the existing Do-it outcome. Unavailable or invalid accounting refuses admission. Each card body
  carries a `Budget for this card` line ($8 investigation, $15 fix) for the engine watchdog.
  This is not a strict monetary reservation system: queued/concurrent admissions, cross-board accounting,
  detector costs and watchdog overshoot still need separate enforcement before claiming a hard cap.
- **Hub cron** (`~/.hermes/scripts/selfheal_lane.py` → `self_heal.cron`, every 5 min, no agent): Inbox sync, queue drain, and every 25+ min the digest backstop (the complaint detector over
  new user turns of interactive sessions in the coach digests, per-session cursor; the judge gets each turn's `prev`
  agent reply from digest v6 plus the session title and the 3 earlier user turns, since without them the strict stage
  scored context-free reports like "why didnt this work [screenshot]" as new issues), Inbox sync. In the cascade a
  stage whose answer is empty or unparseable is retried once and then left out of the mean, not counted as a 0.
- Events (coach `log.jsonl`, read by `coach_weekly.py`): `selfheal_would_file`, `_filed`, `_recurrence`, `_recurrence_held`, `_skip`,
  `_proposed`, `_approved`, `_fixed`, `_reverted`, `_not_defect`, `_declined`. Cards report theirs with
  `self_heal.py event <kind> <fp> --card <id>`. `self_heal.py status` prints the lane state and budget.
- Tests: `tests/test_self_heal_lane.py` (each entry path, dedupe, recurrence, budget, bb routing, shadow vs live,
  Do it → fix card, Not now / Never). `COACH_SELFHEAL_NO_EXEC=1` (set by the test conftest) refuses any real
  `hermes`/ssh call.

### Machine-signal sweeper (`signals.py`, no agent, no LLM)

Raw machine signals become `file_defect` calls with `source: signal` and stable fingerprints:

The errors-log detector suppresses only the exact final exception
`RuntimeError: Hermes can't reach the model provider. You may be offline.`
The cron job summary remains quiet with or without that traceback; other exception types,
partial messages and quoted offline text in unrelated exceptions still file normally.
An ERROR from a Hermes logger saying `provider host does not resolve while network is up: …
(control host … resolves) — check the provider base_url` produces one agent signal, even
without a traceback, fingerprinted by provider host. Misconfigured provider diagnostics are
not treated as offline noise. `tests/test_signals.py` replays both records, with and without
tracebacks, and checks the suppression boundaries using in-memory logs (no live stores).

| kind | source | fingerprint (host in it?) |
|---|---|---|
| `errors` | new tracebacks in `<home>/logs/errors.log` (any level), minus tool output (`agent.tool_executor`) and provider transients (429/529/overloaded/timeouts) | exception type + innermost 4 frames as `file:function`, no line numbers (no) |
| `hook` | `Hook '<hook>' callback <cb> raised: …` (plugin hook exceptions) | hook + callback + exception type (no) |
| `worker` | managed kanban worker crashes in `<home>/logs/managed-worker.log` (`[pid N] ` lines, no timestamps), only as a crash storm: 3+ distinct pids dying with the same exception + normalized message (numbers, ids, paths collapsed) within 30 min on one host. State lives in the cursor, so a storm spans passes; `at` is the pass time. One signal per fingerprint per 30 min; `sk-` keys are scrubbed from the details | exception type + normalized message (no) |
| `cron` | a job whose sticky `last_failure.at` is new (`cron/jobs.json`), even if it has recovered since | job name (yes) |
| `kanban` | a card with ≥ 2 runs `crashed` / `timed_out` / `spawn_failed`, every board db; self-heal's own `selfheal:` cards are skipped. Also the dispatcher's systemic breaker (3+ same-fingerprint crashes in one tick, each card crashed once): a `gave_up` event with `sticky: true` (event-id cursor per board) files one `systemic kanban crash … held N card(s) in triage` per board + normalized error head per 30 min; it names no card trigger, so the held cards finishing later doesn't drop it | board + card id (yes); systemic hold: error head (no) |
| `gateway` | `gateway.previous_unclean_exit`, `asyncio.run.exception`, `gateway.exit_nonzero` in `logs/gateway-exit-diag.log` (a clean exit or a code-75 restart is not a crash); `gateway.exit_signal` (an unplanned SIGTERM bounce: bootout, `kickstart -k`) only as a restart storm, 3+ in 10 min per profile | tag + exception type; for `exit_nonzero`, its `cause` + reason class (a new cause is a new defect; `cause: unrecorded` = exit path missing a cause note) (yes) |
| `coach` | coach `fixloop_card` (2nd confirmed failure of a fix), and a `reply_nudge` of any reply_check rule (handoff, status_only, report_only, denied_ask, ...) only when its `reply_nudge_outcome` (pre_verify re-runs that rule once on the revised reply and the turn's tool log, attempt 1, same judge and threshold, never nudges again) says it still fails, or no outcome came within 15 min (symptom "coach <rule> nudge got no outcome (no revised reply)"). A no-outcome nudge whose session transcript (`state.db`, default home and profiles) shows an interruption marker ("Operation interrupted" / "An automated delivery was interrupted") after the nudge with no later final reply is not filed: the turn died (gateway restart), and `nudge_outcome_lost` (reason=interrupted) is logged instead; an unreadable db or a session not found files as before. Non-handoff nudges without the `outcome` marker (a backend still on the old plugin) are not filed. The complaint backstop never judges `[coach]` turns (they are stored as user-role messages but are the coach's own text). `self_heal.py status` → `nudges` counts per rule (resolved / still_failing / no_outcome / waiting / untracked / filed); `handoff_nudges` is kept | session (+ issue) (no) |
| `monitor` | a propose-only monitor's finding (`route-monitor`, below) | the monitor's own dedupe key |

Profiles (`<home>/profiles/*`) are swept too. Cursor `<home>/coach/signals-cursor.json` (offset + inode per log, last
run id and last event id per board, last failure per job, the last 30 min of worker crashes); first sight looks back 7 days; a rotated log is re-read; ≤ 25 signals per
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

### Desktop backend timeouts (`backend_timeouts.py`, no agent, no LLM)

The Desktop app logs each failed backend call to `~/.hermes/logs/desktop.log` as
`<local stamp> [hermes] [hermes:api GET /api/...] Error: Timed out connecting to Hermes backend after Nms`. On
2026-10-27 the MacBook's `hermes serve` was CPU-starved and logged ~1,200 of those an hour for hours; nothing read
them until an archive preflight failed in front of the user (self-heal `desktop-64ac180d9e`). A hermes cron job on
each Mac (no-agent, every 10 min) runs `backend_timeouts.py`: it counts those lines over the last 30 min (live log
plus the rotated `desktop.log.1..3`), grouped by request path with ids folded to `<id>`. At or above the threshold
(default 30; config.yaml `coach.backend_timeouts.threshold` / `.window_minutes`, env
`COACH_BACKEND_TIMEOUTS_THRESHOLD`, or `--threshold`) it runs `hermes kanban create --idempotency-key
backend-timeouts:<host>:<date>` with the counts, top paths, load average and backend `ps` CPU; later firings that day
comment on the same card at most once an hour while it remains open (state in
`<HERMES_HOME>/coach/backend_timeouts/state.json`). Before throttling or commenting, the watcher reads the
card's status. Done, archived, or unreadable destinations start a new card with suffix `:2`, then `:3`,
and a line naming the preceding card; the daily state tracks the newest destination and sequence. Tests
cover open-card comments/throttling, done/archived recurrences, lookup failures, and a second recurrence.
The
card's acceptance is a fresh 30-min window with zero timeouts after the fix, not a restart. No Inbox items; stdout is
empty while healthy. `--dry-run` prints the JSON report and files nothing; `--hours` prints a per-hour histogram of
every timeout still in the logs. Tests: `tests/test_backend_timeouts.py` (40 lines file one card, 5 file none, a
repeat the same day comments instead of duplicating, lost state, next day, rotated logs, histogram; also passes
under Python 3.9).

On macOS, the watcher also reads `pmset -g log` (30-second bound) to identify positively observed Sleep/DarkWake
intervals, ending at either a plain full Wake or DarkWake-to-FullWake. Timeouts in those intervals, including
expired timers delivered in the full-wake second, remain in raw `count`/`by_path` but are split into
`sleep_count` versus `awake_count`/`awake_by_path`. Only `awake_count` triggers an incident. There is no
post-wake grace period: failures from the following second alert normally. The `power` field reports the
intervals and probe status. Unsupported platforms, unavailable power history and failed probes suppress nothing.
The acceptance check still requires raw `count: 0`, not merely `awake_count: 0`. Additional regressions cover
sleep-only and mixed bursts, darkwake-to-fullwake, the wake-second boundary, pmset failures and unsupported hosts.

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

### Duplicate-owner overrides (`create_overlap_watch.py`, no agent, no LLM)

Core `kanban_create` refuses a card that open cards on any board or gateway already cover (core patch 0726,
`core/kanban-create-overlap.md`); an agent overrides with `distinct_from` + `distinct_reason`, recorded as a
`create_overlap` event with `outcome: override` on the new card. A hermes cron job on hermes-hub (no-agent, daily)
runs `create_overlap_watch.py`: it reads every board on the host (`kanban.db` + `kanban/boards/*/kanban.db`,
read-only) and counts overrides in the last 7 days whose new card is now archived (a coordinator closed it as a
duplicate). More than 2 files ONE card per host and ISO week (`--idempotency-key
create-overlap-overrides:<host>:<year>-W<week>`) with the evidence, asking an agent to fix the refusal note,
matcher or skill guidance that let them through. `--dry-run` prints the JSON and files nothing. Tests:
`tests/test_create_overlap_watch.py` (only recent archived overrides count across boards; exactly 2 files nothing,
3 files one card with a stable week key; dry-run files nothing).

### Bare ask-gate notices (`withheld_notice_watch.py`, no agent, no LLM)

ask-gate used to swap a whole reply for "I withheld a request ..." (17 in 7 sessions, Oct 27-29). It now holds
first and keeps the draft minus the refused ask, so a delivered bare notice is a defect. A hermes cron job on
hermes-hub (no-agent, hourly) runs `withheld_notice_watch.py`: it scans `state.db` for assistant replies that start
with "I withheld a" / "The workflow remains stopped" / "I suppressed a repeated" newer than its last scan and files
ONE card per host and day (`--idempotency-key withheld-notice:<host>:<date>`), commenting on it for later hits.
`--seed` marks history as seen (run once at install); `--dry-run --hours N` prints hits and files nothing. Tests:
`tests/test_withheld_notice_watch.py` (only assistant replies starting with a notice count, not quotes or mentions;
one card per day then a comment only on new hits; seed and dry-run file nothing).

### Restart notices to a team home channel (`restart_notice_config_watch.py`, no agent, no LLM)

A gateway posts "Hermes is restarting" / "Gateway online" to each platform's home channel unless
`platforms.<platform>.gateway_restart_notification: false` (default true). alpha-askeve's home channel was a team
Slack channel and got 26 such notices in 7 h before the opt-out was added. A hermes cron job on hermes-hub (no-agent,
hourly, "restart-notice config watch (hub)") runs `restart_notice_config_watch.py`: it reads `<HERMES_HOME>/config.yaml`
(profile `default`) and every `profiles/*/config.yaml`, takes each platform with a home channel (`home_channel.chat_id`
in config.yaml or a non-empty `<PLATFORM>_HOME_CHANNEL` in the profile's `.env`), and files ONE card per host, profile
and platform (`--idempotency-key restart-notice-config:<host>:<profile>:<platform>`) when the flag is not false (top
level or under `extra`, read like core's `_coerce_bool`). A home channel that should keep the notices (an owner DM) is
allow-listed in `<HERMES_HOME>/restart-notice-allow.txt` as `<profile>` or `<profile>:<platform>`. An unreadable
config is reported, not skipped. `--dry-run` prints the JSON and files nothing; a clean real run prints nothing. Tests:
`tests/test_restart_notice_config_watch.py` (config.yaml and `.env` home channels flagged, explicit true flagged;
top-level/extra/string false, flow-style YAML and no home channel quiet; allow-list by profile and profile:platform;
unreadable config reported; dry-run files nothing, a real run files one keyed card; clean run silent).

### Agent probes in a person's chat (`probe_token_watch.py`, no agent, no LLM)

A fix worker proved its fix with `hermes chat --resume <David's chat> --oneshot -q "... Reply exactly ROUTE_..."`
and left the probe and its bare-token reply as the newest turns of two of his chats (self-heal agent-93a55211b1).
The user-chat-probe-guard plugin now refuses that; this catches what gets through. A hermes cron job on hermes-hub
(no-agent, hourly) runs `probe_token_watch.py`: it pulls `~/code/hermes-coach-data` and reads every host's digests
written in the last 3 days, and flags a user-facing session (desktop, cli, messaging) where a real user turn is
followed by a `Reply exactly <TOKEN>` turn, or whose `last_reply` is a bare `UPPER_token` after one. A session an agent
opened for its own test (first user turn is the probe) is not flagged. ONE card per session
(`--idempotency-key probe-token:<host>:<session>`), later evidence as a comment. `--seed` records current hits as known
(run once at install); `--dry-run --days N` prints hits and files nothing. Replay over every digest on 2026-10-30
flags exactly macbook 23ef49 and 55b17f. Tests: `tests/test_probe_token_watch.py` (incident digest and bare-token
reply flagged; worker/oneshot sources, ordinary short answers, prose "reply exactly the same", agent-opened test
sessions not flagged; one card per session, nothing on an unchanged rerun, a comment on newer evidence; dry-run files
nothing).

## Files

- `__init__.py`: the hooks (`pre_llm_call`, `post_tool_call`, `transform_tool_result`).
- `self_heal.py`: the complaint and self-admission detectors, fingerprints and shadow/live handling;
  `eval/selfheal_replay.py` replays them over a labeled set.
- `signals.py`: the machine-signal sweeper, forwarder and monitor router (stdlib, Python 3.9+).
- `agent_rules.py`: the agent-channel rules, plus the signals the user channel needs (subagents, long jobs, bug signatures).
- `core.py`: the file store under `<HERMES_HOME>/coach/` (`sessions/<id>.json`, `bugs.jsonl`, `log.jsonl`), the gate, the user-rule evaluation, the briefs (spin-out's transcript reader + aux task `coach`) and stats.
- `drafts.py`: the redacted, size/age-capped store of judged reply drafts (`drafts.jsonl`) that `reply_nudge` /
  `report_only_confirm` events link to by `draft_id`.
- `second_opinion.py`: the fix-loop review card and the unverified-diff review (settings, repo/diff detection, the
  neutral reviewer prompt); `eval/second_opinion_replay.py` counts how often each would fire over `state.db`.
- `stalled_goal.py`: the stalled-goal detector (a chat's delivery card idle while its graph spends runs; a human-wait ancestor frontier is `waiting_on_human`, not a takeover), hook helper
  and read-only CLI.
- `curriculum.py` + `curriculum.yaml`: the tips (loading/overlay, live triggers, spacing, adoption read).
- `dashboard/plugin_api.py`: `/state`, `/offer`, `/event`, `/restart`, `/board`, `/file-bug`, `/stats`.
- `desktop-plugins/coach/plugin.js`: the panel. It checks after each finished turn, every 60 s while a turn runs, and on screenshot sends.

The brief model is set with `auxiliary.coach.provider` / `.model` (default: the auto aux model).

## Tests

Run the full Python suite through a current Hermes core checkout (set `CORE` and
`MODS` to your checkout paths):

```bash
CORE="$HOME/.hermes/hermes-agent"
MODS="$HOME/code/hermes-mods"
(cd "$CORE" && scripts/run_tests.sh -j 2 "$MODS/plugins/coach/tests")
```

Tests clear inherited delegated-child and `HERMES_KANBAN_*` worker pins before
fixtures run, and use a temporary fallback board (without changing `HERMES_HOME`).
`test_worker_isolation.py` checks this in a subprocess with injected worker pins.
Transport subprocesses retain managed `site-packages` paths, disable lazy installs,
and still exclude worker identity and credentials; `test_transport_dependency_env.py`
checks dependency import and identity isolation. The manager acceptance fixture
checks four distinct callbacks (coach goal + compute envelope, ask-gate, and
chat-closeout), including unload/reload and dispatch-order assertions.

This uses the core's locked test dependencies and per-file isolation. Core-dependent
tests and transport children use `hermes_yaml`, the runtime parser: PyYAML stopped
being a core dependency in core commit `284dbaf537`. The Inbox action contract test
keeps its assertions unchanged and can also run without core using PyYAML:

```bash
cd plugins/coach
uv run --with pytest --with pyyaml python -m pytest -q -p no:cacheprovider tests/test_self_heal_lane.py -k do_it_is_a_resolve
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
Its six cases cover initial rejection, composer Stop, Ctrl+Enter queueing,
reconnection of the exact remote session socket, full renderer-document reload,
and simultaneous loss/replacement of all gateway sockets in the single configured
remote topology (ambient plus default-profile socket). Reload asserts a new document
identity and keeps DOM leak observation active across navigation. Each checks rejected-candidate
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
used for these tests. Renderer reload is not Electron process relaunch or backend
restart. Socket-loss fault injection permits immediate reconnect; sustained network
outage, completion while offline and multiple remote gateways remain unverified.
Production activation, remote owner adapters and exact-original replay remain
separate requirements.

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


### Held rechecks and current verification scope

`tests/test_goal_run23_review.py` covers configuration loss/invalidity and explicit
off/shadow transitions after a confirmed hold through complete scripted agent turns,
including streamed-text exclusion, incomplete status, and Stop precedence. Fresh
off/missing/invalid configuration remains default-off; shadow remains non-enforcing.
The same suite rejects successful URL readbacks predating newer human steering,
then accepts a fresh post-steering readback through the native pending-recheck gate.
This conservative verification epoch does not infer whether older results remain
semantically applicable. Kanban current-scope reads reject queued UI receipts;
a later genuine canonical user row is a separate positive control.
`test_goal_queue_owner_acceptance.py` additionally connects native busy acceptance,
queue draining and adopt/stage/flush persistence to the public owner guard and real
scoped consumer health. Its five cases cover matching, mismatching, newer-request,
Stop and consumer-loss boundaries. The worker launch is replaced with the existing
native turn-body fixture: this is in-process integration, not a complete agent or
serve/WebSocket ownership turn. All fixtures use isolated state and no live repairs.

Native mid-turn steering now carries a process-local core attestation: the guard
uses the original human text only when identity and content still match. Arbitrary
display rows and marker-shaped text cannot mint this proof. Observed tool results
retain their captured payloads but are ordered at their live suffix positions;
duplicate, reordered or incomplete observed suffixes fail unavailable rather than
guessing freshness. `test_goal_run24_review.py` exercises native steering injection,
capture/observe and pending evaluation with stale/fresh verification, repeated
steering and spoofed tails. Core `test_steering_provenance.py` covers identity
binding, copying, wire stripping and suffix preservation. These are native seam
tests, not whole-transport steering acceptance. The attestation is not historical
authentication after restart. Native whole-turn in-place/rotated compaction now persists accepted steering before summarization. A bounded profile-qualified scan treats a lost live attestation (including archived steering) as UNKNOWN, not permission; pending holds cannot clear even with a fresh readback while scope is missing. `test_goal_steering_retention.py` exercises actual AIAgent turns and SessionDB compaction with scripted model/judge/summarizer boundaries, plus cold-history loss detection. Core `test_steering_compaction_retention.py` covers failed/missing storage and duplicate-free flushing. Hook registration is fixture-wired; provider-owned compaction and real transport restart retention remain unverified.


### Native HTTP-availability remediation (bounded acceptance)

For a literal user request `Restore HTTP 2xx at <URL>.`, the read-only validator also
accepts a paired foreground native `terminal` result from this canonical shell-quoted
argv (use `shlex.join` to quote the URL and arguments):

```
/usr/bin/curl -q --noproxy '*' --path-as-is --max-time 5 --silent --show-error --output /dev/null --write-out '%{http_code}' -- <URL>
```

The call must set `http_readback=true`. This requires the matching core producer:
Linux **local** foreground only, validated before acquiring a shell environment. After
ordinary pre-exec and approval guards it directly executes the fixed argv with no shell,
no startup/snapshot, and an explicit clean child environment (no inherited loader,
function, proxy or curl-config overrides). It emits the outer
`execution="local-http-readback-v1"` field; this is native producer metadata, never
parsed from command output or supplied by the judge. Ordinary shell execution remains
unchanged but **cannot certify remediation**, even when its exact curl command prints 200.
Older cores and unsupported backends/environments remain UNKNOWN, never host-local fallback.
The actual terminal envelope must also have integer `exit_code=0`, no error/pending
approval or background session, and output consisting only of a 2xx HTTP status.
The command does not follow redirects, use proxies, or print page content as status.
The native producer rejects curl URL globbing, credentials and fragments, bounds curl
to five seconds (outer wait at most six seconds or the requested shorter timeout),
and kills/reaps on Stop/timeout without success provenance. Trust assumes the native
runtime, OS/system executable and trust store are intact; this is not cryptographic
host attestation or protection from privileged binary/network compromise.
No shell suffixes/substitutions or arbitrary
successful commands qualify. This is HTTP availability, **not** proof of transactions,
content correctness or arbitrary natural-language repair goals. Other native shapes and
broader goals remain UNKNOWN; missing curl is not remediation. No command runs in the hook.
Later user steering or a later supported same-target readback invalidates the citation.

`tests/test_goal_native_remediation.py` drives complete held AIAgent text stops using the
real executor/registered terminal and private exact-command approvals against a disposable
loopback app. It proves a native repair changes the requested path from 503 to 200 and
clears the hold, while an ineffective repair/continued 503, an unrelated 200 path, stale
pre-steering success and Stop do not. Separate parser/envelope controls reject redirects,
shell wrappers, failures, broader goals and later failures. The native shell-function
regression rejects an actual uncontacted-app 200; opt-in readbacks still contact the
app despite configured startup functions, inherited function/startup/loader/proxy
variables, curl config and an existing shell snapshot. Core
`tests/tools/test_terminal_http_readback.py` exercises real user-deny preservation,
unsupported remote/background/PTY/timeout-promotion/command/glob rejection, and
active request Stop/timeout. All HTTP servers are disposable loopback fixtures.
Primary/judge answers and hook
wiring are fixture-controlled; HTTP/terminal results and repair are real. This is not
live-model, production or activation acceptance, and does not close the original-incident
or remote-ownership release gates.


### Bounded native inline images (experimental)

The goal gate preserves authenticated native user content unchanged in its capture
and proof evidence. `goal_images.py` supplies a separate text-budget projection
(shared by scope recovery, rotated capture, observation, and evidence budgeting):
attachment indices and SHA-256 identities replace image bytes only in the JSON
text projection. The auxiliary receives the original ordered `image_url` blocks,
not base64 embedded in JSON text. Full user text and literal tags remain intact;
screenshots never confer authority. Native text-only Stop alongside an image
still vetoes abandonment.

The deliberately narrow supported format is static 8-bit non-interlaced PNG
(grayscale, RGB, grayscale-alpha, RGBA), including bounded screenshot metadata
(iCCP, cICP, eXIf, pHYs, iTXt and iDOT) preserved byte-for-byte. CRC,
header, exact bounded zlib output and scanline filter checks reject malformed
payloads. Budgets: four images, 4 MiB decoded bytes per image / 8 MiB aggregate,
4 million pixels per image / 8 million aggregate, 1024 PNG chunks, 200 content
parts per native user row. Existing 200-row / 32k text limits remain; images do
not spend that text budget. Metadata has a 1 MiB aggregate expanded/raw budget;
compressed profiles/text are bounded before decoding. Other formats, unsupported
ancillary chunks, animation, remote
or file URLs, historical list content (even self-labelled with a sidecar), and
unknown blocks remain UNKNOWN. No image fetching or historical reconstruction.

A vision verdict is accepted only if the auxiliary's reported final provider/model
has positively known vision capability. Main-model defaults and provider aliases
are not borrowed for auxiliary capability checks. Unsupported/missing route metadata or
provider errors remain UNKNOWN. This capability check occurs after the auxiliary
call, to validate the final route including fallback; it does not certify an
actual provider's image interpretation. Tests are offline, not live-model semantic
validation: `tests/test_goal_native_multimodal.py` covers native full-loop delivery,
exact bytes/order/text/provenance preservation, limits, unsupported inputs/routes,
literal restrictions and explicit host Stop. Original historical incident
prevention and live transport/model behavior remain separate acceptance gates.
