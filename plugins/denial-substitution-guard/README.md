# denial-substitution-guard

Unpublished M1 containment candidate, **not enabled or deployment-approved**. See SPEC.md for rule, test paths and limitations. It retains observed denials without TTL, identity exemptions or writable grants; independent subsequent authority verification is UNAVAILABLE.

Run targeted offline tests: `python3 -m pytest -q -p no:cacheprovider tests_guard.py tests_m1.py`. The M1 suite also runs without pytest: `python3 -m unittest tests_m1`. The repository runner discovers `tests_*.py`; use `scripts/test-all -k denial-substitution-guard` from the repository root.
All fixtures are synthetic. No AWS probes, IAM/credential changes or grant/reset procedure. Full adversarial acceptance, exact-head review and isolated installed callback proof remain required before publication.
