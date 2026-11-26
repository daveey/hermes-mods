# kanban-honest-done

A Kanban worker can't fake "done".

## Why

A goal-mode card couldn't run its live acceptance (the target machine was offline). The worker ran
`hermes kanban edit` on its **own** card to drop that acceptance from the body, retitled it
"… (shipped; …run split to follow-up)", and completed 10 s later. No follow-up card existed. The goal judge accepted
the narrowed body, every card in the chat was done, and the chat went green while the work wasn't done.

## Structured completion receipts

Optional `metadata.completion_receipt` is validated by the canonical core validator
before the existing admission checks. The loaded completion writer must advertise
receipt admission; newer helper files alone do not make an old running writer safe.
Malformed or unsupported receipts are refused. Scope/supersession claims, summary,
other metadata and origin delivery remain gated. Typed remaining obligations and
historical evidence references are not flattened into dropped-work prose: explicit
deferral does not demand a new execution obligation. The canonical writer separately
verifies pending owners; a valid receipt is never an acceptance or approval waiver.
Receipt-only references cannot satisfy a follow-up required by the prose gate.

`tests/test_completion_receipt.py` exercises the guard classifier through direct
completion, wrapped `tool_call`, terminal CLI text and decoded `execute_code` terminal
text. Those are classifier tests, not actual subprocess/writer or live deployment
proof. Dynamic Python/shell values remain subject to the canonical writer, not this
static classifier.

**Hint, not refusal (R).** A worker's own `kanban_complete` whose summary claims delivery
(shipped, merged, fixed, deployed, ...) with no `metadata.completion_receipt` gets a hint
appended to its tool result, after the call ran: on a refused call it says to add the
receipt on the retry. It never blocks; legacy completions stay valid. Each hint logs
`kanban-honest-done: receipt-hint <task> ok=<bool>`, the count of receipt-less delivery
claims (self-heal agent-f82d813de9). `tests/test_receipt_hint.py`.

## Body-only failure branches

In any session, branch-looking comments are refused when their literal full step is
not already among Gate G's `preregistered_branches(body)`. A comment registers nothing.
The same case-insensitive, line-anchored parser is used for bodies and comments,
including diagnostic bullets such as `- If it failed: ...`; use ordinary prose
(`On failure, ...`) when not registering a disposition. Exact body citations and
mid-sentence mentions remain allowed. Appending another sentence changes the step;
there is no prefix or fuzzy match.

`kanban_comment`, `cross_task action=comment`, and literal `hermes kanban comment`
commands in `terminal` are checked, including deferred `tool_call` wrappers.
A terminal command's arguments are read the way hermes reads them, by a static reader
of core's `hermes kanban comment` grammar. It accepts argparse's unique prefixes
(`--max 5` is `--max-len 5`, `--boa x` is `--board x`), `--opt=value`, and options
after the text. Help and ambiguous options post nothing, so they are never refused.
The hook never builds core's parser, because the first build in a process costs
63-107 ms. The reader doesn't check value types, so it still refuses a command
hermes would reject anyway (`--max abc`).
The guard resolves the selected local board, not an arbitrary same-ID row on another
board. Explicitly remote targets and unreadable bodies are allowed with the body-only
hint appended to the result, never refused blind. This is static tool-hook feedback,
not shell evaluation or a guard on human CLI/API writes. Tests:
`tests/test_comment_branches.py` (entry matrix, real-pattern regressions, missing-body
hint, CLI options, board selection, remote fallback, worker and non-worker hooks) and
`tests/test_comment_branches_cli_options.py` (abbreviated and reordered options, board
prefixes, help, a check that the hook builds no parser, and, when core imports, a drift
check of the static option lists and of the reader's results against core's parser).

## Sidebar classification cost

The sidebar memoizes only the pure dropped-work text classification, keyed by the
exact classifier function, summary and title. The LRU retains at most 1024 entries;
inputs over 16384 combined characters bypass it. Database-dependent verdicts are
not cached here: ACK insertion/deletion, link/creator changes, follow-up statuses
and replica arrival/removal remain fresh. The worker completion gates still use
the literal classifier. This is not a complete semantic input declaration and
does not suppress the holder's conservative database-dependent refreshes.

