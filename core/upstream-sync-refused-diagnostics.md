# Upstream-sync REFUSED diagnostics and bounded updater reports

Patches: **1393** (`a219c0c52cae`), **1394** (`f8989e8d7313`),
**1395** (`38b04a4a255c`), **1396** (`b8600f6f813a`), **1397** (`2947ddb1f550`).
Related: [PR-only landing](upstream-sync-land-via-pr.md) and
[CI slices and the upstream-sync gate](ci-fast-lane-test-slices-and-upstream-sync-gate.md).

## Behavior

- A red candidate keeps its `REFUSED` verdict and run URL. Evidence beneath it names
  non-successful required jobs, their conclusions and URLs, and parsed failing test IDs.
  Skipped/cancelled jobs retain their own conclusion rather than being called failures.
- IDs also failing at the main baseline are labelled `main-baseline failure`, explicitly
  not evidence that upstream caused them. Other parsed IDs are `candidate-only failure observed`
  only with complete baseline evidence; that observation is not proof of upstream causation.
  Missing/incomparable evidence is `baseline comparison unknown`.
- Nightly ownership is matched to the candidate's complete e2e failing set. An owner for
  main's different set is labelled separately. Missing/unreadable state or board access is
  `lookup unavailable`; an incompletely parsed candidate set is `lookup unknown`, not “no owner”.
- `hermes update` preserves the gate verdict when diagnostic lines would hide it behind
  the old four-line tail. Over the 40-line report cap, test-ID listing lines are removed
  before attribution/owner lines; a further cap can still truncate non-listing evidence.
- Diagnostics are informational: even a main-baseline failure remains refused, and a
  diagnostic exception prints `diagnostics unavailable` without waiving the gate.

## How it works

1393 retains the verdict's Actions `run_id` in `Candidate`; `advance()` calls `_explain_red()`
only after printing REFUSED. Required-job names are matched by their final ` / ` component.
`nightly_e2e.failing_tests()` parses job logs; each displayed ID list is limited to 20 entries.

1394 selects the candidate's first parent if it is an ancestor of main, otherwise the
merge-base. Baseline discovery runs once per refusal. Eligible completed `ci.yaml` runs
must match that SHA, main branch, and push/dispatch event. Nightly dispatches must prove
that exact tested SHA in the `Resolve ref` log; their workflow `head_sha` is insufficient.
1395 checks the oldest ten eligible nightlies created at/after the baseline commit time,
then considers verified baseline runs newest-first for each job kind (Python/e2e).

Baseline completeness requires every returned job of that kind to finish success/failure,
no affected-only slices, and complete parsed IDs for every failed job. This is not a
validation that every expected slice number was returned. Whole-file crash, collection,
no-tests and timeout markers make failed-log ID sets incomplete; overlapping parsed IDs
can still be reported as baseline failures, but missing IDs cannot establish candidate-only.

1395 introduced fingerprint-to-card ownership; **1430** (`7737f2331c81`) updates it
for [one red owner per alert scope](nightly-e2e.md). The main-scope `red` record's
latest `failing` IDs are authoritative over its initial body and stale legacy records.
Owners must have a status in `flaky_kanban.OPEN`, have `created_by == nightly-e2e`,
match the state's card ID, and belong to main, not an acceptance scope. This diagnostic
lookup retains the explicit OPEN set; the reporter's own `_is_open` uses the broader
neither-done-nor-archived rule. Legacy fingerprint records still require
body count/list evidence. A missing board idempotency key is allowed because the CLI
JSON shape omits it; a returned key must match creation provenance, including an
adopted legacy identity. `_owner_identity` validates saved identities and can recover
an older adoption fingerprint only from the complete original body. 1396 validates
the state/card-map shape.

1430's source tests in `tests/scripts/ci/test_upstream_sync_diagnostics.py` cover
latest-set versus initial-body lookup, adopted legacy identities, unrelated provenance,
acceptance exclusion, invalid saved identities and red-record precedence over legacy
state. These tests were inspected, not rerun in this mirror pass. Apply 1430 after
its predecessors using the full replay procedure; it changes diagnostic ownership,
not the refusal verdict or landing gate. No plugin is involved.

Log reads share a 300-second elapsed-time budget checked before each call. This is not
an interrupting deadline for an in-flight request or for API pagination. 1396 treats
subprocess timeouts/read errors as unavailable individual logs so other evidence survives.
1396–1397 `_gate_report()` normally keeps the last four lines, widening to the last
`REFUSED`, `NOT YET` or `LANDED` prefix if older. It strips six-space `- ` / `… ` listing
lines only over the cap, reports omitted listing lines, and caps at 40 plus a truncation notice.

