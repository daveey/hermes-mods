# Refused-send subscriptions are notice-only

Patches: **2221**.

## Behavior

A chat subscribed after a worker's cross-session send is refused still receives the card's completion notice, but does not acquire responsibility for that card. Such subscriptions do not create derived waiting outcomes, held-chat obligations or Board shepherd links, and are not inherited by children.

## How it works

`send_refused` joins `touch` in `UNCOUNTED_SUB_REASONS`. Session activity queries and held-chat selection exclude those proven notice-only reasons. Subscription merging preserves any existing counted reason, including legacy unstamped ownership, instead of downgrading it to a notice destination.

## Code

- `hermes_cli/kanban_db_notify.py`: reason sets and merge precedence.
- `hermes_cli/kanban_session_activity.py`: counted subscription filtering.
- `hermes_cli/kanban_watch.py`: held-chat filtering.

## Tests

Source coverage, **not run in this mirror**: `tests/hermes_state/test_session_outcome_send_refused.py` covers live/archived chat outcomes, ownership controls, notice delivery, subscription upgrades, child inheritance, held chats and Board chat links.

## Applying

Use the [README full replay procedure](README.md#applying); surrounding notification and outcome machinery is required.

## Gotchas

A refused send does not release a pre-existing shepherd/create/origin obligation. The subscription row remains, preserving completion delivery and unseen counts. This patch changes how a subscription is counted, not whether cross-session sends are permitted.

## Plugin

Core subscription semantics. A plugin may create the notice subscription, but no plugin is needed for this counting fix.
