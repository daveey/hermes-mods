# Compatibility audit: core PR 625

Candidate: `c63fa491de4fafad77310a055f9e7508f28f1471`.

The most recent accepted `agent/turn_recovery.py` version is commit
`37c70bbcaa1c0ce3d960ba0bd71adf237dbdf7e9`, SHA256
`30e2f96afae65650afb4a89f16344777782ab882a3824a826a7d15e35c8606ed`.
The candidate changes only the eager fallback target predicate in that file,
adding `_class_failover_available`: a nonempty routing dict without `exact_pin`
can fail over on a rate limit even when the legacy fallback chain is empty.

## Seam review

- Client factories, `recover_with_credential_pool`, `_failed_credential_identity`,
  `switch_model`, both `interruptible_*_api_call` functions and `perform_api_call`
  are unchanged. No new credential fence or request-local snapshot bypass is introduced.
- The output-cap and usage-window exclusions remain before eager fallback.
  Pool availability still gates activation; fallback still calls
  `_try_activate_fallback`, rather than mutating the request snapshot.
- Selection's recovery wrapper retains failed-attempt credential identity,
  rotation accounting, route equality and one-attempt semantics. Its terminal
  wrapper checks request and agent route equality. Its middleware accepts an
  automatic retry snapshot only for the same turn and route. No change required.
- Fast-rate-limit-fallback still delegates to the original route function under
  a context-local pool-availability override. The new target predicate does not
  bypass the usage-window exclusion or change that wrapper's signature.
- Other candidate changes concern response intake and continuation diagnostics,
  after the request boundary, not credential construction or recovery identity.

Append the candidate file and AST digests; retain every existing accepted digest
for rolling deployment. `selection.py` and its peer composition digest are unchanged.

## Verification

Using installed Hermes Python 3.14.9, `scripts/test-all -k account-usage` passes
against both installed core and the candidate, including combined-plugin tests.
`scripts/test-all -k fast-rate-limit-fallback --hermes-core <candidate>` passes.
These invocations also pass repo-guards. `check_compat.py` exits 0 against both
cores, with no skipped pins. This is compatibility evidence, not a claim that
PR 625 has merged or that existing backend processes have reloaded.

## Revised candidate aadd87984178

Compared `agent/turn_recovery.py` at the already-audited `c63fa491de4f`
(SHA256 `53be24af3e94076a1ea3d540c82e682db7e373badaeb70a5f65e37d158f3f30a`)
to `aadd87984178` (SHA256
`ec792229d99e619a2582bf1d82f5c05df61879232c0a829a9b1bad08c22828ce`).
The sole delta shortens the comment above `_eager_target`; complete module ASTs
are identical. All seam conclusions above therefore remain unchanged, with no
new credential or route fence. Append the file digest, preserving all rollout
versions. No selection.py or fallback AST/composition pin changes are needed.
