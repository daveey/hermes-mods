# Upstream source and attribution

This skill is a derivative work of **Elephant-Goldfish Model (EGM)**,
https://github.com/google/elephant-goldfish-model, pinned at commit
`403d8881d62022490637d740657b33f01e321ea8`, licensed under the Apache License, Version 2.0
(full text: [LICENSE.apache-2.0.txt](LICENSE.apache-2.0.txt)). Upstream notice: "Copyright 2026 Google LLC.
SPDX-License-Identifier: Apache-2.0". The upstream tree at that commit has no NOTICE file. EGM is not an officially supported Google
product, and this adaptation is not affiliated with or endorsed by Google.

The files in this skill directory (`SKILL.md`, `references/`, `templates/`, `tests/`) are distributed under
Apache-2.0, not the MIT license that covers the rest of the hermes-mods repository.

## Upstream files read

- `README.md`, `docs/designs/architecture.md`
- `skills/egm-review/SKILL.md`, `skills/egm-review-loop/SKILL.md`, `skills/egm-drift-report/SKILL.md`
- `agents/zoom_out.md`, `agents/comprehension.md`, `agents/design_critic.md`,
  `agents/implementation_readiness.md`, `agents/codebase_details.md`, `agents/structural.md`
- `resources/document_standards/zoom-out-standard.md`, `feature-spec-standard.md`,
  `implementation-readiness-standard.md`

## What was kept

- The elephant/goldfish split: the author holds context; reviewers see only the document and cited source.
- The five-stage order (zoom out, comprehension, design critique, zoom-out recheck, implementation readiness).
- Paraphrase-first comprehension, the zero-decision readiness rule, the alternatives rule, "proposed files are
  not findings", stable finding IDs, and "a failed reviewer is retried once, then recorded as not run, never as
  zero findings".
- Settled vs. fixed findings: a rejected finding is not reopened without new evidence; a finding that returns
  after a fix stays open.
- Artifact-first reports and on-demand drift comparison.

## What changed (Apache-2.0 section 4(b) notice)

All text was rewritten for Hermes; no upstream file is copied verbatim except the license.
- Seven skills and nine personas collapsed into one skill plus one rubric file.
- Harness-specific subagent tools replaced with Hermes `delegate_task`, dispatched flat by the main agent
  (no synthesizer subagent, no nested delegation).
- Per-stage interactive gates replaced by one consolidated run; the user is asked only about material
  trade-offs, accepted risks and final approval.
- Upstream's loop (two consecutive clean rounds, cap 5) replaced by an initial pass plus at most one
  remediation/review cycle, ending with explicit open findings.
- Added: explicit allowed-input set per reviewer, isolation-boundary disclosure, refutation pass for
  consequential findings, model pinning for design/refutation reviewers, spec digest and source revision
  in every report, citations by heading instead of line ranges.
- Drift is on demand only (upstream suggests periodic runs); no scheduled job.
- Brainstorm, draft, bootstrap and the structural/style personas were not adapted.
