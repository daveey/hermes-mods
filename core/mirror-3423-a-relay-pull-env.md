# Credential relay pull: isolated subprocess environment

Patches: **2940, 2941**.

## Behavior
- The relay pull child owns its Python imports and ignores a backend-provided `PYTHONHOME`, so the agent runtime's environment does not leak into it.
- The pull uses the shared subprocess environment factory (without secret scrubbing or profile-home inheritance).

## How it works
2940 isolates the pull child's import environment in `credential_relay_repull`; 2941 swaps the raw `os.environ.copy()` for `build_subprocess_env(scrub_secrets=False, inherit_profile_home=False)`.


## Code
- `agent/credential_relay_repull.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_relay_pull_environment.py`: `test_pull_child_ignores_backend_pythonhome`, `test_pull_child_owns_python_imports`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
2941 has no test of its own; 2940's tests cover the child environment behavior.
