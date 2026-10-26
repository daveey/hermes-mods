# Touch subscriptions do not own work

Patches: 0250.

## Behavior

Commenting, linking or unblocking another chat’s card retains completion delivery without making the touching chat busy or human-blocked. Owners and explicit create/shepherd subscriptions still count.

## How it works

Subscription delivery_metadata.reason distinguishes create, touch and shepherd. Explicit tui notify-subscribe upgrades touch subscriptions; legacy untagged subscriptions count only for the owning chat.

## Code

hermes_cli/kanban.py; hermes_cli/kanban_session_activity.py; tools/kanban_tools.py

## Tests

tests/hermes_cli/test_kanban_notify.py; test_kanban_session_activity.py; test_kanban_session_activity_triage.py; test_kanban_session_attention.py; tests/tools/test_kanban_tools.py

Test files above are shipped regression coverage, not a claim that every suite was rerun. See [mirror-verification.md](mirror-verification.md) for this export’s execution results.

## Applying

Apply the ordered core series through the last listed patch using `git am -3`, starting at the fork/upstream merge-base; these are not independent patches.

## Gotchas

Kernel activity and subscription tests cover these predicates. Actual Desktop rendering is untested in this mirror; subscriptions are not deleted.

## Plugin?

This changes core-owned behavior or its test harness; no disk plugin is required.
