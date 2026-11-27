# Spend review, decided-triage repair and spend-stop watch

**Scope:** Delayed caused-stop review, standard-class routing, notices, watch CLI, malformed-stop isolation, bounded request scan.

**Source ordinals:** 6417, 6418, 6419, 6420, 6421, 6422, 6423, 6424, 6425, 6426, 6427, 6428, 6429, 6430, 6431, 6432, 6433, 6434, 6435, 6436

## Behavior

- New ordinary spend stops still queue immediately. Already-routed stops receive backlog review after a configurable delay; scope/capability stops require that delayed path. Grants recheck fences, and caused verdicts use immutable Board requests with authenticated readback; safety and unknown causes stay excluded.
- Review routes through the managed standard subscription class and escalates without a paid call when policy is unavailable.
- Decided triage repair finishes recorded decisions without granting spend; budget edits never release capability/scope/safety stops.
- A model-free watch CLI files one card per stale stop lacking an active reviewer hold, a granted/settled review or a live decision route; self-stopped cards get distinct notice labels in all locale files.
- Malformed stop causes are contained per card; request journals close ended requests and the due scan reads only open ones.

## How it works

- Modules: `kanban_spend_reviewer.py`, `kanban_spend_caused_review.py`, `kanban_spend_review_request.py`, `kanban_decided_triage.py`, `kanban_spend_stop_watch.py`, `kanban_spend_decision.py`, `kanban_hold_notices.py`, `kanban_spend_budget.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_decided_triage.py`
- `hermes_cli/kanban_hold_notices.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_caused_review.py`
- `hermes_cli/kanban_spend_decision.py`
- `hermes_cli/kanban_spend_review_request.py`
- `hermes_cli/kanban_spend_reviewer.py`
- `hermes_cli/kanban_spend_stop_watch.py`
- `hermes_cli/kanban_stranded_park.py`
- `hermes_cli/kanban_triage_sweep.py`
- `locales/af.yaml`
- `locales/ar.yaml`
- `locales/de.yaml`
- `locales/en.yaml`
- `locales/es.yaml`
- `locales/fr.yaml`
- `locales/ga.yaml`
- `locales/hu.yaml`
- `locales/it.yaml`
- `locales/ja.yaml`
- `locales/ko.yaml`
- `locales/pt.yaml`
- `locales/ru.yaml`
- `locales/tr.yaml`
- `locales/uk.yaml`
- `locales/zh-hant.yaml`
- `locales/zh.yaml`
- `website/docs/user-guide/features/kanban.md`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_kanban_caused_stop_label.py`
- `tests/hermes_cli/test_kanban_decided_triage.py`
- `tests/hermes_cli/test_kanban_decided_triage_comments.py`
- `tests/hermes_cli/test_kanban_hold_failure_isolation.py`
- `tests/hermes_cli/test_kanban_hold_notices.py`
- `tests/hermes_cli/test_kanban_hold_revisions.py`
- `tests/hermes_cli/test_kanban_spend_caused_review.py`
- `tests/hermes_cli/test_kanban_spend_maintenance_isolation.py`
- `tests/hermes_cli/test_kanban_spend_malformed_dispatch.py`
- `tests/hermes_cli/test_kanban_spend_reconcile_bound.py`
- `tests/hermes_cli/test_kanban_spend_review_delay.py`
- `tests/hermes_cli/test_kanban_spend_review_due_cost.py`
- `tests/hermes_cli/test_kanban_spend_review_fences.py`
- `tests/hermes_cli/test_kanban_spend_review_http.py`
- `tests/hermes_cli/test_kanban_spend_review_reconcile.py`
- `tests/hermes_cli/test_kanban_spend_reviewer.py`
- `tests/hermes_cli/test_kanban_spend_stop_watch.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6417-6436, 20 commits: 6417, 6418, 6419, 6420, 6421, 6422, 6423, 6424, 6425, 6426, 6427, 6428, 6429, 6430, 6431, 6432, 6433, 6434, 6435, 6436) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- No local scope renewal authority is added.
- Strict episode reads are used by automatic deciders; dispatch readers stay non-raising.
- Replacement-card validation treats one sentence-ending '.' or ':' after the id as prose; longer coordinates (suffixes, prefixes) still refuse.
- The watch files one default-assignee card per home triage episode older than thirty minutes with no active reviewer hold, no granted/settled review and no live decision route (a live chat notice counts as a route). An escalated review, disabled reviewer or excluded cause does not by itself suppress filing. Watchdog and budget-decision cards are skipped, and it is meant for script-only cron use that this mirror does not install.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Spend gates sit in dispatcher/budget code.
