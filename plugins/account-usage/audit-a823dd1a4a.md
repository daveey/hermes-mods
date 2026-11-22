# Audit: core a823dd1a4a (Kanban delegation pin refusal), card t_01a204362dbd2c55

Core efa8965dab. Drift: `execution-compatibility.json` `run_agent.py` eec1eec6e064 (faaf0c4535) -> 00b0e36bd9e2
(a823dd1a4a). Only that pin drifted; every other pin matched.

`run_agent.py` diff (+7/-2): `AIAgent._dispatch_delegate_task` now calls `prepare_model_tasks(self, tasks)` (preflight
of a Kanban-origin parent's original tasks, `tool_error` on refusal) instead of `_strip_model_hidden_task_fields`.

Against selection.py's seams:
- Client factories, `recover_with_credential_pool`, `_failed_credential_identity`, `switch_model`,
  `interruptible_*_api_call`, `perform_api_call`: untouched.
- The change only restricts what a delegated child's task list may carry (model/provider/credential overrides on
  Kanban-worker delegations). It reads and writes no credential or route state of the main agent, so it is no new
  fence a request-local snapshot could bypass.
- selection.py unchanged: recovery_wrapper and fast-rate-limit-fallback/account-usage-compatibility.json need no refresh.
