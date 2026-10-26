# CI determinism, contributor attribution and source mirrors

Patches: 0256, 0284, 0341, 0350–0351, 0353.

## Behavior

0256 waits for the spawned dead checkout claimant's exit before asserting R5 acknowledgment, removing a test race without changing runtime ownership. 0284 records owner attribution for an automation contributor; the public patch substitutes example identifiers for private contributor details.

0341 bounds optional Python bytecode warmup to one worker and skips splitting shebang-free or binary files, while still detecting late embedded nonportable shebangs. 0350–0351 replaces only the libtirpc 1.3.7 source URL with a content-addressed mirror, preserving the pinned hash. 0353 drains assertion pipelines under pipefail so an early successful match does not give the producer SIGPIPE.

## How it works

The checkout test awaits the child's exit event. Contributor attribution is a data file only. The shebang scanner checks for NUL and `#!` before scanning lines; compileall uses `-j 1`. A flake module overlays libtirpc with `src.overrideAttrs`, replacing builder `urls`, not the fetcher's function arguments; `checks.libtirpc-source` exercises the real fetch. Guard assertions use non-quiet grep redirected to `/dev/null` and fixtures larger than a pipe buffer for early, late and absent messages.

## Code

- `apps/desktop/electron/updater/checkout-handoff.test.ts`
- `contributors/emails/agent@example.com` (public substitute)
- `scripts/check_bash_shebangs.py`, `scripts/run_tests.sh`, `tests/scripts/test_bash_shebangs.py`
- `flake.nix`, `nix/source-mirrors.nix`, `nix/checks.nix`

## Tests

`tests/scripts/test_bash_shebangs.py` covers binary, large shebang-free and late-match fixtures. `checkout-handoff.test.ts` covers the dead claimant. Nix checks include `libtirpc-source` and the early/late/missing managed-message regression. Public mirror verification is recorded below; no live Desktop or full Nix build is claimed.

## Applying

Apply the complete ordered series from its recorded merge-base. Take 0350 and 0351 together: 0351 corrects the fetcher override in 0350. Attribution is intentionally not byte-identical to the private tree; adapt the example mapping for your own contributors.

## Gotchas

The source mirror is version-gated and does not update the locked hash. URL reachability alone is not a Nix derivation build. Serial warmup does not limit the actual test runner's configured parallelism. No runtime deployment occurs when publishing these patches.

## Could this be a plugin instead?

No: these are repository tests, build inputs, contributor metadata and CI tooling.
