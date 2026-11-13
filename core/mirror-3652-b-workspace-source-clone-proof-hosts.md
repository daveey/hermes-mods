# workspace_source clone proof on pool workers and the hub

Patches: **3642**.

## Behavior
- A global or system git `url.*` rewrite (for example a `pushInsteadOf` mapping ssh to https) no longer makes the clone proof fail with `origin_rewritten` when it cannot touch this origin. A `url.*` rule in the clone's own local config still refuses. A global/system rule refuses only if it changes the origin's fetch URL or push URL.
- The GitHub id lookup now goes through the dispatcher's gh route (the configured broker wrapper plus resolved gh), so hub clones, which have no gh login, can be proven. Any failure still fails closed as `origin_unreachable`. The lookup timeout rises from 15 s to 30 s.

## How it works
- 3642 changes `prove_clone` in `kanban_source_clones.py`: it checks local-scope rules directly, and for global rules compares `ls-remote --get-url` and `remote get-url --push` (which expand insteadOf/pushInsteadOf) against the origin. The id lookup uses `dispatcher_gh_argv()` instead of a bare `gh api`; no argv means fail closed.

## Code
- `hermes_cli/kanban_source_clones.py`
- `tests/hermes_cli/test_kanban_source_resolver.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_source_resolver.py`: `test_global_push_rewrite_that_misses_origin_passes`, `test_global_rewrite_of_origin_refused`, `test_github_repo_uses_dispatcher_gh_argv`, `test_github_repo_without_gh_argv_fails_closed`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The publish push runs isolated from global git config and pushes to the verified URL, so allowing global rules in the proof does not change what is pushed.
