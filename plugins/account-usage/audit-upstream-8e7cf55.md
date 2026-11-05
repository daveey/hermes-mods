# Upstream 8e7cf55 compatibility audit

Candidate: upstream `8e7cf55eb673ab4ee5a35cd41faf35592eb70bd6` merged with fork
`03af78fa4a868b3fb545955540294bcda3effd0c`, producing tree
`9b9e8f742a0be459c6680b1afe5de68e551cd753`. Raw upstream is not the deployment candidate.

The chat helper delta moves the compression-feasibility import. Runtime helpers
move the same import and scope intermediate-ack tool detection to the current
turn rather than the entire session. The extracted feasibility module tracks the
auxiliary compression route, clears stale compression ceilings/cooldowns and
re-probes on configuration changes; it introduces no primary credential fence.
Client factories, recover_with_credential_pool, _failed_credential_identity,
switch_model, interruptible API calls and perform_api_call retain their contracts.
The merged tree retains the fork's managed-route fences. No selection.py or
recovery_wrapper change is needed; fallback composition pins remain unchanged.

Additive accepted digests (all previous pins retained for rollout):

- agent/chat_completion_helpers.py: `a0016f92670e6f984d3cc3fde25fe6e35a5f700fdb45c812f59cf6f1cd2a567d`
- agent/agent_runtime_helpers.py: `659fb183fd2ce4e1a3011dbd108ee68d4b9cc98c5ad45ed2682cc812c9b323bd`

Previous accepted baseline digests were respectively
`34d6235241032411cbf65dfef8bf61332320e7e907450b5761c7c089f02f7db7` and
`dd843a0d0f5dc0f3bbaa00cb6c19fd939b3fa74b9a21e180d6bfd07ba26c0a67`.

## Verification

The initial audit run passed canonical `scripts/test-all -k account-usage
--hermes-core <core>` (account-usage and repo-guards) and `check_compat.py`
against installed core 78a282ac5de6a8fb093f458b75d45a1451e33a4e and the exact
candidate tree above, using dependency-equipped Python 3.14.9.
The publication run independently rechecked the two-file delta and passed both
canonical suites and `python3 plugins/account-usage/check_compat.py` against
installed core 5ff44110acc7d21a900f9fe9b30bd1bab3525423.
