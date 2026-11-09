# Reserved-child configuration compatibility audit

Candidate: `de81fa41ab7b70362b35066f0ce390964acb575c`.

The nearest already-audited `run_agent.py` version in candidate history is
`82f288a1e596880b5d92fbe6cb58547e4d87730b`, SHA-256
`2da577406ece8b47552d0ebb8b604e59d23398bc48198f12a09baa34f6cfbce0`.
The candidate file has SHA-256
`0b079f3feca58060818c928f0b64c40d1e42dcf16c8b484b2273e4d9e8901052`.
All other execution-pinned files match an existing digest.

## Semantic audit

The complete file delta adds a reserved-row branch to `_ensure_db_session`:
non-None constructor model-config defaults are passed to the owner's
`seed_session_model_config` API after create. Existing keys, including explicit
None values, win atomically. This does not change runtime credentials or requests.

The related delegate reservation change publishes `_delegate_from` immediately.
Selection's `_continuation_chains` already uses the core explicit-fork classifier
and stops at delegate/branch/reset boundaries; preserving this marker prevents a
delegate from being mistaken for a compression continuation. `_mirror_route`
still cross-checks persisted provider/model/base URL against the live mirror.
The worker store's seed method validates session identity and does not journal or
write owner routing/lineage/policy; selection does not call or bypass this API.

Client factories, `recover_with_credential_pool`, `_failed_credential_identity`,
`switch_model`, both `interruptible_*_api_call` seams, and `perform_api_call` are
unchanged. No new credential or request-route fence is bypassed by request-local
snapshots. No selection.py change is required, so the fallback composition digest
remains unchanged. Add the candidate digest without removing rollout digests.

## Verification

Using the installed dependency-equipped Python 3.14.9 runtime:

- `scripts/test-all -k account-usage --hermes-core <installed-core>`: exit 0;
  account-usage and repo-guards PASS (installed core `50022b860ce959918137fa353839008fadbfb74a`).
- Same command with the exact candidate checkout: exit 0; both suites PASS.
- `python3 plugins/account-usage/check_compat.py`: exit 0 on installed core.
- Same checker with `--core <candidate>`: exit 0 on candidate.

Host installation probes are tracked separately from this source audit; these
suite results do not assert activation in already-running host processes.
