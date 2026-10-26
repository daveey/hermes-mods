# Default tier provider inference

Patches: 0253.

## Behavior

When kanban.default_provider is unset, a pinned default-tier model uses its inferred provider rather than inheriting an unrelated profile provider.

## How it works

The tier resolver consults the static model catalog and a claude-* fallback. An explicitly configured provider remains authoritative.

## Code

hermes_cli/kanban_model_tier.py

## Tests

tests/hermes_cli/test_kanban_model_tier.py includes test_unset_default_provider_is_inferred_not_the_profiles and updated default expectations.

Test files above are shipped regression coverage, not a claim that every suite was rerun. See [mirror-verification.md](mirror-verification.md) for this export’s execution results.

## Applying

Apply the ordered core series through the last listed patch using `git am -3`, starting at the fork/upstream merge-base; these are not independent patches.

## Gotchas

No live provider call is made by the regression tests. Unknown-model inference is not a promise of entitlement or credentials.

## Plugin?

This changes core-owned behavior or its test harness; no disk plugin is required.