`tests/test_attention_classification_cache.py` covers repeat/changed text,
classifier replacement, bounded/oversized retention, and mutations after warming.

## What it does

Inside a dispatcher-spawned worker (`HERMES_KANBAN_TASK` set), a `pre_tool_call` hook refuses (E and F: any session):

| Gate | Refuses | Paths |
|---|---|---|
| A | editing your **own** card's title/body (`--priority`, other cards, comments stay allowed) | `hermes kanban edit <own id, $HERMES_KANBAN_TASK or $HERMES_KANBAN_WORKER_TASK> --title/--body` via `terminal`, `process`, `execute_code`, `tool_call` |
| B | completing your own card when the handoff, meaning the summary **and every text value in its `metadata`** (`not_done`, `remaining`, nested lists), admits dropped, deferred, unrun or unverified work (incl. plain "not verified / not performed / not checked / not confirmed / not tested"), unless the handoff names a follow-up card `t_xxxxxxxx` that exists (any board, or this host's Board replica `kanban/sync-shadow/*.db`: a pool worker's live board lacks cards placed on other gateways), is open or done (not archived), and was created by this card (`creator_task_id`) or is linked to it | `kanban_complete` tool (direct or via `tool_call`), `hermes kanban complete <own> --summary/--result/--metadata` via shell or `execute_code`. `execute_code` is not run: shell text is the decoded Python string value (`ast`, so a `repr`-escaped command is the command the shell would see). A command that exists only as a runtime value is not recovered |
| D | completing your own card when its body or comments name an origin chat (`origin … @session:X`, e.g. "Origin chat: @session:default@macbook/X") that is not linked to the card: no unreleased `task_chat_links` row, no notify subscription (`tui` X, or `hub` `<gw>/<profile>/X`), and no `follow-through: SENT … X` comment. The refusal tells the worker to send the chat the result with `cross_session` and record that comment (workers are fenced from `hermes kanban notify-subscribe` on their own board), or request worker-runnable operator repair if the send fails; only an actual human step uses `kanban_block` with `human_request`. Destroyed origins have a separately verified exception below. A Board-synced card run on another machine (`runs_on machine:X`) is also checked against the Board replica (`kanban/sync-shadow/*.db`), where its owner's links live, and the adopt-task hint names the owner (`tasks.created_on`), never the executing machine. Double-quoted text is skipped | same paths as B |
| E | (any session, not only workers) `kanban_create` whose title/body names an origin chat on **another** gateway (`origin … @session:<profile>@<gw>/<id>`, `<gw>` a known hermes-hub gateway) without `origin_session` (or `HERMES_ORIGIN_SESSION`) binding that chat. The refusal gives the exact `origin_session='…'` to pass, which subscribes the chat so it sees the card and its completion. Fails open when this gateway has no hermes-hub name | `kanban_create` tool (direct or via `tool_call`) |
| F | (any session) `hermes kanban edit <id> --body` that **changes acceptance**: adds waiver text (`ACCEPTANCE AMENDED`, "acceptance waived/withdrawn/…", a `WITHDRAWN:`/`WAIVED:` line, "observation/proof withdrawn") or drops a sentence of the current body that states acceptance (a `DONE` sentence, an "acceptance" sentence; removing an earlier waiver section is a restore and passes), unless the new body cites `user message <id>` (or `msg<id>`) that resolves in `state.db` to a user turn typed in **this** chat (this session or its compression ancestors; not a delegated parent, not another chat), in a local human chat only (session source `desktop`/`tui`/`cli`/`local`; `peer-mail`, kanban, subagent, cron, dispatch/review lanes and any unknown source are refused), with `messages.display_kind` empty (including legacy/UI decision rows that pass the text checks; not proof of exclusive keyboard authorship) or `steer` (a mid-turn `[OUT-OF-BAND USER MESSAGE …]` row, accepted only when it is exactly one envelope), not a runtime/Kanban notice (`[...]`, `⏸ [..]`, `⚠ subagent … was stopped by a restart`, `[Message from another Hermes session …]`, `David pressed …`; any other `display_kind`), and newer than the card's current body (its last `edited` body event, else creation). An unknown calling session fails closed for such edits only. It proves who gave the authority and when, not that the words authorize the change. `acceptance.py` | `terminal`/`process` (literal `--body`, `"$(cat FILE)"`, `$(< FILE)`, unquoted `$(cat FILE)`, variables assigned earlier in the same command), `execute_code` (decoded string literals), `tool_call`. A body built any other way at runtime is not recovered (not refused). The `kanban edit` CLI typed by a human and the dashboard are not hooked |
| G | completing or `kanban_request_review`-ing your **own** card with a handoff that turns a **failed acceptance threshold into a waiver or a guessed pass** (see below). `thresholds.py` | `kanban_complete`, `kanban_request_review` (direct or via `tool_call`), `hermes kanban complete <own>` via shell / `execute_code` |
| H | (any session) a **write that delegates an action the user did not consent to**: after an approval result in this chat is a non-consent (`BLOCKED: … has NOT consented` / timed out, "Silence is not consent"), any `kanban_create`, `kanban_comment`, `kanban_supervisor_request`, `cross_task`/`cross_session` send, `delegate_task`, or `hermes kanban`/`hermes-hub` write command naming a card the blocked call named, until the user's next real message in that chat. A worker is bound by the stops of the chat its card came from (`tasks.session_id` or the body's `origin @session:`), on the same host. `consent.py` (see below) | `post_tool_call` records, `pre_tool_call` refuses, `pre_llm_call` clears |
| I | completing your **own** card with a handoff that **claims a delivery to another card** ("posted on t_X", "commented on t_X", "relayed to t_X") with no write result behind it. `delivery.py` (see below) | same paths as B |

