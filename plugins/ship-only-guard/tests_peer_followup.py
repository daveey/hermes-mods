"""Regression coverage for bounded peer eligibility follow-ups."""
import subprocess
import sys
from pathlib import Path

from tests_turn import g, gh, pr, seed_turn, NOW, H2563, R


def test_drift_undecided_refusal_does_not_offer_approval_as_resolution(gh):
    fake = gh({n: pr(n, H2563, ['hermes-sync']) for n in (1, 2)})
    seed_turn(fake, 1, NOW - 1000)
    why = g._turn_hold_refusal(2, fake.prs[2], R, None, {})
    assert 'may still hold' in why
    assert 'blocking exact-head verdict' in why
    assert 'merges' in why and 'yields' in why and 'expires' in why
    assert 'gets an exact-head verdict' not in why


def test_expired_cleanup_has_no_thread_traceback_on_stderr():
    # A fresh interpreter retains threading's real excepthook (pytest intercepts it).
    code = '''
import importlib.util
import sys
spec = importlib.util.spec_from_file_location('cleanup_guard', sys.argv[1])
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
token = g._PREFLIGHT_DEADLINE.set(0)
try:
    g._turn_delete_all(['refs/land-turn/pr-1'], 'example/repo', None, {})
finally:
    g._PREFLIGHT_DEADLINE.reset(token)
'''
    result = subprocess.run([sys.executable, '-c', code, str(Path(g.__file__).resolve())],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'Traceback' not in result.stderr, result.stderr
    assert 'Exception in thread' not in result.stderr, result.stderr
    assert result.stderr == ''
