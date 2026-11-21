# Core CI: state-contract dispatch canary

Mirror snapshot 5406; ordinals 5365, 5366.

Entries:

- **5365**: Core CI: state-contract-dispatch hourly upstream canary + core_ref on core merges
- **5366**: Test canary title lookup and missing-run summary

## Behavior

- **5365**: a script holds the selection logic: push to main dispatches the state tests with `core_ref` set to the pushed SHA; a new hourly schedule (and manual dispatch) pass both upstream heads; the daily run is unchanged.
- **5366**: test covers canary title lookup and the missing-run summary.

## How it works

- `.github/workflows/state-contract-dispatch.yml` and `scripts/ci/state_contract_dispatch.sh`.

## Code

Shipped non-test paths (union over these entries):

- `.github/workflows/state-contract-dispatch.yml`
- `scripts/ci/state_contract_dispatch.sh`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/ci/test_state_contract_dispatch.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Needs the downstream workflow to accept `core_ref`/`mods_ref` inputs; that part lives elsewhere.

## Why core instead of a plugin

CI wiring for core's own repository.