Gate D keeps `(gateway, profile, session)` qualifiers throughout membership checks;
malformed short qualified tokens cannot backtrack into a profile name. Replica
fallback is restricted to the same owner gateway, board and profile. For a
courier-owned card executing at its origin gateway, `origin_receipts.py` can
recognize the existing authenticated origin obligation even when the board
shadow omits notification subscriptions. It requires an unreleased binding,
matching accepted receipt/hash/request and exact owner/origin identity, a current
matching publication/marker, fresh replica metadata and readable live owner
scope. All reads are read-only; it never creates, refreshes, adopts or releases
an obligation. Missing, stale or malformed evidence does not count. No exemption
is based on scratch sessions or prose describing another card. Tests:
`tests/test_origin_receipt_recognition.py` (identity negatives, receipt freshness,
malformed-token regression and query-only connection/closure checks).

### Destroyed origin gateways

Gate D accepts a `follow-through: origin chat gone` note in completion metadata
(or a card comment) only after independent exact owner-store absence checks. A
bounded session catalog, `where` miss or offline gateway is **not** absence.

The gate requires a fresh authenticated registry, fresh caught-up hub and Board
replicas, and complete gateway/profile inventory. It checks every registered or
publishing gateway, including a reused origin name and profiles holding archived
sessions. Each scope must have two complete, sequential sweeps of authenticated
`notice-receipts` answers reporting `session_exists=false`. That reader uses
read-only `SessionDB.get_session`, not the truncated published catalog. Envelope,
receiver echo, successful state and freshness are checked independently. A live
session, missing/stale/failed/partial answer or unreachable owner refuses.

Registry, replica and inventory freshness is 180 seconds. Owner reads have a
600-second total window to accommodate sequential fleet fan-out; every second
sweep request must additionally have been created within 180 seconds. All
positive, pending, rejected or malformed reads within the full 600-second window
veto absence, even if other newer reads say absent. These are bounded observations,
not an atomic fleet snapshot.

An absent registry member can only be omitted with explicit structured terminal
rehome evidence checked at or after this card's creation, no fresh heartbeat,
and no published collection newer than that termination check. Missing card
creation time cannot authorize omission. The trust root for EC2 proof is the
Board admin-key caller's attestation: the server verifies revocation, but does not
independently query EC2. The newest observed rehome is selected across available
board replicas, not per board. This
lifecycle evidence explains a missing member; it does **not** establish which
incarnation held a session. Exact absence on all remaining owners is independently
mandatory. A reused gateway name is queried rather than inferred dead.

