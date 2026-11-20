# Chat waits that wake the owning orchestrator

Mirror snapshot 5011; ordinals 4936, 4937, 4938, 4939, 4940, 4941, 4942, 4943, 4944, 4945, 4946, 4947, 4948, 4949.

Entries:

- **4936**: kanban: a park on the card's own orchestrator wakes that chat
- **4937**: kanban awaiting: a chat wait never launders a human ask or agent wall; count only wakeable subs
- **4938**: kanban chat waits: continue asks too, the answer wakes the card, reachable chats only
- **4939**: kanban chat waits: owner-only subscriber check, root session stores, system notes never answer
- **4940**: kanban chat waits: the owner turns an undeliverable synced ask into a visible one
- **4941**: kanban unloaded sweep: an orchestrator ask is not wake-budgeted
- **4942**: kanban chat waits: rewording does not reset escalation; exempt ask wakes are bounded
- **4943**: kanban chat waits: an ask is retried, never written passively; answered asks end the streak
- **4944**: kanban chat waits: a 2 h board deadline and a daily cap end every unanswered ask
- **4945**: kanban chat waits: answers by time and portable uid; the deadline trusts the card's comment probe
- **4946**: kanban chat waits: every chat wait has the 2 h deadline; comment probes recognised however spelled
- **4947**: kanban chat waits: an answer is strictly after the park; drop an unused regex
- **4948**: test(kanban chat waits): answers in tests come after the park second
- **4949**: kanban awaiting: read session stores through connect_tracked

## Behavior

- **Kind `chat`** (4936): a park on the card's own orchestrator records awaiting kind `chat` and posts `decision_requested` in the park transaction (schedule, timed block, and later `kanban_continue`). An `external` wait whose text waits on the owning orchestrator is reclassified, not treated as a vendor wait. No wakeable subscriber → `no_chat_subscriber`.
- **Walls and subscribers** (4937–4939): a chat wait still runs the human/agent text-wall and refuses `human_wait` / `agent_action_wait`. Only TUI and notify+wake / wake rows that the notice can actually wake count. Subscriber check runs only on the owning gateway; a pool worker records the ask and syncs it. Session stores are the root store plus every profile store. `--skip-system` ignores kernel writers (including older names without a `kanban-` prefix).
- **Owner fallback** (4940): if the owner has no wakeable chat, an imported ask leaves the park and becomes a visible `needs_input` with the question.
- **Delivery** (4941–4943): an ask is never written as a passive acked transcript row. If the sweeper cannot wake now, it retries. Rewording does not reset repark escalation (premise is kind + gateway). Answered parks end the streak; the card's own worker comments are not answers.
- **Deadlines** (4944–4947): every chat wait has a 2 h board deadline after the card's comment probe says “not yet” (`escalate_if_overdue`); an answer that beat the deadline wins. Answers use synced `created_at` strictly after the park and portable `writer_run_uid`. Comment probes are recognised however spelled (`nice`, env, absolute path). Six chat parks per card per 24 h escalate even if each was answered “not yet”.
- **Tests / hygiene** (4948–4949): fixtures date answers after the park second. `tui_chat_reachable` opens session stores through `connect_tracked` (untested beyond lock-guard CI for raw sqlite).

## How it works

- `hermes_cli/kanban_awaiting.py`, `kanban_decision_ask.py`, `kanban_timed_park.py`, `kanban_continuation.py`, `kanban_wake_check.py`.
- Unloaded sweeper `tui_gateway/kanban_unloaded.py`; notify fanout; i18n `decision_requested` strings in `locales/*.yaml`.

## Code

Shipped non-test paths (union over these entries):

- `gateway/kanban_wake_fold.py`
- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_cli_park.py`
- `hermes_cli/kanban_comment_origin.py`
- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_db_schedule.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_decision_ask.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_timed_park.py`
- `hermes_cli/kanban_wake_check.py`
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
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/session_notifications.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_decision_ask.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- A chat wait is not a way to park a human ask or an agent wall.
- System/progress notes never count as the orchestrator's answer.
- Deadline applies to chat waits; a wait that named another producer escalates only while unanswered.

## Plugin?

None.
