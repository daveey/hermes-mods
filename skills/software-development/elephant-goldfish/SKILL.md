---
name: elephant-goldfish
description: "Use when a design/plan needs review before building."
triggers:
  asks:
    - "review this plan before I build it"
    - "is this design ready?"
    - "stress-test this spec"
  not_for: "reading a repo or assessing implementation evidence (use codebase-inspection); code diffs (use requesting-code-review)"
version: 1.0.0
author: Hermes Agent (adapted from google/elephant-goldfish-model)
license: Apache-2.0
metadata:
  hermes:
    tags: [design-review, plan-review, implementation-plan, spec, architecture, fresh-context, goldfish, readiness, drift]
    related_skills: [writing-plans, requesting-code-review, github-code-review, plan]
---

# Elephant–Goldfish design review

Review a substantial design or implementation plan **before anyone builds it**, with fresh reviewers that see
only the document and the source it cites. The author (the "elephant") holds the context and writes the spec;
reviewers (the "goldfish") never see the author's chat or reasoning, so they catch what the spec leaves unsaid.
Adapted from [google/elephant-goldfish-model](references/upstream.md) (Apache-2.0).

Typical asks that should land here, even without naming this skill: "review this implementation plan before I
build it", "is this design ready?", "stress-test this spec", "check this plan against the code", "has this design
drifted from the code?".

## When to use / not

Use for: a multi-file feature, a new subsystem, a schema/API/protocol choice, a plan that another agent will
build, or a design whose failure modes (retries, idempotency, migration, rollback) matter.

Skip for: a small clear fix, a one-file change, a typo (use the normal workflow). This is not code review:
diffs go to `requesting-code-review` / `github-code-review`; behavior goes to tests. A clean design review
never replaces either, and never authorizes deployment.

## Inputs

1. **Spec**: one self-contained markdown file. If none exists, write it from [templates/design.md](templates/design.md).
   Separate the high-level design from implementation detail **by headings** (e.g. `## Design` vs
   `## Implementation plan`), never by cached line numbers.
2. **Source reading list**: the files, dirs or docs the spec cites (paths, symbols, contracts). Reviewers read
   these themselves.
3. **Rubric**: the matching section of [references/review-rubrics.md](references/review-rubrics.md).
4. **Neutral task** (below). Nothing else.

The allowed input set for every reviewer is exactly: spec path, reading list, rubric section, neutral task,
and the report path to fill. Do **not** pass the conversation, your conclusions, why you chose the design,
what you expect them to find, or earlier reviewers' findings (except the "current findings" list a refutation
pass must test).

## Procedure

### 1. Set up the review directory

`docs/designs/reviews/<slug>/` in the project (or `$HERMES_KANBAN_WORKSPACE/reviews/<slug>/`, or a scratch dir
when the project has no docs tree). One directory per review; never a shared global report filename. Record
with tools, not from memory:

```bash
git -C <repo> rev-parse HEAD          # source revision (or "not a git repo")
sha256sum <spec.md>                   # spec snapshot digest
```

Copy [templates/review.md](templates/review.md) to `<dir>/review.md` and fill the snapshot block.

### 2. Dispatch fresh reviewers (parent-owned, flat)

You (the main agent) call `delegate_task` directly. **No nested delegation**: reviewers do not spawn
reviewers, and there is no synthesizer subagent; you consolidate. Each task's `goal` is the neutral task;
its `context` is only the allowed input set. Record each reviewer's returned subagent/task id and the exact
inputs you gave it in the report.

Neutral task (fill the brackets; keep the wording neutral):

> Read `<spec path>` under the heading(s) `<scope headings>`, then the source it cites and these files:
> `<reading list>`. Apply the rubric below and report what you observe. Cite each observation by spec heading
> and by source `path:symbol` (or `path:line` read from the file now). Zero findings is a valid result: say so
> explicitly if you have none. Do not edit files. Read only the files listed here and the source they cite; do
> not open other review reports. Rubric: `<rubric section text>`.
> Report format: for each finding give severity (blocker/major/nit), spec location, source location if any,
> the evidence you observed, and its consequence if unaddressed.