The exact note names the qualified session, read request IDs and any lifecycle
proof; it is audit-only regardless of its author. The gate performs no writes.
Queue read-only `hermes-hub request <gateway> sessions <profile>/<sid>
notice-receipts --arg keys=<valid-remote-notice-key> --id <unique-id>` separately,
wait for every answer, then queue the second sweep. No notice is delivered by this
lookup. Copy the gate's exact note into metadata, not a SENT or adoption claim.
Tests: `tests/test_origin_gone.py`.

Each refusal says what to do: finish the missing part, or `kanban_create` the follow-up (`parents=[<this card>]`)
and name its id, or `kanban_block` with the exact physical step. Completing another card (a reviewer approving a
card in review) is not gated.

C. In the Desktop backend, `register()` also registers `core.needs_attention` with the fork core's
`kanban_session_activity.register_done_check` (see `core/kanban-false-done-attention.md`): a chat whose card
finished in the last 24 h with such a handoff and **no** follow-up card (created by / linked to it, not archived,
or named) stripes amber instead of green. `honest-done: ack` in a comment on the card clears it (the human accepted
the narrower result). Older cores without the registry just skip C.

Classifier: titles are request context, not evidence of unfinished work; only the current handoff is classified.
Per sentence, quoted text removed, questions skipped; negations ("no follow-ups", "nothing deferred",
"deferred at X's request", "deployment is deferred") don't count; follow-up commits/PRs are finished work. Patterns
are `DROP_PATTERNS` in `core.py`.

## Gates H and I: a consent stop survives delegation, and intent is not delivery

Why (defect kanban-6cf8e9e3fd, 2026-11-12): a chat's comment on another gateway's card hit an approval prompt that
timed out. The chat then ordered a worker card to post the same comment (the delegated request carried
action/target/text, not the stop). That worker completed saying the tests "were posted on t_…", and filed the
operator request 13 s later, after completion, where the `stale_run` fence correctly refused it. Nothing was
posted, and the chat relayed the false claim.

H records a stop only from an approval result's own status field (`error`/`message`, or the whole result)
starting `BLOCKED:` and saying the user has NOT consented / silence is not consent (or `user_consent: false`), and
only for card ids the blocked call named. Technical refusals (`stale_run`, `foreign_card`, guard refusals, a
notifier that could not send) are never a human veto; quoted stop text in ordinary output is not a stop. Stops
live in `$HERMES_HOME/cache/kanban-honest-done/consent-stops.json` (24 h TTL) and clear on the chat's next real
user message (`pre_llm_call` with canonical user content; notice turns do not clear). Limit: a worker on another
host does not see its origin chat's stops; the parent's delegation is refused at the source instead.

I treats a past-tense, non-negated, unquoted delivery sentence naming another card as a claim. It holds when a
readable board (live files and `kanban/sync-shadow` replicas) has a comment on that card written since this card
was created, a `supervisor_requests` row from this card naming it with status `done` (filed before completion),
or the card was created by / links to this card. `pending`/`accepted` means queued, not delivered (the refusal
gives the honest wording, `requested … (supervisor request #N, pending); not yet delivered`); `rejected`/`failed`
is a failure to report. Replay on worker-6's 278 recent runs: 3 claims, all verified, 0 refused.

Telemetry (agent-owned, never the Inbox): `$HERMES_HOME/logs/kanban-honest-done.jsonl` lines
`consent_stop_recorded`, `consent_relay_refused`, `consent_stop_cleared`. `tests/test_delivery_and_consent.py`.

## Gate G: failed thresholds stay failed

Why: three red-sweep rows (slug `acceptance-waiver-owner-decision`, 2026-11-12) were cards whose fixed threshold
failed (an energy filter where no source passed checks 1 and 4; an oracle bar of 800 against 807.35; a
25%-free disk target against 6.1%). Each next step on offer was a waiver or a test chosen after the result, and one
was applied as a red-sweep guess. Gate F guards the body; G guards the handoff.

