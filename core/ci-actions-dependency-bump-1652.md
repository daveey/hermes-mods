# CI: actions minor/patch group bump (Dependabot)

Patch: **1652** (`1648872268`, `chore(actions)(deps): bump the actions-minor-patch group across 1 directory with 9 updates`).
Authored by Dependabot in the fork; workflow-only.

## Behavior

Nine GitHub Actions pins move to new commit SHAs across 14 workflow files:

| Action | From | To |
| --- | --- | --- |
| `actions/setup-node` | 7.0.0 | 7.1.0 |
| `azure/login` | 3.0.1 | 3.1.0 |
| `hadolint/hadolint-action` | 3.1.0 | 3.5.0 |
| `actions/setup-python` | 7.0.0 | 7.1.0 |
| `docker/build-push-action` | 7.1.0 | 7.4.0 |
| `docker/login-action` | 4.1.0 | 4.7.0 |
| `actions/download-artifact` | 8.0.1 | 8.0.2 |
| `cachix/install-nix-action` | 31.11.0 | 31.11.3 |
| `google/osv-scanner-action` reusable workflow | 2.3.8 | 2.7.0 |

## How it works

Each `uses:` line keeps its full-SHA pin and version comment; no workflow logic changes.

## Code

`.github/workflows/`: `bootstrap-installer-build.yml`, `desktop-bundled-release.yml`, `docker-lint.yml`, `docker.yml`,
`docs-search-monitor.yml`, `js-autofix.yml`, `nix.yml`, `osv-scanner.yml`, `plugin-catalog-ci.yml`, `pm-bundle.yml`,
`sandbox-image.yml`, `stable-release-publication.yml`, `stable-release.yml`, `tests.yml`.

## Tests

None in the patch. Not rerun by this mirror pass; the workflows themselves are the only check, and their runs are not
verified here.

## Applying

Apply in series order. Conflicts are limited to `uses:` lines if other patches re-pin the same actions.

## Gotchas

- In `docker.yml` and `sandbox-image.yml` the four `docker/build-push-action` lines now pin the 7.4.0 SHA but keep the
  trailing comment `# v7.1.0 (fork: upstream uses useblacksmith/build-push-action)`; the comment is stale, the SHA is
  authoritative.

## Plugin?

No. CI configuration.
