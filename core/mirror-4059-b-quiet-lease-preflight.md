# Quiet lease preflight and canonical-home clocks

Patches: 3789, 3801, 3802, 3815.

## Behavior

Refuses file-backed quiet-start failures before acquiring a claim-quiet lease or spending its cooldown. Also fixes canonical-home caching for relative and symlink-containing spellings, and avoids wedging starts on a future wall-clock record from a prior boot.

## How it works

`gateway.quiet_control` shares drain/admission-hold and deploy-window readers between gateway start and `start_preflight_reasons`. The CLI runs that check before `claim_quiet.acquire`: missing/unreadable, non-idle, stale or held windows return exit 78 without a lease or observer. The preflight reserves a 30-second margin inside the gateway's 120-second freshness limit.

`local_work.canonical_home` expands the home spelling and joins relative spellings onto the current cwd (including a bytes cwd for bytes input). It caches by that spelling, then applies `realpath` and `normcase`; it must not use `abspath` first because lexical normalization would collapse `link/..` before following the link. In cooldown checks, a negative wall age from another boot is elapsed/start-permissive; same-boot incoherence still refuses, and zero cross-boot age is a full cooldown.

## Code

- `gateway/quiet_control.py`
- `hermes_cli/kanban_quiet.py`
- `agent/claim_quiet.py`
- `hermes_cli/local_work.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/gateway/test_quiet_control.py` (patch 3789).
- `tests/hermes_cli/test_kanban_quiet.py` (patches 3789, 3815).
- `tests/agent/test_claim_quiet.py` (patches 3801, 3802).
- `tests/hermes_cli/test_local_work.py` (patches 3801, 3802).

- **3789:** Gateway tests compare file-preflight reasons with start at zero margin and require the default margin to reject near-stale windows. CLI tests require exit 78, no cooldown/observer and immediate successful retry after fresh idle state, including missing or exploding preflight cases.
- **3801:** Canonical relative homes follow changing cwd while absolute keys cache correctly; future prior-boot wall records permit acquisition, but exact zero age raises a full cooldown.
- **3802:** Symlink-before-dotdot paths resolve identically for absolute, relative and cached spellings and agree with hermes_home_key; adjacent quiet-test wording is clarified without changed runtime assertions.
- **3815:** With an otherwise idle window, a drain/admission hold or unreadable hold exits preflight with no active lease, cooldown or spawned observer.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

The host-file preflight is advisory; the gateway's start remains authoritative and runner-only refusals can still occur after acquisition. Passing preflight does not guarantee an observer can start. Ordinary attempts retain the existing lease/cooldown semantics. Use the separate attestation/check document for the later read-only gateway verb.

## Plugin?

No. Lease custody, clock semantics and shared gateway start gates need coordinated core changes.
