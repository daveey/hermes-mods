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

## Deleting a finished card's scratch workspace

Core scratch GC removes terminal cards' scratch trees after `kanban.worktree_retention_days`. Comment that and
archive the card; no human `rm` is needed.