Prompt to trace and report, never "find the bugs": a directive to find something guarantees a finding.

**Isolation boundary (disclose it in the report):** a fresh subagent has no access to this conversation, but it
is not an OS sandbox. It still inherits global instructions, memory and skills of its profile, and it can read
any file it can reach. Say "conversation-isolated" in the report, never "zero-context" or "sandboxed".

**Routing:** architecture, design-assessment and refutation reviewers may request `model: class:deep`
on the `tasks[]` entry, with provider omitted and never mandatory. The per-task selector is honored
only when `delegation.allow_task_model` is true (default false); otherwise it is stripped and the child
uses the normal configured delegation/backend route (or parent inheritance when unconfigured).
Do not change that fleet setting just to force a reviewer selector. Other lenses may use `class:standard`,
`class:fast` or no selector as their capability needs warrant. Do not select an exact model/provider for
Kanban or delegate review, including recovery from a quota hold. Independence comes from fresh run/session custody,
neutral inputs and the supported review handoff, not a different model identity; never bypass quota or billing gates.

### 3. Stages

Run in this order. Independent lenses inside one stage may go in one `delegate_task` call (parallel tasks);
a later stage always reads the **current** spec (re-read and re-hash after any edit before dispatching it).

| # | Stage | Rubric section | Scope |
|---|---|---|---|
| 1 | Simplicity / reuse | `Simplicity` | design headings |
| 2 | Comprehension (paraphrase first) | `Comprehension` | whole spec |
| 3 | Design assessment | `Design` | design headings |
| 4 | Simplicity recheck | `Simplicity` | only if stage 2–3 edits changed the design |
| 5 | Implementation readiness | `Readiness` | implementation headings; checks every cited path and contract against source |

Feature spec with no implementation plan yet: run 1–4 and say readiness was not run. Implementation-only
review of an already approved design: run 5 (plus 2 if the plan is new to readers).

**Refutation:** for each blocker/major finding that would change the design (a consequential design claim),
dispatch one independent refutation reviewer with the spec, the reading list, the finding text and the
`Refutation` rubric. Keep a finding only if it survives; record the refuter's verdict either way. Apply the
same capability-only routing to later refutation tasks; `class:deep` is an optional preference, not an
exact-model requirement or proof of independence. Record each fresh reviewer's custody and inputs.

### 4. Collect and record findings

For every finding record: stable ID `<stage>-<reviewer>-<n>` (e.g. `R5-readiness-2`), reviewer id, severity,
spec location (heading), source location, observed evidence, consequence, proposed disposition
(`fix` / `reject` / `accepted-risk` / `ask-user`). Verify each cited source location exists before accepting
the finding; a finding whose evidence you cannot reproduce is marked `unverified`, not silently dropped.

**Failed reviewers:** a reviewer that errors, times out or returns no usable report did **not** review. Retry
it once with the same inputs. If it fails again, set its status to `not_run` and the overall review status to
`incomplete`. A missing report is never a clean review and never "zero findings".

### 5. Remediate (consolidated, not per stage)

Default to one consolidated run: do not stop after each stage to ask the user.

- Fix factual, clarity and missing-detail findings yourself when the fix does not change approved scope.
- Ask the user (one batched question) only for unresolved material trade-offs, accepted risks, or the final
  plan approval.
- Record rejected and accepted-risk findings with the reason. Do not reopen a settled finding unless a reviewer
  brings new evidence. A finding that reappears at the same place after an attempted fix stays **open**: the
  fix did not hold.

### 6. One re-review, then stop

