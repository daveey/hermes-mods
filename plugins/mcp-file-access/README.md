# mcp-file-access

Approval dialog for Chrome DevTools MCP file access.

`chrome-devtools-mcp` only reads/writes files inside its workspace roots (its temp dir plus any
`--filesystem-root` args in `mcp_servers.chrome-devtools.args`). This plugin asks before it
touches anything else, using Hermes' standard approval card (Once / Session / Always / Deny),
then routes the file through the server's temp dir, so no restart or config edit is needed.

- Grants are per folder and per direction (read vs save). Session/Always on a folder also covers
  its subfolders. "Always" grants live in config.yaml `command_allowlist` as
  `plugin_rule:mcp-file-access:chrome-devtools:<read|write>:<folder>`; delete an entry to revoke.
- Honors Hermes' approval mode: with `approvals.mode: off` (Desktop "Approvals: off"), requests
  are auto-approved without a card, like every other Hermes approval.
- Covered tools: upload_file (read); take_snapshot, take_screenshot, evaluate_script,
  get_network_request, performance_stop_trace, take_heapsnapshot, lighthouse_audit (save).
  Not covered (they write later or keep per-path state): screencast_start,
  performance_start_trace, get_heapsnapshot_*.
- Hooks only (pre_tool_call x2, post_tool_call, transform_tool_result). It does not rely on
  tool_execution middleware: in the sequential executor that middleware runs before
  pre_tool_call hooks.

Tests: `python3 -m unittest discover -s tests`. Disable: `hermes plugins disable mcp-file-access`.
Logs: `grep mcp-file-access ~/.hermes/logs/agent.log`.
