# kanban_complete refuses a ship review card's completion that ship cannot read

Patches: **2722**.

## Behavior

- `hermes-core-wt ship --review-task` lands a core PR only when the review card's completion metadata carries exactly `verdict` (`approve`), a 40-hex `reviewed_sha` equal to the PR head, and `reviewed_pr`. Reviewers sometimes completed with `review_verdict`, `review_outcome`, `reviewed_head` or a bare PR number; the card showed done, ship refused it later, and a new review card was filed for the same head.
- Now `kanban_complete` refuses such a completion while the reviewer can still fix it in the same run. The error has rejection class `review_contract`, names each bad key and the unread alias (e.g. `review_verdict is not read`), and states the exact metadata to send. Nothing changes: the card stays running and the same run retries.
- A card states the contract when its body contains `reviewed_pr`, `reviewed_sha` or `reviewed_head`, and a pull URL of the core fork repository (matched case-insensitively against its builtin repo aliases; other repositories, including similarly named ones, are not matched).
- On such a card, a completion counts as a review result when its metadata has any of `verdict`, `review_verdict`, `review_outcome`, `reviewed_sha`, `reviewed_head`, `reviewed_pr`, `approved`, `approval`. It must then carry `verdict` in {`approve`, `request_changes`}, a lowercase 40-hex `reviewed_sha`, and `reviewed_pr` as `https://github.com/<owner>/<repo>/pull/<n>` or `owner/repo#n` (exact string; no surrounding space or `/files` suffix).
- Left alone: cards without the contract, a landing handoff (`published_pr` set), and completions with no review key.
- Ship itself is unchanged: no aliases, no inferred sha, exact-head check as before.

## How it works

New `hermes_cli/kanban_review_contract.py` (`states_contract`, `is_review_result`, `problems`, `refusal`). `tools/kanban_tools.py` `_handle_complete` calls `refusal(task.body, metadata)` right after loading the task, before the goal judge and any write, and raises `_Reject` with class `review_contract` (added to `COMPLETE_REJECTION_CLASSES`). Non-dict metadata names all three keys.

## Code

`hermes_cli/kanban_review_contract.py` (new), `tools/kanban_tools.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_kanban_review_contract.py`. Entry path: the module functions directly, and `tools.kanban_tools._handle_complete` on a claimed card in a temporary board (an unreadable completion is refused as `review_contract`, the card stays running, the corrected metadata then completes it; an exact review completes; a card without the contract completes with alias keys). The `hermes-core-wt ship` consumer, the CLI/dashboard completion paths and the legacy non-tool completion paths are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The gate is in core, not a plugin, because pool workers run most review cards and may not have the completion-guard plugin installed.
- Detection reads the card body: a brief that omits the three key names or the core-fork pull URL is not gated.
- Reviews of other repositories keep their own contracts and are not gated.

## Plugin

Core change; no plugin required.