Bound the review to the initial pass plus **one** remediation/review cycle. After editing: re-read the spec,
re-hash it, re-derive locations from the new headings (old line numbers are stale), then re-run only the
stages whose scope changed, with fresh reviewers. A stage's scope changed when any edit touched its scope headings,
even one you judge to be wording or numbers only: do not skip that stage on your own judgement of the edit (a live
run skipped Design after editing `Decisions` and `Security`). If findings are still open, stop and report them as open
with their severities. Never call this "converged" or "two clean rounds" (that is upstream's loop, not this
one). Another pass needs a stated reason (e.g. a new design decision), not a chase for a clean report.

### 7. Hand off

Report: review status (`complete` / `incomplete`), reviewers and their ids, open findings, decisions taken,
accepted risks, path to `review.md`. Keep long content in the file; chat gets the summary and the path.

The approved spec goes to a **new implementer** with only the decided spec and the referenced source (no
review chat, no open questions). Review success does not grant deployment authorization and does not replace
behavioral tests or code review.

## Drift mode (on demand, read-only)

"Has this design drifted from the code?": dispatch one fresh reviewer with the spec, the source it cites and
the `Drift` rubric. It reports supported mismatches (spec says X, `path:symbol` does Y) or "No drift detected".
Do not edit the spec or the code in drift mode, do not rewrite the spec to match a regression, and do not
schedule it: there is no recurring drift job. Report the inputs' digests before and after to show they were
untouched.

## Pitfalls

- For diagnostic CPU partitions, trace the existing envelope before naming a subdivision: reader-open CPU may already be subtracted from a probe counter. Specify activation, nested ownership, exceptional intervals and publication overhead separately. For an `outer=False` mutation test, inspect the accounting sink: an enclosing scope can suppress accidental `outer=True`, masking the mutant. Assert the emitted flag or exercise an eligible depth-zero case with nonzero CPU, alongside enclosing-total invariance tests.

- When an owner freezes scope and excludes a dependency, distinguish an honest partial deliverable from whole-contract acceptance before review. If source demonstrates the excluded dependency can destroy supported results, report the exact path and stop for the scope owner; do not grow another certification subsystem or replay unchanged review cycles. Process/lease zero proves no active work, not delivery of completed results: a direct run-end lease release can succeed while the completion outbox remains pending. Check whether an existing manual preflight actually runs on the autoscaler path and inspects finished rows before treating it as mitigation.

- For change-feed materialization plans, trace every projected column against cursor exclusions and distinguish latest-state invalidation from durable event delivery. Preserve message/edit clocks, dynamic membership, external publications and time deadlines; pure fact subscribers cannot replace leased notification/reconciliation writers. State request complexity as lookup plus output bytes and background complexity as changed inputs plus affected closure/index work. A separate process reduces shared-GIL exposure but must be included in total resource acceptance.
- Passing your rationale "so they understand" defeats the method. If the spec needs it, put it in the spec.
- Include the target repository's root and affected-area AGENTS/contribution rules in the readiness reading list. For observer APIs, identify the concrete production consumer and activation/readback path before calling the slice shippable; real-caller tests alone do not satisfy a repository ban on unconsumed hooks. Keep privacy-reduced diagnostic envelopes separate from lossless replay evidence.
- Line numbers go stale after any edit; cite headings, and re-read before assigning a location.
- A reviewer citing a path that does not exist is itself a finding about the spec only if the spec claims the
  path exists now; files the plan says it will create are not missing.
- Upstream EGM runs seven skills, per-stage human gates and a two-clean-round loop. This adaptation is one
  skill, a consolidated run and a bounded cycle on purpose; don't re-add the gates.
- Small clear fixes do not need this pipeline.
- Reviewers explore: in a live run a refuter opened an earlier `review.md` next to the spec. Keep earlier reports
  out of the reviewed tree where possible, and mark a verdict from a reviewer that read them as not independent.