Card template (the decision is made before the result is known): state each threshold in the body, and for each
add a preregistered failure branch line `IF FAIL <check>: <next step>` (`IF FAIL:` alone covers every check), for
example `IF FAIL check 4: report FAIL with the measured share, keep the source out of the next phase, file a decision card`.
Get the branches reviewed before execution. A branch decides what happens next; it never waives or passes the
check, and a waiver-shaped branch is ignored. Changing a test or target needs David's explicit answer, recorded
in the body through Gate F (`ACCEPTANCE AMENDED (user message <id>): …`).

Structured report, `metadata.acceptance_results`: a list of
`{"check", "status": "pass|fail|not_run|waived", "threshold", "comparator": "< <= > >= ==", "observed", "branch"}`
(or a `{check: {...}}` dict). Refused: `pass` with no `observed`, with a numeric `observed` that contradicts
`comparator threshold`, or in other units than the threshold; `fail`/`not_run` whose `branch` does not cite a body
`IF FAIL` line covering that check; `waived` unless a body waiver line names the check; any other status.

Prose (summary + all metadata text except `contract_waiver`; quoted text and questions skipped): a sentence that
reports a failed / missed / unmet check and waives or accepts it ("check 4 failed; waived as an artefact",
"accepted the failure"), or a pass by judgment ("treated as a pass", "counts as a pass", "close enough",
"effectively passes", a failed check "within noise"). Negation that governs the hit ("no waiver", "never waived",
"did not … or waive") passes. The waiver sentence passes when the body already records a human waiver; a guessed
pass never does.

Replay (2026-11-12, worker replica of the default/bb/hermes/askeve boards, completed runs of the last 14 days):
5268 handoffs, 2 refused: the energy card's real handoff ("All three sources are admitted to the next phase by a phase-only
waiver", the incident) and one false positive describing a fixed defect ("They exited rc 5 and counted as
passing, and the runner now runs them explicitly"; quote the defect's words to pass).

Limits: only the worker's own card and only what reaches the handoff; a test or threshold changed in a repo
(not the card) is not seen here, and nothing judges whether a preregistered branch is wise. `tests/test_threshold_waiver.py`.

## Replay

```
python3 replay.py --days 14 [--json out.json] [--task t_xxxxxxxx]
```

