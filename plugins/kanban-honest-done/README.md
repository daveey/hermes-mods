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

## What it does

Inside a dispatcher-spawned worker (`HERMES_KANBAN_TASK` set), a `pre_tool_call` hook refuses (E and F: any session):

| Gate | Refuses | Paths |
|---|---|---|
| A | editing your **own** card's title/body (`--priority`, other cards, comments stay allowed) | `hermes kanban edit <own id, $HERMES_KANBAN_TASK or $HERMES_KANBAN_WORKER_TASK> --title/--body` via `terminal`, `process`, `execute_code`, `tool_call` |
| B | completing your own card when the handoff, meaning the summary **and every text value in its `metadata`** (`not_done`, `remaining`, nested lists), admits dropped, deferred, unrun or unverified work (incl. plain "not verified / not performed / not checked / not confirmed / not tested"), unless the handoff names a follow-up card `t_xxxxxxxx` that exists (any board), is open or done (not archived), and was created by this card (`creator_task_id`) or is linked to it | `kanban_complete` tool (direct or via `tool_call`), `hermes kanban complete <own> --summary/--result/--metadata` via shell or `execute_code`. `execute_code` is not run: shell text is the decoded Python string value (`ast`, so a `repr`-escaped command is the command the shell would see). A command that exists only as a runtime value is not recovered |
| D | completing your own card when its body or comments name an origin chat (`origin … @session:X`, e.g. "Origin chat: @session:default@macbook/X") that is not linked to the card: no unreleased `task_chat_links` row, no notify subscription (`tui` X, or `hub` `<gw>/<profile>/X`), and no `follow-through: SENT … X` comment. The refusal tells the worker to send the chat the result with `cross_session` and record that comment (workers are fenced from `hermes kanban notify-subscribe` on their own board), or `kanban_block` if the send fails. Double-quoted text is skipped | same paths as B |
| E | (any session, not only workers) `kanban_create` whose title/body names an origin chat on **another** gateway (`origin … @session:<profile>@<gw>/<id>`, `<gw>` a known hermes-hub gateway) without `origin_session` (or `HERMES_ORIGIN_SESSION`) binding that chat. The refusal gives the exact `origin_session='…'` to pass, which subscribes the chat so it sees the card and its completion. Fails open when this gateway has no hermes-hub name | `kanban_create` tool (direct or via `tool_call`) |
| F | (any session) `hermes kanban edit <id> --body` that **changes acceptance**: adds waiver text (`ACCEPTANCE AMENDED`, "acceptance waived/withdrawn/…", a `WITHDRAWN:`/`WAIVED:` line, "observation/proof withdrawn") or drops a sentence of the current body that states acceptance (a `DONE` sentence, an "acceptance" sentence; removing an earlier waiver section is a restore and passes), unless the new body cites `user message <id>` (or `msg<id>`) that resolves in `state.db` to a user turn typed in **this** chat (this session or its compression ancestors; not a delegated parent, not another chat), in a local human chat only (session source `desktop`/`tui`/`cli`/`local`; `peer-mail`, kanban, subagent, cron, dispatch/review lanes and any unknown source are refused), with `messages.display_kind` empty (including legacy/UI decision rows that pass the text checks; not proof of exclusive keyboard authorship) or `steer` (a mid-turn `[OUT-OF-BAND USER MESSAGE …]` row, accepted only when it is exactly one envelope), not a runtime/Kanban notice (`[...]`, `⏸ [..]`, `⚠ subagent … was stopped by a restart`, `[Message from another Hermes session …]`, `David pressed …`; any other `display_kind`), and newer than the card's current body (its last `edited` body event, else creation). An unknown calling session fails closed for such edits only. It proves who gave the authority and when, not that the words authorize the change. `acceptance.py` | `terminal`/`process` (literal `--body`, `"$(cat FILE)"`, `$(< FILE)`, unquoted `$(cat FILE)`, variables assigned earlier in the same command), `execute_code` (decoded string literals), `tool_call`. A body built any other way at runtime is not recovered (not refused). The `kanban edit` CLI typed by a human and the dashboard are not hooked |
| G | completing or `kanban_request_review`-ing your **own** card with a handoff that turns a **failed acceptance threshold into a waiver or a guessed pass** (see below). `thresholds.py` | `kanban_complete`, `kanban_request_review` (direct or via `tool_call`), `hermes kanban complete <own>` via shell / `execute_code` |

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
