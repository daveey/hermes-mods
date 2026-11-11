# GitHub Actions outages

Outage shapes and how to handle each. Linked from `SKILL.md`; paths below are relative to the skill dir.

## Incident: runs queue and none start

Signs: githubstatus lists an unresolved Actions incident (`https://www.githubstatus.com/api/v2/incidents/unresolved.json`),
no job in the repo has started since it began, and the queued count keeps rising. Before telling the user or a card
that a fix waits on it, check what gates that fix's landing:
- **Needs CI:** core landings through `hermes-core-wt ship` (it requires the exact-head aggregate check) and any merge
  that branch protection gates. Tell their owners once, and leave the queued runs alone.
- **Doesn't:** a change on the `state` branch (hermes-sync) deploys from the push itself, through each host's sync;
  CI only checks it afterwards. Tell its owner to push as soon as its own tests pass.

Once Actions recovers, run `--once` on each head you waited for before calling it green: runs created during the
incident can be ghosts (next section).

## Ghost runs

After a GitHub Actions incident, a run created during it can be a ghost: `queued` with 0 jobs while newer runs
execute. GitHub refuses every remedy on it (`cancel`: "Cannot cancel a workflow run that is completed";
`force-cancel`: 409 "has not been queued yet"; `rerun`: 403 "This workflow is already running"), and a wait on its
sha never ends. First confirm Actions is back (githubstatus.com `api/v2/summary.json`, component Actions
operational, new runs finishing). For a PR whose current head has only ghost runs, close and reopen the PR: the
`reopened` event refires every `pull_request` workflow (default types) on the same sha, so an exact-head review
still holds. Never push an empty commit for it, because that voids the review. Before each close, re-check that
the PR is still OPEN, the head is unchanged, `autoMergeRequest` is null (closing drops auto-merge) and the head
has no live run, and skip the PR if any check fails. A branch push's ghosts need nothing once a later push has
real runs; otherwise `gh workflow run` where `workflow_dispatch` exists. Ghosts that can't be cancelled stay
`queued` forever: ignore them, and don't count them as pending CI.

## CI refused for billing (account-wide outage)

Symptom: every job fails in 1–4 s with zero steps, across all workflows and repos of the account. The cause is
in the job's check-run annotation, not the log: `gh api repos/R/check-runs/<job id>/annotations --jq '.[].message'`
contains "The job was not started because recent account payments have failed or your spending limit needs to
be increased". This is not a code failure: don't fix-forward or revert anything for it, and tell running repair
workers to stop chasing tests.

1. **Wait for it to clear with this skill's `scripts/actions_billing_probe.sh OWNER/REPO [cheap-workflow.yml]`**
   (don't write a new probe script: this one already handles the in-progress and zero-steps cases), started in
   the background with notify on `ACTIONS OK` / `STILL BLOCKED`. The probe re-runs a cheap Linux workflow, so it
   doesn't depend on someone pushing. "I raised the limit" isn't proof: confirm with the probe before relaying it.
   For a one-shot "are we still blocked?", don't scan recent runs: once repos are on self-hosted runners, no
   hosted job may have run for hours, so recent runs prove nothing. Re-run the refused job itself
   (`gh run rerun <run id> -R O/R --failed`, ~60 s, reuses upstream job outputs) and read its annotation.
2. **List everyone who is waiting before it clears**, and save the list to a scratch file so it survives compaction:
   - chats: `session_search` on the exact phrases `"payments have failed"` and `"spending limit"`. One search
     misses sessions that only quoted the other phrase.
   - Kanban: read the *latest* `blocked` event reason of each blocked card, not the title:
     `select t.id,t.session_id,json_extract(e.payload,'$.reason') from tasks t join task_events e on e.task_id=t.id where t.status='blocked' and e.kind='blocked' and e.created_at=(select max(created_at) from task_events where task_id=t.id and kind='blocked') and (e.payload like '%spending limit%' or e.payload like '%payments have failed%' or e.payload like '%billing%')`.
     Run it on every board/host that has workers, e.g. a remote host's board db over ssh.
   - The card `session_id`s add the chats that own those cards.
3. **Notify only after the probe passes.** A message arrives as a user turn, so sending it early makes every
   session retry into the same refusal. Once it passes: `cross_session send` each chat (CI is back, resume),
   then `kanban_comment` + `kanban_unblock` each card (or `hermes kanban --board <b> comment/unblock` on the
   remote board).
4. **If the probe keeps failing more than ~30 min after the user's change,** tell the user what you observed (how
   many probes, first and last times), then narrow the cause yourself before handing them pages to click:
   - Scope: list the account's repos' newest runs. Public repos still running while private ones are refused
     means GitHub is blocking all *paid* usage on the account, not one workflow or repo.
   - Usage: `gh api "/users/<user>/settings/billing/usage?year=Y&month=M"` returns per-day, per-repo, per-SKU
     items: aggregate `grossAmount` by (repo, sku) and by day to find the runaway workflow (hosted macOS is the
     usual one). There's no API for a personal account's budgets (`/settings/billing/budgets` is 404): read the
     Budgets, Payment information and Payment history pages yourself with `browser_exec(local=true)` (the user's
     signed-in Chrome) instead of asking for screenshots.
   - Mail: search the user's Gmail (same browser) for GitHub mail since the block began (budget reached,
     payment failed, `[GitHub Support]`). No such mail plus a budget under its cap plus all-successful payments
     points away from budget/charges.
   - "The issue we filed" about billing usually means a GitHub Support ticket, not a Kanban/board card: find its
     id in the `[GitHub Support]` mail and read it at `https://support.github.com/ticket/personal/0/<id>` before
     answering. Check board cards only if no ticket exists.
   - Then the remaining documented cause is a failed card **authorization hold**: GitHub places holds after
     significant metered usage or a budget increase, a declined hold locks billing, and it never appears in
     payment history (that only lists completed charges). Docs:
     https://docs.github.com/en/billing/how-tos/troubleshooting/locked-account. Tell the user to look for a
     declined GitHub authorization at the bank, re-save the card on the Payment information page (that triggers a
     new authorization), and otherwise open a support ticket asking for a billing-lock review.
   - Keep the probe running throughout; it ends the wait the moment any of these clears it.
5. **When billing can't be fixed soon, move repos to self-hosted runners instead of waiting** (registration,
   no-sudo workflow edits, dirty-checkout pitfalls, refused PR reruns; also moving back to hosted/Blacksmith): `references/self-hosted-runner-migration.md`.
   Before moving a repo, check its `runs-on` and runners: another session may have moved it already.
6. **Detection:** `~/.hermes/scripts/gh_actions_billing_watch.py` (hourly no_agent hub cron) files verified Kanban cards for refused jobs, spend thresholds, and persistent scan failures. Scan-unavailable errors are operational evidence, not confirmed billing locks: inspect broker vend failure_phase/failure_class, blocked reason, attempts and samples before choosing remediation. Never blindly retry exit75, which also represents quota denial. For read-only live verification, import and call `refused_jobs(now)` and `spend(now)` directly; `main()` and `--force` can create real cards, so tests must isolate the card sink and state.