## Code

- `scripts/ci/upstream_sync.py`: `Candidate.run_id`, `candidates()`, `advance()`, `_explain_red()` (1393).
- `scripts/ci/upstream_sync_diagnostics.py`: `candidate_failures()`, `_evidence()`, `_log()`,
  `fork_point()`, `baseline_runs()`, `baseline()`, `compare()`, `nightly_owner()`,
  `_lists_exactly()`, `_owner_lines()`, `budgeted()`, `explain()` (1393–1396).
- `hermes_cli/update_cmd_git.py`: `_sync_fork_with_upstream()`, `_gate_report()`,
  `_GATE_VERDICTS`, `_GATE_REPORT_LINES` (1396–1397). No plugin or new configuration switch.

## Tests

Source tests inspected in the patches; **not rerun in this mirror documentation task**.
In `tests/scripts/ci/test_upstream_sync_diagnostics.py` (1393–1396):

- `test_main_baseline_failure_names_job_tests_owner_and_never_blames_upstream`: job/test/run evidence and baseline wording.
- `test_candidate_only_needs_a_completed_passing_job_at_the_same_base`: passing-baseline attribution and causation caveat.
- `test_partial_overlap_splits_preexisting_from_candidate_only`: different IDs go into separate attribution groups.
- `test_python_slice_compares_by_test_id_against_main_ci_yaml`: Python comparison and no e2e owner lookup.
- `test_missing_or_incomparable_evidence_is_unknown_not_attributed`: unavailable logs, absent/wrong-ref nightly evidence, off-main/PR CI, skipped e2e and affected-only slices.
- `test_unparsed_candidate_failure_states_baseline_fact_without_attribution`: no-ID failure keeps baseline fact but not candidate-only attribution.
- `test_owner_lookup_failure_keeps_the_ci_evidence`: unreadable board preserves CI evidence and reports unavailable.
- `test_no_nightly_state_on_this_host_is_unavailable_not_no_owner`: absent state does not assert no owner.
- `test_owner_requires_open_nightly_provenance_and_the_exact_fingerprint`: open/provenance/body/key matching, absent optional key, subsets and empty IDs.
- `test_gate_prints_diagnostics_under_refused_and_still_refuses`: real gate function with mocked transport refuses and does not open/merge a PR.
- `test_gate_still_refuses_when_diagnostic_reads_fail`: API exception leaves REFUSED intact.
- `test_main_ci_yaml_e2e_failure_is_a_main_baseline`: CI dispatch e2e baseline and first-parent label.
- `test_partial_python_slice_set_cannot_prove_candidate_only`: a returned cancelled slice blocks candidate-only attribution.
- `test_python_and_e2e_failures_are_attributed_per_kind`: independent Python/e2e comparisons.
- `test_fork_point_falls_back_to_merge_base_when_first_parent_is_not_on_main`: both fork-point branches with fake git.
- `test_owner_of_mains_different_set_is_never_labelled_exact`: main-only owner is not the candidate-set owner.
- `test_owner_found_through_the_real_kanban_show_json_shape`: spawned fixture executable returns CLI-shaped JSON without an idempotency key; not a live board test.
- `test_unavailable_candidate_log_makes_owner_lookup_unknown_not_no_owner`: missing log prevents negative owner claim.
- `test_corrupt_nightly_state_is_lookup_unavailable_not_no_owner`: corrupt JSON, non-object state, invalid card map and malformed record.
- `test_baseline_whole_file_failure_never_yields_candidate_only`: crash, import/collection, no-tests and timeout markers block attribution from incomplete baseline IDs.
- `test_candidate_whole_file_failure_is_not_matched_to_an_owner`: parsed overlap remains comparable but incomplete candidate set has unknown owner.
- `test_the_oldest_nightlies_after_the_base_are_read_first`: older matching baseline survives newer unrelated nightlies.
- `test_spent_log_budget_reads_as_unavailable`: fake-clock budget prevents the next underlying read.
- `test_timed_out_log_read_is_an_unavailable_log`: candidate-log timeout does not abort remaining diagnostics.
- `test_per_test_failure_sections_keep_the_id_set_complete`: ordinary failure/fixture-error section headers do not invalidate parsed IDs.
- `test_no_required_failure_points_at_the_run`: successful returned required jobs produce the run-directed fallback message.

