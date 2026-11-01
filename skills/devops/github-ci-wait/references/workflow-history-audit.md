# Auditing a workflow's run history

Use this for a green-window soak or deciding whether a lane can become a required check.

## Collect a complete, bounded window

Define the branch, workflow, event scope, inclusive start/end timestamps and acceptance policy before querying.
Keep API timestamps with their offsets; convert to the reader's timezone when reporting. Freeze the end time
so new pushes cannot shift pagination. `gh run list -L N` alone cannot prove a time window is complete.

```bash
# Replace O/R, workflow.yml, START and END; use ISO8601 timestamps.
gh api --method GET --paginate --slurp \
  'repos/O/R/actions/workflows/workflow.yml/runs' \
  -f branch=main -f 'created=START..END' -f per_page=100 > pages.json
```

Check the command's exit status before consuming the file; a failed fetch may leave partial output.
The endpoint caps filtered searches at 1,000 results. Split any interval at or above that count into
smaller inclusive intervals, retain each response and deduplicate boundary overlaps by run `id`.
Do not sum `total_count` across pages: it describes the query, not the page.

For each interval below the cap, validate and emit full SHAs:

```python
import collections
import json

pages = json.load(open("pages.json"))
assert pages, "missing response"
totals = {page["total_count"] for page in pages}
assert len(totals) == 1, "window changed; collect again"
expected = totals.pop()
assert expected < 1000, "split window before claiming completeness"
rows = [run for page in pages for run in page["workflow_runs"]]
runs = {run["id"]: run for run in rows}
assert len(rows) == len(runs) == expected, "duplicate or missing runs; collect again"
runs = sorted(runs.values(), key=lambda run: (run["created_at"], run["id"]))
assert all(len(run["head_sha"]) == 40 for run in runs)
json.dump(runs, open("runs.json", "w"), indent=2)
print(collections.Counter((run["status"], run["conclusion"]) for run in runs))
```

Record the query bounds, raw pages, unique count and conclusion counts. Recheck nonterminal runs before
closing the audit. Listing describes retained, accessible runs, not deleted history; disclose retention gaps.
To identify the workflow, use `gh run view RUN -R O/R --json workflowDatabaseId`, then
`gh api repos/O/R/actions/workflows/WORKFLOW_ID --jq .path`.

## Classify evidence, not missing fields

- List **all** jobs with `gh api --paginate --slurp
  'repos/O/R/actions/runs/RUN/jobs?filter=all&per_page=100'`. Validate job counts against `total_count`
  and unique job IDs as above (the run-list search cap does not apply to this jobs check).
  Group by `run_attempt`; the default latest-attempt view can hide earlier failures. For an all-attempt
  soak, enumerate attempts 1 through the run's `run_attempt` via
  `repos/O/R/actions/runs/RUN/attempts/ATTEMPT` and its paginated `/jobs` endpoint.
- A cancelled run with no runner name or concluded steps is **unclassified**, not proven supersession.
  Corroborate with cancellation annotations, the workflow's concurrency configuration at that commit,
  replacement-run timing and any available cancellation record. Missing job data alone proves neither
  that execution never started nor who cancelled it. Keep unknown cancellations separate from success.
- A cancelled run with executed steps is non-green under a strict green-window policy. Exclude confirmed
  queued supersessions only if the agreed policy explicitly allows that; report the excluded count.
- Start failure triage with `gh run view RUN -R O/R --attempt ATTEMPT --log-failed`.
  This selects failed-step logs, not all logs. Empty output is not proof of a never-started job or lost
  runner: inspect exit status/stderr, run/job status, full logs and check-run annotations. Logs may be
  unavailable or expired, and the CLI documents job/log association limitations.
- Read the job's `check_run_url` rather than assuming job ID equals check-run ID. Retrieve that check run
  and paginate its `/annotations?per_page=100`; reconcile the collected annotations with
  `output.annotations_count`. Annotations can be absent too; report insufficient evidence rather than
  inventing a cause. An explicit runner-communication annotation supports investigating runner logs,
  host boot time and maintenance history, but does not alone identify why communication was lost.

## Prove ancestry and acceptance separately

Find candidate fixes with `git log origin/main --format='%H %cI %s' -- <failing-file>` and review their diffs.
Keep the full 40-character fix SHA and run `head_sha` in the evidence; never feed display abbreviations
into the audit. Fetch the relevant history in an owned checkout, verify both objects with
`git cat-file -e SHA^{commit}`, then run `git merge-base --is-ancestor FIX_SHA RUN_HEAD_SHA`.
Exit 0 proves containment, 1 means not an ancestor, and other failures mean missing/error evidence.
A shallow checkout must fetch enough history first. A missing object is not evidence of non-containment.
The first containing run is a candidate, not acceptance: check its required workflows/jobs and outcomes.

A tag containing the fix proves ancestry only. Tags can be created without CI and releases can be drafts,
prereleases or missing assets. Verify the release workflow on the relevant full SHA and the required
published assets/provenance before claiming a successful build or release. A wait helper's tag-mode success
is only its configured signal, not an independent CI attestation.

Restart a strict green window after the last disqualifying outcome, preserving earlier attempts and unknowns
in the report. A proposed runner-loss mitigation needs live verification; a passing rerun does not erase the
original failure. Before promoting a lane, inspect both branch protection and applicable rulesets, plus the
aggregate gate's dependencies and conditional paths. Use observed queue/wall durations and runner capacity;
a cancelled-run fraction alone does not measure pushes executed or establish sufficient merge-path capacity.

## Sources

- [Workflow runs REST API](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow): filters, pagination and search cap.
- [Workflow jobs REST API](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run): attempt filters and job/check-run fields.
- [Check runs REST API](https://docs.github.com/en/rest/checks/runs#list-check-run-annotations): annotation counts and pagination.
- [gh api](https://cli.github.com/manual/gh_api): explicit GET, pagination and slurp.
- [gh run view](https://cli.github.com/manual/gh_run_view): failed-step log selection and log limitations.
- [Git references REST API](https://docs.github.com/en/rest/git/refs#create-a-reference): tag-reference creation is separate from Actions execution.
