# Read-only context for isolated Slack bots

Set `<profile-home>/slack-workspace/policy.json` to `{"context_only": true}` before enabling
`slack-workspace` in that profile. Link the plugin into that profile's `plugins/` directory and use
`hermes -p <profile> plugins enable slack-workspace`. Keep the existing toolsets unchanged.

In this mode registration installs only `pre_llm_call`. No tools, Slack native handlers,
engagement runner, owner-ops handoff or approval handlers are registered. It reads only the
current Slack channel, using that profile's `.env` Slack token, never a gateway-wide environment
token. Terminal execution and credentials remain in the profile's configured sandbox.

For Slack channel turns (not CLI turns or DMs), the hook pages the last 24 hours of channel roots
and their replies. Bot replies are included. Current and future messages are excluded. Channel
text is labelled untrusted context, not new instructions. The context also requires runtime
verification before access claims; it does not assert GitHub authentication is present.

Bounds: 40 API requests per hook call, 100 messages per page, 5-second HTTP timeout with no retries,
40,000 display characters and 2,000 characters per message. Newest evidence wins when the display
fills. Missing scopes, pagination/request limits and display truncation are explicit incomplete
context, never evidence that no prior work exists. Replies on roots older than the 24-hour root
window are not discovered by this bounded history read. This is not a full channel archive.

Tests (offline):

    uv run --with pytest --with pyyaml python -m pytest -q -p no:cacheprovider plugins/slack-workspace/tests_awareness.py

Coverage: top-level cross-thread context including bot replies; history/replies pagination;
current/future/old exclusions; six-decimal Slack timestamp bounds on history and replies;
CLI/DM isolation; failure redaction; request/display bounds; late
status updates after a large old thread; repeated/missing cursors; context-only registration with
no tools/handlers and profile-bound credentials. Model compliance with the capability guidance
is not guaranteed by these deterministic tests.
