# Unattended runs (cron `kanban-red-sweep`, hub, every 30 min)

The cron's monitor is `red_cards.py list`. The agent wakes when any of these change: fleet cards, blocked events,
decision-session open items, or explicit coverage failures. The monitor's output is sorted and leaves out read
timestamps, so asks that haven't changed cost nothing. Interactive and unattended runs use the same inventory
and classifications.

Before accepting a monitor deployment, run its actual wrapper and check that a remote card and a decision
session appear; passing fixture tests do not prove the deployment works. Cron `monitor`/`script` paths must be a
bare filename under `~/.hermes/scripts/`. So the job points at the thin wrapper `kanban_red_monitor.py` there,
which execs the skill script.

- Follow steps 1-3 and 5, and skip step 4, because a cron run can't show a form. A card you are really unsure
  about stays blocked, since its ask is already in the user's For-you queue. Log it `--action left-blocked`,
  don't comment on it again unless its state changed, and don't re-link it to a chat.
- Read state first and record it last with recurring-job-state (`job_state.py show|record kanban-red-sweep`).
- If the cron is overdue with no run, a core-update drain (`~/.hermes/core-update/drain-hold`) may be holding
  ALL cron dispatch, not only card starts. Deferred jobs fire once on release. Don't cut the drain short; note
  the overdue count on the card that owns the drain. If the cron is instead parked until a date days away, the
  cron route's quota is out (claude-usage-diagnostics §3c).
- Final response: only the decisions you guessed (card title, pick, how to reverse it) and the cards still
  waiting on the user, in a few lines. If nothing was cleared, guessed or newly waiting, reply `[SILENT]`.
