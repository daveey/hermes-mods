# Every built-in browser tool leases the managed Chrome

Patches: 0608, 0609 (daveey/hermes `4487e0fa3b`, `9f014a21c3`).

## Behavior

With `browser.managed_agent`, `browser_exec` was the only client that acquired an owner lease; navigate/snapshot/click, `browser_cdp`, `browser_dialog` and the vault page ops used the raw `cdp_url`, which the managed supervisor answers with 403. Now all of them run under a task-scoped lease. Unmanaged `cdp_url` behaviour is unchanged.

0609 (review follow-up): a cached CDP supervisor attached through another endpoint is never used inside a lease or vault operation; a retained hybrid sidecar never launches local Chrome under a managed lease; the vault pins its scope for the whole operation so a concurrent `browser_exec` cannot redirect a fill; a missing task identity is refused under managed config instead of sharing a default owner.

## How it works

`browser_agent_lifecycle` adds `tool_lease` (task-scoped "tools" owner, nested reuse, no harness daemon), `scoped_override` (raises outside a lease, never returns the raw root) and `renew_selected` (the vault keeps the last-selected exec/tools scope leased; a rotated capability fails clearly). `_get_cdp_override` resolves through the lease; agent-browser sessions pin their lease and re-attach on rotation; managed close is a no-op so cleanup never wakes Chrome; the Lightpanda Chrome fallback is gated off for managed sessions. 0609 adds `supervisor_in_scope` and a ContextVar read by `supervisor_task`.

## Code

- `tools/browser_agent_lifecycle.py`, `tools/browser_tool.py`, `browser_tool_cdp.py`, `browser_tool_session.py`, `browser_tool_lifecycle.py`, `browser_cdp_tool.py`, `browser_dialog_tool.py`, `browser_vault_tool.py`.

## Tests

- `tests/tools/test_browser_managed_all_tools.py` runs every path both managed and unmanaged; 0609 adds the supervisor-scope, sidecar, vault-scope and missing-identity cases.

## Applying

Apply with `git am -3` in series order from the merge-base with upstream; the patch is self-contained apart from the earlier patches it extends. If it no longer applies, re-implement from Behavior and Code.

## Gotchas

- The patch was reviewed by a second model; scope binding (not the lease itself) was the part that needed the follow-up. Keep both patches together.

## Plugin?

No. It changes core code that runs before or outside plugin loading.
