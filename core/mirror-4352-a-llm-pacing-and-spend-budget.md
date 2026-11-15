# Kanban LLM quota pacing governor and per-card spend budget

Mirror snapshot 4352; ordinals 4210, 4212, 4229.

Entries:

- **4210**: kanban: LLM quota pacing governor holds claims when the pool is ahead of pace
- **4212**: fix(kanban): spend budget sends a card that keeps spending without a hand-off to triage
- **4229**: kanban: pacing holds are recorded skips; an idle account's null five_hour counts as a fresh window

## Behavior

- **Pacing governor** (4210): before a card is claimed the dispatcher can hold it when the account pool for its route is spending ahead of a sustainable pace. Pool factor per window kind (five-hour, seven-day) is sustainable rate over measured burn; below 1 the pool is paced, and this host keeps only a share of its recent average concurrency running (half for review-lane, negative-priority and scheduled-wake cards). Accounts past reserve thresholds are left to chats, cron and notices. Mandatory pins, unknown routes and ungoverned providers always start; any read/resolve fault fails open. `kanban.llm_pacing.enabled: false` restores the old behaviour. A held card stays ready and unclaimed with `llm_pacing` as the clock-held reason and an `llm_paced` event.
- **Follow-ups** (4229): a pacing hold is recorded as one `dispatch_skipped {reason: llm_paced}` per episode, so queue-position and chat-wait notices and `kanban diagnostics` (`held_in_ready`, hold_reason `llm_paced`) explain it; an idle account with a null `five_hour` window counts as a fresh full window rather than adding zero sustainable rate. `kanban.llm_pacing.*` defaults are registered through an import-free defaults module and documented.
- **Spend budget** (4212): a card that keeps spending runs without handing work on is sent to triage. Spending runs (closed, at least 60 s) since the last hand-off (completed / review_requested / changes_requested) or last budget triage are counted along with reported tokens; at `kanban.spend_budget_runs` (default 15) or `kanban.spend_budget_cache_read_tokens` (default 30M; 0 disables) the card moves to triage with a `spend_budget_triaged` event and a comment. Workers book session token usage on the run's `worker_result` receipt on whichever host ran it.

## How it works

- `hermes_cli/kanban_llm_pacing.py` is the pure pool math; `kanban_llm_pacing_dispatch.py` resolves the route read-only (`resolve_dispatch_tier`), reads `/api/oauth/usage` at most every `read_interval_seconds` per home and persists samples and the host's running count in `<kanban home>/llm_pacing.json`; `kanban_db_dispatch.py` has the single hook in `_dispatch_lane_task`. Only the dispatcher imports the governor.
- `kanban_dispatch_skip.py`, `kanban_db_dispatch_stuck.py`, `kanban_diagnostics.py` render the recorded skip; `kanban_pacing_defaults.py` and `config_defaults.py` hold defaults.
- `kanban_spend_budget.py` is called from `lane_admission_refused` after the no-progress hold and before any claim, deciding again under the write lock; `kanban_db_dispatch_admission.py`, `kanban_dispatch_skip.py` and `gateway/session_kanban.py` report it as `spend_budget:triage`. `website/docs/user-guide/features/kanban.md` documents both.

## Code

Shipped non-test paths (union over these entries):

- `gateway/session_kanban.py`
- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_admission.py`
- `hermes_cli/kanban_db_dispatch_stuck.py`
- `hermes_cli/kanban_diagnostics.py`
- `hermes_cli/kanban_dispatch_skip.py`
- `hermes_cli/kanban_llm_pacing.py`
- `hermes_cli/kanban_llm_pacing_dispatch.py`
- `hermes_cli/kanban_pacing_defaults.py`
- `hermes_cli/kanban_spend_budget.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_llm_pacing.py`
- `tests/hermes_cli/test_kanban_llm_pacing_followups.py`
- `tests/hermes_cli/test_kanban_spend_budget.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Defaults can slow dispatch across a fleet; set `kanban.llm_pacing.enabled: false` to disable. The spend budget sends cards over the limit to triage on their next ready tick, including existing long-running cards.
- Usage numbers come from a read-only provider endpoint; failures fail open, so pacing can silently not apply.
- Promoting a triaged card opens a new spend window.

## Plugin?

None.
