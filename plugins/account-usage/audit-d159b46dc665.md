# Runtime-helper audit: core d159b46dc665

Accepted SHA-256 for `agent/agent_runtime_helpers.py`:
`cda17f4dff0772f18e15006738c52934dc9e3e2fdbdc5a6b07d4467400076c22`.

Compared with audited `72ee74c4a20be88739fc3b9767f5d5b7be400f8a4660df76436b9742a1f1df84`
(core 4ab8a82a979b), this variant extracts `_remember_own_row` to
`agent_runtime_helpers_user_rows` and calls `keep_one_notice_row` while merging
consecutive user messages. Neither change touches request credentials or routing.

Compared with the original reported pin `2f5583394064740558f65ac5172606234f56ca17350c8e8655e3e07c7a2549ab`
(core fe24ed797f53), it also includes Gemini `_http` socket traversal and
`_swap_switch_runtime` switching from `load_pool` to `load_route_pool`.
The latter's origin fence is already covered by audit-bf4225663068.md and
`selection.reject_custom_entry_route`: manual snapshots refuse custom-entry
routes, including built-in provider names shadowed by configured endpoints.

The client factories, `recover_with_credential_pool`, `_failed_credential_identity`,
`switch_model`, interruptible API-call wrappers, and `perform_api_call` have no
additional seam changes in this variant. The route wrapper still delegates to
core; dispatch and terminal wrappers verify provider/model/base URL. Recovery
retains failed-snapshot attribution and validates the next snapshot through
`choose_snapshot`. Managed-routing refusal remains intact.

No selection.py modification or fallback composition pin refresh is needed.
This adds an audited rolling-deployment variant without dropping older hashes;
it does not disable fail-closed checks or authorize unknown core versions.
