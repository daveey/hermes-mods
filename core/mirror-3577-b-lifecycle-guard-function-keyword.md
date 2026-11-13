# Lifecycle guard scans bodies of `function NAME { … }`

Patches: **3569**.

## Behavior
- Inside the supervised gateway, `function f { ./x.sh; }; f` is now treated like `f() { ./x.sh; }; f`: the referenced script is scanned, so a script that restarts the gateway is refused by the lifecycle guard.
- The peeled view is additive (the segment as written is still read), so the same applies to shell scripts, Python interpreter runs, `sh -c` / `env -S` / `su -c` payloads and `launchctl submit`.
- Inert text such as `echo function f x.sh` and `function f { echo x.sh; }` stays allowed.

## How it works
3569 adds `function` to `_COMMAND_PREFIX_KEYWORDS` in `cron/lifecycle_guard.py` and skips the NAME word after it (any word, since bash accepts names like `f-x.y`). The `function f() {` form already worked because `(` `)` split the segment.

## Code
- `cron/lifecycle_guard.py`
- `tests/cron/test_lifecycle_guard_reserved_words.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_reserved_words.py`: the patch adds parametrized cases to existing tests (no new `def test_` names); cases cover `function f {…}`, `f-x.y`, nesting in if/then, `time` inside the body, a python3 runner, `bash -c` / `env -S` / `su -c` payloads, `launchctl submit`, and fail-closed unscannable scripts.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Depends on the earlier reserved-word peeling (`_peel_command_keywords`, `coproc`) being present.
