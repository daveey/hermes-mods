# CI upstream sync gate: bounded GitHub reads on the canonical slug

Patches: 2807.

- **2807** (`d13461aaa1`, fix(ci): upstream sync gate reads jobs once per actionable head, on the canonical slug)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- The gate read `runs/{id}/jobs` for every green run in a 50-run list through 301 redirects from a renamed slug, costing ~100-200 requests per call and tripping the fleet GitHub quota brake.
- `candidates()` walks runs newest first, reads jobs only for each head's newest verdict-bearing run and only for heads the gate can act on; completed run-attempt verdicts are cached on disk (keyed by run id + attempt); `main()` resolves the canonical slug once (known rename map, else `repos/{slug}.full_name`, cached); the run list reads page 1 only and `github_pages` does not `--paginate` a named page.

## How it works

- `scripts/ci/upstream_sync.py`; fake in `tests/hermes_cli/upstream_gate_fake.py`.

## Code

- `scripts/ci/upstream_sync.py`

## Tests

- `tests/scripts/ci/test_upstream_sync_quota.py`: jobs read at most once per head, only wanted heads, newest verdict wins (cancelled newer run ignored), verdict cache by attempt, known/unknown slug resolution, every endpoint on the canonical slug, named page not paginated.

Test files touched by these patches:

- `tests/hermes_cli/upstream_gate_fake.py`
- `tests/scripts/ci/test_upstream_sync_quota.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- The on-disk cache lives under the Hermes home cache directory; a stale cache only affects completed attempts.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