Read-only over every board: completed cards whose handoff admits dropped work, and which of those have no
follow-up card. On the author's boards (Oct 2026, 14 days): 1345 completed cards, 140 admitted dropped work, 37 had
no follow-up card; spot-check precision of those 37 was about 85 % (misses: domain uses like "trust stays
unverified", scoping notes like "the other test files were not run").

## Tests

Shell write classification grants data exemptions only when the **whole input** is
in the bounded grammar G in `shell_commands.py`:

- Words: unquoted `[A-Za-z0-9_./:=@%+,-]`, single-quoted strings, or double-quoted
  strings without dollar signs, backticks or backslashes.
- Lists/pipelines: `;`, `&&`, `||`, `|`, newline; every command must qualify.
- Input redirection: `<` with a literal operand; `<<'D'` / `<<"D"` with a
  verbatim body ending at a physical delimiter line, or an unquoted delimiter
  whose body has no dollar sign, backtick or backslash. No fd prefixes/duplication,
  here-strings, tab-stripping or mixed-quoted delimiters.
- Commands: literal `hermes` (a call), `echo`, `printf`, `cat`, `grep`, `head`,
  `tail`, `wc`, `cd`; or `git commit` with only `-m <word>`, `-a`, `--amend`,
  `--allow-empty`, `-q`, `--no-edit`. No leading assignments or wrappers.
- `printf` is output-only: optional `--`, then a non-option format using only
  output conversions (`diouxXfFeEgGaAcbsq` or `%%`, with width/precision/flags).
  `-v`, `%n`, unknown conversions, and decorated variable-writing conversions
  are outside G. Any output redirection (`>` or `>>`) anywhere revokes G: even
  an isolated write can poison a previously harmless executable for this or a
  later invocation. This includes writes to apparent notes files; no pathname
  allowlist or filesystem provenance is assumed. Output characters inside quoted
  data or heredoc bodies are not operators. Input redirection combined with
  `git commit` also remains outside G.

`tests/test_output_poisoning.py` proves echo/printf/heredoc overwrite and append
against initially harmless fixture executables, through real Bash and the actual
hook. It also covers reversed-order and pipeline composition, isolated writes,
and quoted-output-character controls. Four former output-to-notes/definitions
controls now conservatively detect mentions, while plain quoted heredocs remain
exempt.

Inside G, other commands' arguments/input are data. Outside G, **no data exemption
is possible**: the old any-position token scan applies to the entire string, with
additional conservative nested-string and continuation readings preserving the
previous nested-call detections. Unknown constructs never become an exemption.
This deliberately flags even inert `bash /dev/null -c 'hermes kanban …'`, unrelated
function definitions, `/bin/echo`, and printed substitutions. Plain echo/printf,
grep, restricted Git messages and quoted heredoc bodies remain data.

Consent is different from the argv-based gates: outside G it retains main's raw
`_SHELL_WRITE_RE` over joined command/input/data, including the full target text.
It must not rely on nested segment discovery for hub requests. Consent also retains
that raw detector inside G whenever any command starts with `hermes` (including chat
prompts and heredoc stdin). Only command-only `terminal` / `run_in_user_terminal`
inputs can receive inert-data exemptions: process input/data may feed an existing
interpreter, so joined fields and process tools retain the raw main detector.
`tests/test_consent_execution_scope.py` verifies these boundaries and stopped-target refusals. The regression
`tests/test_consent_raw_parity.py` covers shell/eval wrappers, here-strings, pipes,
substituted command names, and SSH (classification only), plus inert controls and
an independent frozen-main-regex comparison across the test-source corpus.
Runnable hub probes use only a fixture fake and the allowlisted environment.

`tests/test_command_position.py` covers every consumer and the real comment hook,
including prior nested execution controls. `tests/test_inert_grammar.py` adds the
review redefinition/continuation/output-process repros and 200 distinct whole-input
boundary strings with independently declared membership. Runnable controls use a
fake `hermes` and allowlisted environment, including availability probes and the
positive startup control (only the fixture's `BASH_ENV` is added). No login shells;
unavailable tools are recorded skips. This is static hook feedback, not an OS
sandbox: ambient executable definitions, PATH contents, Git hooks, sourced files
and computed command values are not resolved.

`tests/test_acceptance_provenance_real_shapes.py` replays real state.db shapes (msg 931890 notice, peer-mail, exact steer, relays, stale and wrong-chat rows); `tests/test_acceptance_authority.py` (gate F) replays the t_7e2ee45c 2026-11-10 shape: a prepared waiver body applied with `f=…; hermes kanban edit <id> --body "$(cat $f)"` from a chat with no user turn, quoting a real user message of another chat, is refused; so are uncited waivers, notices, assistant text, worker prompts, missing ids, a quote older than the current body, a delegated parent's turn, dropping the DONE sentence and an unknown session. A fresh user turn in the same chat or its compression ancestor passes, as do restoring the original over a waiver and edits that keep acceptance. Paths: terminal (quoted and unquoted substitution), `mcp__terminal`, execute_code, tool_call, and the registered `pre_tool_call` hook with the `session_id` kwarg.

`tests/test_title_context.py` covers the exact deferred-cleanup defect title: completed handoffs pass,
while real summary/nested-metadata admissions are refused through the tool, tool wrapper, terminal, and
triple-quoted execute_code paths. It also checks explicit title overrides and sidebar attention.

`tests/test_escaped_execute_code.py` covers the same summary and nested-metadata admissions, and a clean
handoff, through `terminal({command!r})` and triple-quoted strings (including a multi-line literal and
implicit concatenation). Parsing does not execute the snippet. A syntax error still decodes quote-delimited
literals. Own-card edits stay refused, including when the edit command is `repr`-escaped.

Replay with metadata, "not verified" and Gate D (hub, Oct 28 2026, 14 days, 399 completed cards): B would refuse 15
(was 2; the new ones are real admissions such as "CI not yet checked", about 2 false); D would refuse 16, every one a
card naming an origin chat that was never linked to it (1 is a self-heal write-up that only describes the chat).

Why D: a card whose plan said "report to origin chat @session:…" finished with that chat named only in prose. The
done ping went to the supervisor chat, and the origin chat kept saying "waiting on CI" for 14 h.

```
cd plugins/kanban-honest-done && uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
```