- Project accounting/provenance evidence before including it in a neutral review packet: retain immutable run/event identities, source custody, closure and actual usage fields, but omit `worker_result.last_output` and transcript text. Those fields can quote earlier review findings even when no review report is included. Preserve full originals outside the allowed review input tree and disclose any incidental exposure.
- Separate mutable execution identity from role provenance when a policy excludes reviewers or other task classes. A current-caller field, review title/key, dependency edge, or completion-result classifier may not prove a fresh task's role; inspect every writer and manual creation path before promising universal exclusion. A proposed immutable owner field still needs a trustworthy initial producer and migration rule.
- Before making an admission observer write lifecycle status, trace the actual row-ingress merge and placement-age rules. A row version does not imply CAS when ingress merges field clocks; scheduled-to-ready may reset home grace. Consider a separate authoritative admission hold that leaves lifecycle intent untouched, and explicitly preserve denied/human-held state through faults, rehome and deletion.
- Before designing an event-backed expiring hold, verify UID creation with sync disabled, pickup filtering after expiry, and history-size limits on evidence consumers. An append API may rely on capture triggers for UIDs; a claim refusal can still cause repeated imports; deleting old events can invalidate completeness-based landing evidence. Check these before adopting an unbounded start/resolution chain.
- Before reusing a human promotion as recovery authority, verify its allowed source statuses and event ordering. A ready/review/scheduled task may not be promotable; a later-arriving or clock-skewed event is not proof of a new decision. Bind disaster-recovery decisions to the current recovery identity, and ensure the decision barrier precedes the notice that invites action.
- Trace deferred stop decisions through remote claim paths before calling UNKNOWN fail-closed. When worker copies skip a home-only guard, leaving the home card runnable indefinitely can turn a bounded replication race into sustained unverified spend; a local no-spawn result is not a fleet stop.
- For conditional scheduler replays, record hypothetical-pending eligibility separately from actual candidate membership: actual claims remove rows, and lazy budget stops leave even still-ready rows unevaluated. Do not re-run state-mutating gates against later real history. Freeze explicit resource-class service tokens or model per-policy capacity debits; a shared eligible boolean cannot represent divergent within-tick profile/pool consumption. Keep private replay outputs non-certifying until complete exporter coverage, pickup/claim semantics, clocks and deployed-policy fidelity are verified. Differential-test caller outer guards as well as extracted evaluators: a zero-budget evaluator invoked with held-copy support may select rows that the real pickup caller never evaluates. Bind frozen constants and test their boundary, and distinguish explicit unresolved assignees from missing evidence before applying profile caps. Distinguish copied locality from returned pickup IDs: post-copy admission denial can leave a local row while removing it from the return list. Bind outcomes to actual apply results, not proposed verdicts, and account for eviction and partial-copy exceptions. Validate asserted effects even across outer early exits; contradictory eviction evidence on a no-pickup path must not silently disappear.
- For repeated scheduler episodes, separate a committed claim from successful launch and later readiness. A launch failure may release/requeue or auto-block; end the claimed episode, require an explicit uniquely identified successor with evidenced clock/locality, and never automatically replay actual-policy retries under a counterfactual policy. Test stale local copies and atomic rejection of reused episode IDs.
- For historical scheduler replays, use exclusive event cuts and assert baseline counts before testing alternatives. Keep run/event joins within one database or use stable UIDs across replicas; integer run IDs are host-local. Distinguish a historical successful wake from current authoritative ownership, and label counterfactual scheduler paths rather than claiming the observed later history would remain unchanged.
- Before adding a busy-chat workload to an existing replay, trace custody deferral before assuming it creates a buffered claim, and trace every existing latency oracle and shared fixture consumer. A waking sibling can prevent the very claim the test intends to measure; blocking one chat also delays the original alternating notice stream, not only newly injected events. Require explicit reachable fixture ordering and separate eligibility accounting without weakening baseline acceptance.
- For receipt-GC replay specs, distinguish highest-seen from contiguous processed prefixes, retain an explicit bounded unknown-verdict/ack window, and classify deferred/cancelled/rejected outcomes separately from applied effects. Test stale-restore seq<prefix responses: without historical digests they prove prefix coverage, not that the submitted payload executed, so they cannot authorize pruning a pending intent.
- For mixed-version protocol rollbacks, a capability probe and unknown-JSON-field checks do not prevent old handlers executing new requests. Require version-discriminated ingress and fence legacy traffic for already-migrated slots too; old code does not understand the new slot modes. Treat erased legacy execution history as genuinely ambiguous, not something a fresh sequence or snapshot can reconstruct.
- `delegate_task` `context` is literal per task: a shorthand like "COMMON" sends reviewers no spec, rubric or input bans, and they then search the workspace (one batch read sibling stage reports). Paste the full allowed input set into every task, and move stage reports out of the workspace before refutation runs.
- Paths are part of the prompt: a directory named `flawed/`, `expected/` or `mismatch/` tells the reviewer what
  to find. Keep spec, fixture and source paths neutral.
