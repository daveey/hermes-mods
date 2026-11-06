# Execution compatibility audit: core PR 913

Candidate: `0bdfa756c17ab33909914215cd68619900e970b4`.
Accepted `run_agent.py` SHA-256:
`e219e81c6183db22935fcc05bfe34823cbc313bd62a850363ae03fd254610b98`.

## Comparison and decision

Located the original accepted digest `50f38d7bfd75e9366d3d30f2794ac4a6f043f90f83be76c619e9545797de98e3`
at core commit `4101ef74427b9e980f463ec8f999b23dd3f42188` by hashing file history.
Reviewed its full diff to the candidate, and compared with the newer already accepted
`120883598243403f71e5712dae87e4fcfa93f6d4bfbe875d5f35fb0413a80d75`
at `02df0dd38e884090e298be011c47d067dfdee592`.
The latter comparison contains only the additional optional `memory_agent_context`
constructor argument; forwarding to `init_agent` is preserved.

The older comparison also includes browser cleanup relocation, setup callbacks,
provider-profile API-mode/token-parameter hooks, memory teardown, idle GC and
background review/delegation behavior. None changes the selection adapters' call
signatures or request-local context lifetime. Removed Copilot helpers are not used
by selection, which supports only Anthropic and OpenAI Codex.

Client factories, `recover_with_credential_pool`, `_failed_credential_identity`,
`switch_model`, both `interruptible_*_api_call` functions and `perform_api_call`
remain covered by their already accepted candidate file digests. The complete
checker reports only `run_agent.py` drift before this additive update.

Also inspected the candidate's managed-routing changes: quota-wall rerouting,
pin source tracking and temporary pre-build resolution do not authorize manual
account choice. `managed_routing_refusal` still rejects both configured managed
routing and agent class-route metadata before snapshot dispatch. Existing identity
header, endpoint/model, expiry, cooldown and Codex-entitlement checks remain intact.
The credential-auth recovery extraction/Nous backoff does not add a fence that
selected Anthropic/Codex attempts bypass: manual recovery remains one-attempt
attribution plus checked snapshot rotation, rather than shared-client mutation.

No `selection.py` change is needed. Its peer composition digest and recovery
wrapper remain unchanged. Keep all previous run_agent digests for mixed rollouts.

## Verification

Using the installed dependency environment's Python 3.14.9:

- `scripts/test-all -k account-usage --hermes-core <installed-core>`: both
  account-usage and repo-guards PASS (installed core `1a5e39d3c5591e559c40e6b36e515f6e72c07a86`).
- The same suites with `HERMES_TEST_CORE=<candidate>` and
  `--hermes-core <candidate>`: both PASS.
- `python3 plugins/account-usage/check_compat.py`: exit 0.
- Checker with `--core <candidate>`: exit 0, no skipped checks.

Host installation is verified separately; these results do not assert that an
already-running gateway has reloaded its plugin.
