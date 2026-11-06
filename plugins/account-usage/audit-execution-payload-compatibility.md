# Execution payload compatibility audit

Candidate: core `7ae947dfbc0ff42508b000d4de746b270d86bde1`.

The accepted baselines were found by hashing each file in its candidate history
and comparing against `execution-compatibility.json`:

- `agent/agent_runtime_helpers.py`: commit `8733bf9d51171b16e1eb891829a881875c70a0b9`,
  digest `659fb183fd2ce4e1a3011dbd108ee68d4b9cc98c5ad45ed2682cc812c9b323bd`.
  The sole change adds `conversation_id=hook_ids["conversation_id"]` to tool
  dispatch kwargs inside `invoke_tool`. It does not change
  `recover_with_credential_pool`, `_failed_credential_identity`, or `switch_model`.
  New digest: `fc71d64ac441d8807c617e19aec8487bfafe563687de45bcb3e4d60b76533b48`.
- `hermes_cli/middleware.py`: commit `78f7db6349ae315ef9bf40c84c113c72b2ecb94f`,
  digest `d563bca8757d3e9e2766db905d457fdc96de427d873b40d48e3a624f78d7c589`.
  Execution callbacks now receive kwargs filtered by
  `PluginDispatchMixin._hook_callback_kwargs`. Inspected the candidate helper:
  callbacks with VAR_KEYWORD receive the complete payload; narrow callbacks get
  only declared keyword-capable parameters. `ExecutionIntegration.middleware`
  declares `next_call`, `request`, and `**context`, so it retains turn/request
  identity and all context. Invocation remains synchronous, on the same thread.
  The downstream exception envelope, one-call guard and ordinary-error behavior
  are unchanged. New digest:
  `1fe2e27888aab268f5938b3efc33cb70cd137443c6dea1587c37bdb370b66a2d`.

No new credential or route fence is introduced by these diffs. Client factories,
interruptible API call wrappers and `perform_api_call` retain accepted hashes.
Selection's terminal route checks, request-local snapshot attribution and recovery
rotation remain applicable. No `selection.py` edit or fallback composition-pin
refresh is needed; all prior digests are retained for mixed-version rollout.

## Verification

Under the installed Python 3.14.9 runtime, against both installed core
`82b069021392d39971232815600a8596adb67c5d` and the exact candidate:

- `HERMES_MODS_PYTHON="$(command -v python3)" scripts/test-all -k account-usage`
  (candidate adds `--hermes-core <candidate-checkout>`): both suites pass.
- Targeted pytest: 283 tests and 72 subtests pass on each core.
- `python3 plugins/account-usage/check_compat.py` (candidate adds `--core`): exit 0.

The explicit runtime override matters on worker nodes: the runner's default
standalone managed interpreter was not the installed runtime environment and its
suite failed; the installed runtime and dependency environment passed. These
results do not claim a long-lived gateway reload or host rollout proof.
