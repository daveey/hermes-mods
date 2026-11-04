# CI merge-base fetches: complete commit graphs without every blob

Patch **1923** (`e0970e3d0079`).

## Behavior

Merge-base consumers keep a depth-one checkout, then fetch the full commit history of HEAD and their base without unrelated heads, tags or eager tree/blob downloads. Unrelated histories return an empty merge base successfully; fetch errors fail the action rather than masquerading as unrelated histories.

## How it works

The composite `fetch-merge-base` action accepts branch or full-SHA inputs. Its shell script uses `--filter=tree:0`, `--no-tags`, `--no-recurse-submodules` and, for shallow repositories, `--unshallow`. Git lazily retrieves missing tree/blob objects when a later command needs them. Optional HTTPS authentication respects checkout-provided extraheaders.

## Code

`.github/actions/fetch-merge-base/{action.yml,fetch-merge-base.sh}`; workflows `contributor-check.yml`, `history-check.yml`, `lint.yml`, `lockfile-diff.yml`, `prompt-surface-diff.yml`, and `supply-chain-audit.yml`.

## Tests

The 1923 mirror pass ran `tests/ci/test_fetch_merge_base.py`: **5 passed**. Local-origin integration tests compare merge bases and contributor ranges with a full clone, exercise lazy tree/blob reads, SHA inputs absent from the checkout, unrelated histories and fetch failures. This pass did not dispatch the modified private-repository workflows or measure hosted-runner performance.

## Applying

Use the full README replay recipe. When porting separately, carry the action and all workflow call sites, then run the integration tests.

## Gotchas

Later lazy object reads still need credentials. Do not replace a fetch error with an empty successful result. This optimizes transfer shape, not the completeness of commit history.
