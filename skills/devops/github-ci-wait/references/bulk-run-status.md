# Bulk run status: bounded inspector, never an unbounded CLI loop

For status, conclusion and job counts across a finite list of Actions runs, use a bounded metadata inspector rather than looping `gh run view` and `gh api` under a shared user token. Repeated calls can multiply into run, workflow, jobs and log requests for every ID.

- Deduplicate run IDs before fetching. Prefer a repository-scoped GitHub App token when an approved inspector supports one; do not silently fall back to a shared user credential.
- Fetch only run metadata and, when needed, jobs. Cache completed runs and charge every HTTP attempt against a hard request cap.
- Stop on authentication failure, rate limits or an exhausted request budget. Return explicit coverage, including IDs not reached; incomplete inspection never means nothing failed.
- For partial coverage, inspect only the remaining IDs in bounded batches. Preserve results across batches and verify the collected count against the deduplicated input list.
- Metadata cannot answer whether a specific test passed: inspect that named job's log separately, never download logs in bulk merely to report run status.

Use the deployment's private runbook for its inspector command, supported repositories, credential provider and cache limits; those are environment-specific rather than portable skill defaults.
