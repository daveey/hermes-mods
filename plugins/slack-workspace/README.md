# Slack workspace

Observed-channel context, tiered Slack Web API access, and admin-approved actions.

## Read authorization

`slack_api` uses an explicit method inventory; unknown methods require admin approval,
including unknown names starting with `get`, `list`, or `search`. Upload URL allocation
is an action, not a read.

For an ordinary Slack requester:

- Channel-scoped reads require one canonical `channel` ID. Other private or unverified
  conversations are refused; the current conversation remains readable.
- Workspace metadata methods (users, team, emoji, usergroups, presence) remain readable.
- Search, file reads, conversation listings, reminders, and other APIs without an
  authoritative per-conversation content boundary are refused **before fetching**.
  Adding a `channel` parameter does not scope these methods. Use a channel history/
  replies API instead, or ask an admin. This intentionally narrows the former generic
  “reads are free” behavior rather than fetching private results and trying to redact
  every Slack response shape.
- `reactions.get` supports the channel/timestamp message variant only; supplying a
  file or file-comment selector is refused.

Trusted operators/admins retain unscoped reads. These are tool-boundary checks, not
an OS sandbox for a process that already possesses a Slack token.

Channel privacy is cached by immutable channel ID, never by name. Rename/create
events invalidate that ID; the next cross-channel read re-fetches
`conversations.info`. Failed lookup never establishes public visibility.

## Tests

From the repository root, using the documented isolated suite runner:

```sh
python3 scripts/test-all --no-core -k slack-workspace
```

This runs the plugin's complete pytest suite and repository guards. Set `HOME` and
`HERMES_HOME` to disposable locations for local runs; `HERMES_TEST_ALL_HOME_BASE`,
`XDG_CACHE_HOME`, `UV_CACHE_DIR`, and `TMPDIR` can keep tooling writes isolated too.
Privacy regressions live under `tests/test_privacy*.py`. All Slack calls are fakes;
these tests neither require a Slack token nor contact a workspace.
