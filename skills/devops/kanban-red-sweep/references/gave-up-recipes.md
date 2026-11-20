# Recipes for specific gave_up / triage causes

## Review card: `review_model_unavailable`

Error: "No eligible deep review model distinct from [...]". The implementer card ran on every deep route at some
point, so every deep model is excluded as an author and no independent deep reviewer exists. Retrying or unblocking
unchanged repeats it.

Fix it on the card's home gateway (for a worker, via `hub-worker-exec <worker> -- '...'`):
1. Pick a model from `model_routing.routes.standard` that is not in the excluded list.
2. `hermes kanban set-model <id> <model> --provider <provider>`
3. Comment why: the deep routes were all authors, and the pin keeps the review independent one tier down.
4. `hermes kanban unblock <id>`, then read back `running` plus the pinned model.

Leave the review card open while an implementer card waits on its verdict.

If `hermes_cli/kanban_review_model.py` on origin/main already falls back to the standard tier but the error still
fires, the dispatching host's core is stale: compare `git -C ~/.hermes/hermes-agent rev-list --count HEAD..origin/main`
on that host. A static worker that always has a run in progress can defer its core update for hours. Update it with
`~/bin/worker-core-update --interrupt-worker-runs --target $(git rev-parse origin/main) --reason "..."`. That reclaims
only restart-safe runs; run it in the background, because the drain plus apply outlasts a 400 s foreground call. Then
read back HEAD and a `kanban sync: default primary` log line. Pinning each review card by hand only treats the symptom.

## Deleting a finished card's scratch workspace

Core scratch GC removes terminal cards' scratch trees after `kanban.worktree_retention_days`. Comment that and
archive the card; no human `rm` is needed.
