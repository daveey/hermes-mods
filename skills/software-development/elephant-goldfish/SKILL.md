---
name: elephant-goldfish
description: "Use when reviewing a plan or design pre-build. Fresh agents."
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
> explicitly if you have none. Do not edit files. Rubric: `<rubric section text>`.
> Report format: for each finding give severity (blocker/major/nit), spec location, source location if any,
> the evidence you observed, and its consequence if unaddressed.

Prompt to trace and report, never "find the bugs": a directive to find something guarantees a finding.

**Isolation boundary (disclose it in the report):** a fresh subagent has no access to this conversation, but it
is not an OS sandbox. It still inherits global instructions, memory and skills of its profile, and it can read
any file it can reach. Say "conversation-isolated" in the report, never "zero-context" or "sandboxed".

**Models:** architecture, design-assessment and refutation reviewers are pinned to the strongest model under the
current user policy (here: `model: claude-opus-5-5`, `provider: anthropic` on the `tasks[]` entry). Other lenses
may use the default tier.

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
`Refutation` rubric. Keep a finding only if it survives; record the refuter's verdict either way.

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
stages whose scope changed, with fresh reviewers. If findings are still open, stop and report them as open
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

- Passing your rationale "so they understand" defeats the method. If the spec needs it, put it in the spec.
- Line numbers go stale after any edit; cite headings, and re-read before assigning a location.
- A reviewer citing a path that does not exist is itself a finding about the spec only if the spec claims the
  path exists now; files the plan says it will create are not missing.
- Upstream EGM runs seven skills, per-stage human gates and a two-clean-round loop. This adaptation is one
  skill, a consolidated run and a bounded cycle on purpose; don't re-add the gates.
- Small clear fixes do not need this pipeline.
- Paths are part of the prompt: a directory named `flawed/`, `expected/` or `mismatch/` tells the reviewer what
  to find. Keep spec, fixture and source paths neutral.
- "Read-only" is an instruction, not enforcement: reviewers may execute source to probe behavior (useful
  evidence) and leave caches such as `__pycache__/`. Run `git status` on the source after the review and record
  anything they created.
- Reviewers sometimes cite wrong line numbers (seen in a live run: lines 40-46 of a 19-line file). Check each
  cited location before relying on it; keep the finding only on what you could verify.