- For read-only caches that "borrow" another owner's journal anchor/certificate, check the anchor's revision equals the cache's retained base revision. An anchor proves lineage only at its own revision; advancing a delta base past it reduces safety to stat fences (fork/restore between anchor and base). Require base advance only at anchored revisions. Likewise, when a delta path keeps "private previous output", check whether a shared global (e.g. a last-result written by strict callers too) is the literal carry authority; bind to its identity or fall back.
- "Read-only" is an instruction, not enforcement: reviewers may execute source to probe behavior (useful
  evidence) and leave caches such as `__pycache__/`. Run `git status` on the source after the review and record
  anything they created.
- Refute negative search claims against the original records before treating them as missing authority; literal schema-key searches can miss legacy semantic limits. Use the Refutation rubric's native-record comparison.
- Trace both admission sides before declaring a missing update fence: an advisory drain lease can coexist with a POSIX admission lock held through claim commit and a durable updater hold checked after acquisition. Distinguish launch deadline from whole-operation authority; inspect the hold that bridges swap/restart before adding budgets. For timer maintenance, inspect unit-file refresh as well as enable/start: replacing a unit path can overwrite a /dev/null mask.
- For a memo/skip gate keyed on a revision counter, execute a probe that writes each column the skipped reader selects and check the counter moves; activity counters often exclude "bookkeeping" columns (e.g. in-flight marks) that the skipped path reads. Then measure the store's live commit rate read-only: a whole-file key on a busy store can be correct and still miss most passes.
- Before reusing a flocked reservation as inherited descendant authority, inspect its close path: unlinking on parent close hides a still-locked inode held by a live child. Use a dedicated close-only lease and collect unlocked files under the admission lock; verify parent-exit/child-live behavior with an isolated fork/flock probe. Do not assume all subprocess entrypoints pass through tool dispatch or spawn_owned: TUI RPC exec, adapter transforms and SDK launches can bypass both.
- For a host drain fence, inventory remote command senders and node execution entrypoints separately. A lock in one SSM helper does not serialize direct boto/CLI senders or queued commands. Verify RunShellScript, noninteractive sessions, updater and in-process plugin paths before proposing a shell wrapper; a PATH shim is not a universal admission hook. Count systemd activating jobs and SSH channel submissions as well as processes. Treat unavailable command/session enumeration as UNKNOWN, not an empty queue.
- Before making deletion debt sticky, trace routine replica eviction and GC: a fail-closed ledger can make every normal pickup worker permanently unscalable. Certify immutable preimages against exact live authoritative content (not tombstones), and capture integer-reference-to-UID mappings before child deletion order destroys them. Exercise independent SQL debt triggers through the real caller's UPSERT: outer conflict policy can override trigger `INSERT OR IGNORE`; explicit `ON CONFLICT DO NOTHING` avoids that observed conflict propagation.
- For drain custody, trace artifact bytes separately from metadata acknowledgement: an empty outbox can coexist with a pending blob upload. Check capture gaps and every re-enable path too; positive row versions do not certify an uncaptured later edit. Preserve the existing guarded test-policy root when introducing a passwd/privileged account resolver, and define enforcement activation before rollout so a missing latch cannot mean both legacy and lost state.
- Before keying custody debt from an INSERT's NEW row, trace nested UID-assignment triggers: NEW.uid can remain NULL even after another AFTER INSERT trigger assigns the actual row's UID. Query current identity and test both trigger creation orders; resolve first NULL-to-stable identity without inventing deleted content. Validate historical schemas semantically before using exact within-epoch definition hashes: fresh replay and ALTER-migrated boards can have identical named columns but different SQL/order.
- Bound the outer writer transaction and aggregate retained evidence, not only inner SAVEPOINTs or per-row batches. Routine GC/remote deletes can accumulate immutable preimages faster than discharge; preserving safety by permanently holding scale-in does not protect running completion writes from ledger-induced disk exhaustion.
- For bounded SQLite tracking, measure WAL amplification under a pinned reader: a constant-size revision row still appends pages on every update. Consider level-triggered dirty membership with locked re-read-and-clear, and a sticky saturation taint whose recovery uses a single full read transaction upgraded to a writer. Test WAL BUSY_SNAPSHOT with an intervening commit/ABA and rollback-journal BUSY; never commit the read then publish in a new transaction or clear old pending preimages. A logical row/payload quota is not a physical page/WAL bound.
- For speculative append-and-undo paths, prefer rolling back the append savepoint over deleting a just-created uncaptured row. A preservation trigger's ABORT can otherwise roll back unrelated refusal logs/control outcomes; trace the actual outer transaction and cursor before assigning refusal semantics.
- For custody certification, treat empty/default values as possible real edits, not absence. Bind comparisons and digests to one full-identity snapshot, including later-insert detection. Before crediting incoming writes as authoritative, trace in-transaction local projections/terminal guards; a persisted applying flag can suppress capture while non-Board transformations still run. Distinguish safe UNKNOWN containment from a usable scale-in mechanism when unrelated feed traffic invalidates every certificate.
- For cross-service ownership transfers, distinguish a live concurrency gate from crash fencing: a receipt resolves a lost reply after commit, not a remote commit after the coordinator resets. A short deadline does not cover an early reset. Require a durable pending decision and an authoritative commit/abort race before releasing conflicting registry writes. Trace pre-issued authentication contexts and renewable expired leases too.
- For Board-first server-field rollouts, trace an old consumer dropping unknown fields while advancing its cursor. Adding a column does not refill it; a warm resync may skip unconsumed deletes and acknowledgements by setting cursor to head. Require authenticated field-only refill with actual application checks and unchanged cursor/version. Treat re-emitted server-origin audit rows as potentially laundered gateway events; inspect original retained envelopes, and distinguish wire views that substitute current rows from raw historical snapshots.
- For paged ownership imports, verify actual local application rather than cursor or row-version advance: pending-edit merge rules can advance versions while withholding fields. Define foreign-edge placement and deletion evidence beyond tombstone GC before treating a manifest as complete.
- For exact-script authorization, distinguish invocation binding from Python confinement: unchanged bytes can execute different startup/import/persistent state. Inspect the actual child environment allowlist before accepting claims that required variables are scrubbed, and trace existing live worker custody before adding another identity bridge. Finite deny is an intentional boundary; design review never grants execution consent.
- Verify claimed compare-and-set ownership at the authoritative write, not a client read-check followed by an unconditional release. A helper named safe release may still erase a replacement hold. For expiring interruption authority, add the entire start delay, reclaim batch and apply budget before choosing its lifetime; per-action expiry can otherwise kill work and then forbid the update that justified the interruption.
