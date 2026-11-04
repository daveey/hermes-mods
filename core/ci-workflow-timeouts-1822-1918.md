# CI: state contract dispatch and advisory-job timeouts

Patches: **1876** (`d5be956dcf`, `[daveey] ci: dispatch state's core-contract suites when core main changes a file they read`), **1878** (`738ff9ab5b`, `ci: state-contract-dispatch: one concurrency group per event, so a daily or manual run can't cancel a push run [daveey]`), **1895** (`3d185ea860`, `[daveey] ci: advisory diff jobs no longer cancel on slow full-history checkout`), **1898** (`207e594b60`, `ci: sync-PR history and catalog checks no longer cancel at their timeouts`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A core main push that changes a file the state branch's core-contract suites read dispatches those suites on state's head, plus daily and on demand (1876), with one concurrency group per event so schedule/manual runs cannot cancel a push run (1878).
- Advisory diff jobs (lockfile-diff, prompt-surface-diff) get a 10-minute cap and a blob:none partial clone (1895); sync-PR history and catalog checks no longer cancel at their timeouts (1898).

## How it works

GitHub Actions workflow changes only.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1876: bb-dev-tests.yml and hermes-sync-tests.yml (state branch) check out core main as core-fixture and fail when a core contract they guard drifts, but they only trigger on state pushes. A core main commit that broke one stayed green until the next state push touching their paths. This workflow dispatches both on state's head on a main push to any core file those tests read, daily as a backstop, and on demand. ci_main_watch already reads workflow_dispatch runs on state, so a red one opens the state ci-red card.
- 1878: ci_main_watch reads a cancelled newest push run on main as red; a shared group let the 11:17 UTC schedule or a manual dispatch cancel an in-flight push run.
- 1895: lockfile-diff and prompt-surface-diff ran fetch-depth 0 of the ~1 GB private repo under a 5-minute timeout; on fresh ci-fast runners the fetch ran past it, the jobs were cancelled, and the required aggregate failed (sync PR #805 twice, #801, test/735). Raise both to the fast-lane cap of 10 and make lockfile-diff a blob:none partial clone (credentials persist, so its two blobs fetch lazily).
- 1898: check-common-ancestor's full-blob checkout of the fork outran the 10-min fast-lane cap; merge-base needs only commits, so use filter: blob:none. pinned-source-validate re-validates every catalog entry an upstream sync touches (126 here, ~18 s each) and hit its 30-min cap at entry 88; raise it to 75.

## Code

`.github/workflows/state-contract-dispatch.yml`, `.github/workflows/lockfile-diff.yml`, `.github/workflows/prompt-surface-diff.yml`, `.github/workflows/history-check.yml`, `.github/workflows/plugin-catalog-ci.yml`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- No test files changed in these patches (see Behavior for what is therefore untested here).

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: CI workflows of the fork.
