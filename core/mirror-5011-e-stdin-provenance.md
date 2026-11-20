# Reaching provenance for nested startup-shell stdin

Mirror entries: 4813, 4814, 4815, 4816, 4817, 4818, 4819, 4820, 4821, 4822, 4823.

## Behavior

Executed login/interactive shells refuse stdin sources the guard cannot resolve, while supported literal and variable-backed sources are inspected in the nested shell alias scope.

## How it works

The input walk follows descriptor duplication, redirected groups and visible function calls. An initial command-wide union of assignments and descriptor sources is replaced by a narrow flat unconditional reaching-definition proof. Assignment and redirection expansions freeze at their evaluation point; descriptors resolve left-to-right, and only standalone exec definitions persist. Future, conditional, function-local and subshell values cannot erase inherited uncertainty. Environment variables can supply literal paths when no proven local assignment replaces them; unknown mutations invalidate proofs. Expansion is bounded to sixteen candidates and never turns shell syntax or literal tildes in values into fresh shell source. The flat-sequence proof is shared within a scan and skipped for ordinary non-stdin script inputs. Function discovery normalizes comments and continuations; heredoc call detection distinguishes invocation from definition. Startup scanning retains short circuit, with process-substitution expectations updated for the integrated classifier.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_nested_shells.py`
- `cron/lifecycle_guard_stdin_provenance.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Session-alias tests cover redirected functions/groups, descriptors, unresolved sources and mention-only controls. Stdin-provenance tests use real Bash marker oracles for order/scope, inherited values, declarations, descriptor lifetime and safe twins. Literal-value and function-continuation suites cover environment data, tilde uncertainty and joined function names. The final test-only entry updates resolved process-substitution classification after integration.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/cron/test_lifecycle_guard_session_aliases.py`
- `tests/cron/test_lifecycle_guard_stdin_function_continuation.py`
- `tests/cron/test_lifecycle_guard_stdin_literal_values.py`
- `tests/cron/test_lifecycle_guard_stdin_provenance.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

Unsupported compound control flow remains uncertain rather than inventing a reaching value. Prefix assignments are not automatically visible to their own command redirections. A named cannot-resolve refusal can occur before a deeper lifecycle-file finding; these are different conservative outcomes.
