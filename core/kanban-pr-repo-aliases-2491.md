# Renamed GitHub repo aliases in respawn-guard PR matching

Patches: **2460**.

## Behavior

When a repository is renamed or transferred, an owner may checkpoint a PR as the old URL while a reviewer records the canonical URL reported by `gh`. Both now name the same pull. Previously the review-resume matching failed closed and the respawn guard (`active_pr`) kept holding an implementer whose exact-head review handoff was already complete. After this patch a review recorded under either spelling releases the hold; a PR with the same number in a different repository (or a different number in the same repository) still keeps the hold. The built-in alias maps the fork's previous slug to `example-org/hermes`, and operators can add more under `kanban.github_repo_aliases` in `config.yaml`.

## How it works

`kanban_db_dispatch_pr_policy.norm_pr` is the single PR comparison key: stripped, lowercased, no trailing slash, and for a `https?://github.com/<owner>/<repo>/pull/<n>` URL the repo is mapped through `canonical_repo` and the scheme becomes `https`. `repo_aliases()` merges `BUILTIN_GITHUB_REPO_ALIASES` with valid configured entries (both sides must match `owner/repo`; invalid entries and identity mappings are ignored; an unreadable config yields built-ins only and logs nothing), cached for 60 seconds. `norm_pr` replaces the ad-hoc `rstrip("/").lower()` in the review checkpoint, `_named_prs` (URL and `repo#N` refs, resolved through `canonical_repo`, never number-only across repos), `is_review_result`, `resumes_review`, `holding_urls` (foreign completion contracts are now compared in Python rather than SQL `lower/rtrim`), hold release scope, publications and `released_after`, and the `pr_state`/rejection cache keys so both spellings share one `gh` lookup. Legacy checkpoints that stored the old URL still match a canonical review. Related: [kanban-dependency-review-continuation.md](kanban-dependency-review-continuation.md), [kanban-respawn-guard-hold.md](kanban-respawn-guard-hold.md).

## Code

`hermes_cli/kanban_db_dispatch_pr_policy.py`, `hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_dependency_review.py`, `hermes_cli/kanban_hold_release.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_pr_repo_aliases.py`, `tests/tools/test_kanban_hold_release_boundaries.py`. These cover the respawn guard (`check_respawn_guard`), review resume, hold release scope and receipt, the alias map and config reader, and the PR-state cache, by direct function and DB-level calls. The dispatcher tick and any CLI/tool surface are untested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Only exact known repo identities are collapsed; a repo that is not in the alias map is never matched by pull number alone. The alias map is cached for 60 seconds, so a config edit takes up to a minute to apply. A malformed `kanban.github_repo_aliases` is silently ignored rather than reported. The commit message records a replay of old versus new guard logic over live boards in which only the two target-shape cards changed (held to released).

## Plugin

Core change; no plugin required.
