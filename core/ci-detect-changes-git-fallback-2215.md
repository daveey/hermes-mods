# detect-changes falls back to the merge commit git diff

Patches:

- `patches/2189-ci-detect-fall-back-to-the-merge-commit-s-git-diff-w.patch`

When the GitHub compare API call failed (installation rate limit, HTTP 403), the change classifier got no files and failed open, turning every lane on; one burst queued 16 PRs on the single-runner macOS state lane (about 9 h).

## Behavior

- If the compare API fails three times, `detect-changes` lists changed files with `git diff --no-renames --name-only <merge first parent> HEAD`, which spends no REST quota.
- It is used only when `HEAD` equals `GITHUB_SHA` and the merge's second parent equals the event head SHA; otherwise, or when the diff is empty, it fails open exactly as before.
- A rename counts under both names.

## How it works

The `classify` step script in `.github/actions/detect-changes/action.yml` gains a `merge_files()` shell function (`git fetch --depth=2`, `git rev-list --parents` shape check) tried after the API retries.

## Code

`.github/actions/detect-changes/action.yml`.

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`tests/ci/test_detect_changes_git_fallback.py` runs the action's own bash against a real depth-1 merge checkout with a rate-limited stub `gh`: an unrelated change skips the state lane, a state change and a state rename select it, unverified checkouts fail open.

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

A checkout whose merge parents cannot be verified still fails open (by design). The fallback only helps with the merge-commit checkout that `pull_request` events produce.

## Plugin

None.
