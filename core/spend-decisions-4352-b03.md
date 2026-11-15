# Per-card spend limits and durable spend-stop decisions

Patches: **4252, 4316–4317** (4352 snapshot).

## Behavior

A verified human answer reopens a cumulative spend window; agent commentary or a bare unblock does not. Cards can explicitly override board limits, including zero for no limit.

A spend stop remains held until an explicit promotion or archive decision. The stop creates one owning chat-notice route or one supervisor, not both; observer-only links do not establish decision ownership.

## How it works

The newest spend_budget_set event supplies fieldwise overrides; absent fields inherit board defaults and malformed overrides fall back. The spend-budget CLI shows and sets the audited override.

A durable spend-decision producer receipt binds the stop episode. Sweep repair retries producer failures, including after an earlier sweep; checks under the write lock prevent stale decisions from releasing the hold. Gateway and TUI notice renderers include spend and requested disposition.

## Code

- `gateway/kanban_wake_fold.py`
- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_budget_cli.py`
- `hermes_cli/kanban_spend_decision.py`
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
- `tui_gateway/session_notifications.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_spend_budget.py`
- `tests/hermes_cli/test_kanban_spend_budget_hold.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Disabling limits or an automatic status change does not erase a pending decision. A supervisor or observer is not a substitute for the recorded owning decision. CLI and tool coverage are shipped, not freshly executed; translated wording is not validated here.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
