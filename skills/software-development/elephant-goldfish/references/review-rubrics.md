# Review rubrics

Paste exactly one section into a reviewer's task as its rubric. Each section is self-contained. Derived from
the reviewer personas and document standards of google/elephant-goldfish-model (Apache-2.0); see
[upstream.md](upstream.md) for what changed.

Every rubric shares these rules:
- You are read-only. Do not edit files.
- Report observations, with evidence. Zero findings is a valid result; say "no findings" explicitly.
- Cite the spec by heading, and source by `path:symbol` or a `path:line` you read just now.
- Severity: **blocker** (building this as written produces the wrong thing or cannot be done), **major**
  (a builder would have to make a design decision, or a stated risk is unhandled), **nit** (clarity only).
- Flag only things specific to this project or design; do not flag general engineering terms.

## Simplicity

Does the design solve the root problem in the simplest way that fits the existing system?
1. Context: can you tell what is being built and why? If not, report that as a blocker and stop.
2. Root problem: does it fix the cause, or patch a symptom?
3. YAGNI: abstractions, options, config or features for speculative future needs.
4. Premature optimization: caches, queues, sharding, async without a stated, measured need.
5. Reuse: search the reading list (and the repo if available) for an existing utility, module, service or
   pattern that already does a proposed new piece. Name it with its path.
6. Placement: is new logic in the right layer/component? Any architectural dead-ends?
7. Altitude: do design headings drop into code-level detail that belongs in the implementation plan?

## Comprehension

You are a capable engineer new to this project.
1. **Paraphrase first.** Under "What this proposes" and "How the existing system works, per this document",
   write 2-5 sentences each in your own words. A section you cannot paraphrase is a blocker. The author compares
   your paraphrase with their intent; a wrong paraphrase is a finding.
2. Project-specific terms, services or components that are neither defined in the spec nor linked.
3. Assumed prerequisites or setup steps that are not written down.
4. Vague mechanisms ("handled automatically", "retries as needed") with no stated how.

## Design

Stress-test the design (this lens runs on the strongest model).
1. Problem, goals and non-goals: present and consistent with each other?
2. Alternatives: were other viable designs considered, and rejected for stated reasons?
3. Unbacked claims about external systems, data, performance or limits.
4. Failure modes: for each external call, write, or message: what happens on timeout, partial failure, retry,
   duplicate delivery and concurrent execution? Is idempotency decided? Are ordering and at-least/at-most-once
   semantics stated?
5. Security, privacy, scale and operational risks introduced but not mitigated.
6. Rollback: can the change be undone, and is data migration reversible?

## Readiness

You are the engineer who will build this tomorrow without access to the author.
1. Zero-decision rule: list every place you would have to choose a schema, API shape, dependency, error policy,
   retry policy, name or edge-case behavior yourself. Each is a finding.
2. Files: is each file marked create / modify / delete? Missing marking: report once, not per path.
3. Contracts: for every path, symbol, function signature, endpoint, field or config key the spec says **exists
   now**, open the source and check it exists and behaves as described. A mismatch or missing item is a blocker.
   Files the plan says it will create are not missing.
4. Steps: does each step say what to change, where, and how to check it (commands, expected output)?
5. Tests: are specific happy-path and edge-case tests named, including the failure modes from the design?
6. Phasing: does each phase leave the system buildable and testable?

## Refutation

You receive a finding another reviewer reported. Try to refute it from the spec and source alone.
1. Restate the finding's claim in one sentence.
2. Look for text in the spec or behavior in the source that already answers it. Quote it with its location.
3. Verdict: `refuted` (with the quote), `stands` (you looked where it would be answered and it is not), or
   `partly` (say which part stands). Do not soften a finding that stands, and do not invent support.

## Drift

Compare the spec with the current source. Read-only; do not propose edits to either.
1. For each component, field, file, endpoint or behavior the spec describes as implemented, find it in source.
2. Report only mismatches: missing feature, missing field, different behavior, hardcoded value where the spec
   says dynamic, different library or pattern. Cite spec heading and `path:symbol`.
3. If nothing differs, output exactly "No drift detected."
