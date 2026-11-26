# "Running" with nothing running, and testing wake probes

## Row says "Running" but nothing runs
Read the session's stored `outcome_json` presentation `reason_codes` (state.db, read-only) and the hub log's
`desktop stripe report: session=<id>` lines.
- `own_turn_active`: a chat turn really is live (your own investigating turn sets this too).
- `linked_work_running`: a linked card has a live run; check its events.
- `linked_ci_deploy_wait`: a `scheduled` card parked on a deploy/clock/probe wait that core maps to
  `external_running`. Its stripe is `external` ("Running on an external service" / "running externally"). A bare
  shell wake probe's exit 1 ("not yet") is recorded as job `running` in the card's `external_watch` event. The
  label comes from the display mapping in `hermes_state_outcome.py` and
  `hermes_cli/kanban_external_adapters/shell.py`, so the fix is a core card there; when the park's wait is
  valid, it can stay as it is.

## Separate next-step eligibility from final acceptance
For a scheduled CI/deployment chain, inspect each predicate separately before accepting a combined wake:
1. Identify what the next operation actually consumes. CI using a public source mirror needs the fix published there; it need not wait for the hub to install that same revision.
2. Verify publication independently, for example `gh api repos/<owner>/<public-repo>/compare/<fix-sha>...<branch> --jq .status`: `ahead` or `identical` establishes ancestry; a failed lookup is unknown. Check the installed revision separately, without forcing an update.
3. If the failed CI's required dependency is now available, confirm its exact head and that no rerun is already queued/running. Use `gh run rerun <run-id> --repo <owner/repo> --failed`, then read back `status,attempt,headSha` with `gh run view`.
4. Record the evidence and existing CI run on the delivery card, then use the supported unblock path only for an ordinary scheduled wait, preserving decision holds and parent gates. Verify an actual claim, not merely ready status.
5. Replace the combined wake with the next gate: CI termination on either success or failure. Keep installed-version and loaded-behavior proof as later acceptance gates. Verify the resumed worker's checkpoint retains that separation.

Do not require all eventual acceptance predicates before starting the next eligible operation: a correct conjunction can pass positive/negative probe tests yet unnecessarily idle CI behind a later deployment window. This procedure repairs scheduling, not product acceptance; green local tests or a running worker do not waive hosted CI or loaded proof.

## Test a wake probe on its executor host
The probe runs on the `host` named in the card's newest `external_watch` event (often a pool worker), not the
hub. Test it there: write the probe to a script and run `~/.hermes/bin/hub-worker-exec <worker> -- <script>`.
On the hub the same command misleads: `ssh hermes-hub` is refused (publickey), and `git` run in `~` is not a
repo. From a worker, hub-read runs `git` inside the hub checkout and rejects `-C`, so
`ssh hermes-hub git merge-base --is-ancestor <sha> HEAD` is correct as written. `kanban schedule --wake-check`
runs the probe once on the hub at park time, so re-parking from the hub with a probe that needs
`ssh hermes-hub` is refused even though the probe works on the worker.
