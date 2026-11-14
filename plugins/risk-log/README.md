# risk-log

Log and count high-risk Hermes tool use, so you can cut risky capabilities one at a time based on how they are
actually used instead of guessing. It never changes a call's result: a `post_tool_call` hook only
queues the call under a short admission mutex, without waiting for queue capacity or SQLite;
a background thread classifies it and appends metadata-only rows to a 0600 SQLite file.

## Risk classes

| Class | Fires on |
|---|---|
| `dangerous-command` | `terminal`/`process` commands matching Hermes' own approval patterns (`tools.approval_detection.detect_dangerous_command`) |
| `credential-read` | a command, `execute_code` source or `read_file`/`search_files` path touching `~/.ssh`, `.env`, `auth.json`, the keychain (`security find-*-password`), `~/.aws/credentials`, `.netrc`, the token-broker token, private keys |
| `real-browser` | `chrome-devtools` MCP calls (it can attach to your real, logged-in Chrome); writes (click/fill/navigate/evaluate...) are `high`, reads `medium` |
| `message-send` | WhatsApp / Slack / Discord / Signal / iMessage / Telegram send/post tools and core `send_message` |
| `spend-remote-exec` | autoresearch `run_command`, `sandbox_exec`, `buy_credits`, `set_auto_topup` |
| `broker-vend` | `token-broker exec/request` in a command or code (the `--scope` name is kept) |

Each row: time, host, profile, session id, kanban task id, tool, class, level, origin (`interactive`, `cron`,
`kanban`, `gateway:<platform>`, `api_server`/`webhook`), status and duration from the hook. The command/code/path
is stored only as a sha256 plus an 80-character prefix that is redacted first (gitleaks-style rules: vendor
tokens, JWTs, `KEY=value`, `--token x`, URL credentials, long random strings) and then cut.

## Where

`$HERMES_RISK_LOG`, else `<hermes root>/risk/tool-risk.sqlite` (a profile home resolves to its host root, so all
profiles share one file). Directory 0700, file 0600.

```sh
python3 ~/.hermes/plugins/risk-log/store.py stats --days 7        # counts by class and origin
python3 ~/.hermes/plugins/risk-log/store.py export --days 8       # JSONL, no command prefix (for shipping)
python3 ~/.hermes/plugins/risk-log/store.py export --prefix       # include the redacted prefix (local review)
```

`export` without `--prefix` is what you ship off-host (e.g. into a central risk ledger).

## Limits

- Logged after the call returns (`post_tool_call`), so a call that never returns is not logged. Calls another
  plugin blocks are logged with status `blocked`.
- A full queue (2000 pending events, e.g. a wedged disk) drops events rather than waiting for capacity.
  Producer contention alone does not drop events. An idle writer sleeps on a semaphore until submission
  or shutdown wakes it; shutdown wakes do not require a free queue slot.
- Plugin unload permanently retires that generation's writer: new submissions are dropped, accepted events
  drain, and the owning thread closes its SQLite handle. Unload waits at most 5 seconds (process exit: 1 second).
  If disk I/O or classification stalls beyond that bound, shutdown returns false; the daemon drains and closes
  when the operation returns. It cannot forcibly interrupt a stuck operation. Older cores without `on_unload`
  only get the process-exit fallback.
- Failed writes close their connection before reconnecting; failed connection setup also closes its handle.
- Classification is pattern-based: a credential read through an indirection (a script that reads `~/.ssh`) is not
  seen.

## Tests

```sh
cd plugins/risk-log && uv run --with pytest python -m pytest -q -p no:cacheprovider tests
```

Covers redaction, every class, origin labels, 0600 file, export without prefix, hook p99 < 5 ms with a wedged
writer and a full queue, and that a logging failure never changes the call's result. Lifecycle fixtures load
four successive plugin generations and verify unload leaves no retired writer thread or open writer connection;
error/reconnect and setup-failure tests verify explicit closure. A full-queue stall fixture proves bounded
shutdown followed by eventual draining, plus rejection of late writes and retirement before first use.
Concurrent producers verify all 2000 events persist with no contention drops; an idle fixture verifies
no queue polling and successful shutdown wakeup.
