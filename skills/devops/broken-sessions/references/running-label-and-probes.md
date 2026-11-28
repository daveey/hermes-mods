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

## "Waiting on CI" for hours: identify the actual producer
When the panel says `status unknown` or `last known`, treat its wait label and age as historical observations, not current job state.

1. Read the named prerequisite's current status, checkpoint and exact published head. Trace downstream rows to their shared prerequisite before counting them as separate stalled jobs.
2. Query hosted checks for that exact head (`gh pr view <pr> --repo <owner/repo> --json headRefOid,statusCheckRollup`). A completed green rollup rules out a current hosted-CI wait; it does not prove the card's separate acceptance suite finished.
3. If the checkpoint names a worker-local full gate, inspect its existing launcher, completion artifact and recent log on that worker. Report terminal exit versus active progress separately, and label partial test counts as partial. A scheduled agent can legitimately own a still-running test process; do not restart the suite, edit its immutable tree or unblock it solely because hosted CI is green.
4. Inspect a separate triage row independently. If it has no live executor or protected decision hold, use the supported recovery path and read back an actual worker claim; do not attribute every row to the same CI problem.
5. Report the distinction plainly: hosted CI result, local acceptance progress, shared downstream dependency and any recovered triage stall. Give the existing panel owner the conflicting source evidence without claiming a renderer root cause that has not been traced.

## Separate next-step eligibility from final acceptance
For a scheduled CI/deployment chain, inspect each predicate separately before accepting a combined wake:
1. Identify what the next operation actually consumes. CI using a public source mirror needs the fix published there; it need not wait for the hub to install that same revision.
2. Verify publication independently, for example `gh api repos/<owner>/<public-repo>/compare/<fix-sha>...<branch> --jq .status`: `ahead` or `identical` establishes ancestry; a failed lookup is unknown. Check the installed revision separately, without forcing an update.
3. If the failed CI's required dependency is now available, confirm its exact head and that no rerun is already queued/running. Use `gh run rerun <run-id> --repo <owner/repo> --failed`, then read back `status,attempt,headSha` with `gh run view`.
4. Record the evidence and existing CI run on the delivery card, then use the supported unblock path only for an ordinary scheduled wait, preserving decision holds and parent gates. Verify an actual claim, not merely ready status.
5. Replace the combined wake with the next gate: CI termination on either success or failure. Keep installed-version and loaded-behavior proof as later acceptance gates. Verify the resumed worker's checkpoint retains that separation.

Do not require all eventual acceptance predicates before starting the next eligible operation: a correct conjunction can pass positive/negative probe tests yet unnecessarily idle CI behind a later deployment window. This procedure repairs scheduling, not product acceptance; green local tests or a running worker do not waive hosted CI or loaded proof.

## Review blocked behind the implementation it must review
When an implementation is parked and its exact-head reviewer is `todo`, inspect both cards' parents, not just the recorded park. A dependency graph can be acyclic yet deadlocked in practice: the review requires implementation completion, while completion requires that review.

1. Enumerate the chat with `hermes kanban chat-tasks <chat>` and read both cards with `hermes kanban show <id> --json`. Confirm the candidate is already published and the review brief targets it; a reviewer waiting on a genuinely unfinished prerequisite is not this case.
2. As the authorized owner, remove the erroneous implementation→review edge using `kanban_unlink(parent_id=<implementation>, child_id=<review>)`, then add review→implementation using `kanban_link(parent_id=<review>, child_id=<implementation>)`, preserving unrelated prerequisites.
3. Record the correction. If the implementation still has an obsolete ordinary scheduled hold, use `kanban_unblock` with its newest comment id; it should remain dependency-gated until review finishes. Separate spend, safety or containment holds retain their own release requirements.
4. Read both cards back: verify reviewer admission/running separately from implementation waiting on review. On review completion, verify the implementation resumes to consume the verdict; completion of a request-changes review is not approval.

## Test a wake probe on its executor host
For `wait-comment` parks, first run the exact stored command on the executor; then test the same `--from-card` filter with `--after-id 0` against a known genuine reply. Expect rc1 for no new reply and rc0 for the positive control. A healthy negative alone does not prove the custody filter can match. After executor migration, rederive the latest matching comment cursor from that executor's supported comment listing: integer comment IDs are replica-local, so copying the old host's `--after-id` can silently ignore replies. Preserve the producer identity and finite backstop; do not manufacture a marker or relax the author filter to make the probe pass.

The probe runs on the `host` named in the card's newest `external_watch` event (often a pool worker), not the
hub. Test it there: write the probe to a script and run `~/.hermes/bin/hub-worker-exec <worker> -- <script>`.
On the hub the same command misleads: `ssh hermes-hub` is refused (publickey), and `git` run in `~` is not a
repo. From a worker, hub-read runs `git` inside the hub checkout and rejects `-C`, so
`ssh hermes-hub git merge-base --is-ancestor <sha> HEAD` is correct as written. `kanban schedule --wake-check`
runs the probe once on the hub at park time, so re-parking from the hub with a probe that needs
`ssh hermes-hub` is refused even though the probe works on the worker.
