# Design review: <slug>

## Snapshot

- Spec: `<path>`
- Spec sha256: `<sha256sum output>`
- Source revision: `<git rev-parse HEAD>` (repo `<path>`)
- Pass: initial | re-review (1 of max 1)
- Review status: complete | incomplete
- Isolation: conversation-isolated subagents (not an OS sandbox; inherit global instructions/skills)

## Reviewer status

| Stage | Lens | Reviewer id | Model | Inputs given | Status |
|---|---|---|---|---|---|
| 1 | Simplicity | | | spec, reading list, rubric, neutral task | completed / retried / not_run |

## Findings

| ID | Reviewer | Severity | Spec location | Source location | Evidence | Consequence | Disposition |
|---|---|---|---|---|---|---|---|
| | | blocker / major / nit | heading | path:symbol | what was observed | what breaks | fix / reject / accepted-risk / ask-user / open |

## Refutation

| Finding | Refuter id | Verdict | Quote / location |
|---|---|---|---|

## Disposition log

Rejected and accepted-risk findings with reasons. Fixes applied, with the spec heading they changed.

## Remaining uncertainty

Open findings, reviewers not run, claims nobody could verify, and anything this review did not cover
(e.g. no behavioral tests, no code review).