In `tests/hermes_cli/test_update_upstream_gate_report.py` (1396–1397):

- `test_refused_line_and_evidence_survive_long_diagnostics`: real updater subprocess path with a fixture gate preserves verdict/job/owner and drops an earlier verdict.
- `test_short_gate_output_is_still_its_last_four_lines`: short output retains the old tail behavior.
- `test_report_is_capped`: 40 report lines plus accurate remaining-line notice.
- `test_over_the_cap_test_id_listings_go_before_evidence_lines`: two ID lists are omitted while attribution and owner remain.

Untested here: live GitHub/board access, a complete interactive `hermes update`, actual
criss-cross git histories, and wall-clock enforcement against slow in-flight requests.
Skipped/cancelled candidate-job wording and missing expected slice numbers have no dedicated case in these patches.

## Applying

Apply **1393 → 1394 → 1395 → 1396 → 1397** with `git am -3` in that order, after the
preceding numbered series. 1394–1396 depend on 1393's diagnostic module/tests; 1397
depends on 1396's updater formatter. Existing gate, nightly reporter, flaky-card helpers
and updater are prerequisites, not supplied independently by this five-patch subset.
Use the repository's documented full-series replay policy; this task did not verify replay.

## Gotchas

- Do not interpret “candidate-only” as upstream blame or a baseline match as permission to land red CI.
- A bounded nightly search may miss an older matching run; unknown evidence is not evidence of success.
- Owner lookup needs the reporter's local state and readable board; this feature does not create or update cards.
- The updater prioritizes evidence over test IDs, but more than 40 non-listing lines can still hide later evidence.
- These patches change diagnostics, not required jobs, branch protection, PR landing or green-check policy.

## Follow-up patch (1581): upstream-conflict owner card filed in a fresh interpreter

**1581** (`492162703c`, `fix(update): file the upstream-conflict owner card in a fresh interpreter`). The conflict reporter that 1581 changes (`hermes_cli/update_upstream_conflict.py`) arrived with an upstream-merge bridge, not with a numbered patch in this series.

- **Behavior.** When `hermes update` hits an unattended upstream merge conflict, it files (or refreshes) one Kanban owner card, keyed on the checkout path. Before 1581 this could fail. The updater has already imported `hermes_cli.config_effective` by the time it moves the checkout. `report()` then imported the new kanban modules in the same process, the new `kanban_capabilities` asked the stale `config_effective` for `load_profile_config_effective`, and `validate_assignment` refused the card as a capability mismatch. Now the card is filed correctly. When no owner is filed, the updater prints a fixed `    Repair owner: NOT FILED` line (previously it printed nothing), which a signal detector can match.
- **How it works.** `report()` runs `runtime_command(root, args, module="hermes_cli.update_upstream_conflict", home=get_hermes_home())` as a subprocess (60 s timeout). The new `main()` parses `--cwd`, `--upstream` and repeated `--conflict`, runs the unchanged `_report` against the board, and prints `__HERMES_UPSTREAM_CONFLICT_TASK__=<id>`. The parent returns the last marker id when the child exits 0. On any other result it logs a warning with the child's last 2,000 characters of stderr/stdout (or the `OSError`/`SubprocessError`), prints the existing `see agent.log` warning, and returns `None`. Update custody is unchanged. `_synthesize_upstream_merge` (`hermes_cli/update_cmd_git.py`) prints the `Repair owner:` line.
- **Tests** (new `tests/hermes_cli/test_update_upstream_conflict.py`; not run for this mirror):
  - `test_report_files_owner_despite_stale_in_process_modules`: a stale `config_effective` stub sits in `sys.modules`. `report()` must still create exactly one `upstream-merge-conflict:` card, and a second call must comment on that same card. The commit reports red before the fix and green after.
  - `test_report_failure_is_best_effort_and_logged`: an unwritable board path returns `None`, prints the warning and logs it.
  - `test_conflict_without_owner_prints_not_filed`: with `report` stubbed to `None` and git stubbed, the `NOT FILED` line is printed.
  - Untested: the child timeout path, and a full `hermes update` run with a real checkout move.
- **Gotchas.** The child inherits `HERMES_HOME` from the environment if it is set, and otherwise pins the updater's resolved home. It uses the store/bootstrap Python that `runtime_command` selects, so a broken launcher shows up as a warning plus `NOT FILED`, not as a crash.
