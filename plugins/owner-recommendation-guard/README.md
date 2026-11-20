# owner-recommendation-guard

ask-gate's `owner_recommendation_hold` for hosts that don't run ask-gate, such as Kanban pool workers.

A `kanban_block`, or a `kanban_create` with `initial_status="blocked"`, whose `human_request` hands David a pick
between options that stays inside the card's limits is refused. Picks that stay inside the limits name no real
money, credential or write/admin permission, other people, safety or hard-to-reverse effect, threshold change,
human-only step, or rule David stated. The refusal tells the agent to apply its recommendation and comment how to
reverse it.

- **Same rule, one source.** The classifier is `plugins/ask-gate/owner_recommendation.py`, loaded by path. This
  plugin adds only the hook and the per-card hold bound (2 holds per session, tool and blocked card, then the
  ask passes).
- **No model call.** ask-gate also judges every ask and worker handoff with an aux model. That is too heavy to
  turn on for every worker, so only this deterministic rule runs fleet-wide.
- **No double hold.** When ask-gate is loaded, enabled and error-free in the same process, it applies the hold
  itself and this guard returns nothing. Set `OWNER_RECOMMENDATION_GUARD_IGNORE_ASK_GATE=1` to force the guard on.
- **Fails open.** If the rule file is missing or raises, the call goes through and the guard logs
  `owner_recommendation_error`.

Log: `$HERMES_HOME/logs/owner-recommendation-guard.jsonl` (`owner_recommendation_hold`, `_released`, `_error`).

## Unblock-card brief

The guard also runs ask-gate's `unblock_card_quality.py` (same file, loaded by path) after the owner-recommendation
rule: a blocked card without `Tried:` / `Situation:` / `Options:` / `Recommend:` / one `(recommended)` choice is
refused with a pointer to the try-to-unblock and generate-unblock-card skills. Same bound (2 holds per session,
tool and card), same fail-open, same step-aside when ask-gate is active. Log events `unblock_card_hold`,
`_released`, `_error`.
