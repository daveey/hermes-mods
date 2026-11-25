# Recipes for specific gave_up / triage causes

## Review card: `review_model_unavailable`

Legacy error: "No eligible deep review model distinct from [...]". Older admission policy excluded author
model identities; it can exhaust the deep route list even when a fresh independent reviewer is available.
Retrying or unblocking unchanged repeats the error. Do not recover by selecting a concrete model/provider.

Fix it on the card's home gateway (for a worker, via `hub-worker-exec <worker> -- '...'`):
1. Inspect installed review admission and the actual implementer run/session custody. Use the supported
   review handoff to a genuinely separate reviewer; do not erase or fabricate authorship evidence.
2. Remove legacy exact-model/provider and mandatory restrictions with `hermes kanban set-model <id> none`, or `hermes kanban set-model <id> class:<level> --no-mandatory` (deep, standard or fast).
   Leave routing unselected or request an optional `class:deep` / `class:standard` capability preference,
   with provider omitted and never mandatory. Do not lower review acceptance to obtain a route.
3. Comment the custody evidence and routing change. Keep quota, pacing, funding and billing gates intact.
   If installed admission still requires distinct model identities, repair/update that policy through its
   owner rather than pinning a model or bypassing admission.
4. Only after admission is resolved, `hermes kanban unblock <id>` if needed; read back the exact card's
   fresh claim, run/session identity and resolved route. `ready` alone is not proof of a review.

Leave the review card open while an implementer card waits on its verdict.

If `hermes_cli/kanban_review_model.py` on origin/main already falls back to the standard tier but the error still
fires, the dispatching host's core is stale: compare `git -C ~/.hermes/hermes-agent rev-list --count HEAD..origin/main`
on that host. A static worker that always has a run in progress can defer its core update for hours. Update it with
`~/bin/worker-core-update --interrupt-worker-runs --target $(git rev-parse origin/main) --reason "..."`. That reclaims
only restart-safe runs; run it in the background, because the drain plus apply outlasts a 400 s foreground call. Then
read back HEAD and a `kanban sync: default primary` log line. Exact-model recovery pins are not an alternative
to supported capability routing and independent custody.

## Spawn loop: `workspace_kind=dir but no workspace_path`

The card fails before any agent starts, and triage sweeps or promoting it without fixing the path repeat the failure. Its
recovery card's worker can't repair it, because the worker mutation fence refuses `set_workspace_path`. From the
owning chat (a non-worker process) on the card's home gateway:
1. Take the path from the card's `created` event payload (`workspace_path`). Check that the directory exists and
   `git status -sb` is clean.
2. Run this in `execute_code`. Hermes's python has the core deps, and a bare system python fails on imports:
   `from hermes_cli.kanban_db_connect import connect_closing; from hermes_cli.kanban_db_workspace import set_workspace_path, resolve_workspace`.
   Then `with connect_closing(board=B) as c:`, call `with c: set_workspace_path(c, id, path)`, and check
   `resolve_workspace(kb.get_task(c, id), board=B)`.
3. Run `hermes kanban --board B promote --seen <newest cid> <id> "<restored path + why>"`. Then read back a fresh
   `spawned` + `worker_bound` event, not just `ready`. Archive the recovery card and comment on the original.

## Deleting a finished card's scratch workspace

Core scratch GC removes terminal cards' scratch trees after `kanban.worktree_retention_days`. Comment that and
archive the card; no human `rm` is needed.
