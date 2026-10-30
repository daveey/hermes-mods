"""The one definition of "a core file drifted from its audited pin".

Stdlib only. install() (selection.check_pins) and the drift checker (check_compat.py)
both call pin_drift(), so the checker cannot pass while install() refuses.
"""
import hashlib
from pathlib import Path


def sha(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'missing'


def accepted(value):
    """A pin is one audited sha256 or a non-empty list of them (every audited core
    version, so mixed-core rollouts pass). Malformed = nothing accepted (drift)."""
    digests = [value] if isinstance(value, str) else value
    if not isinstance(digests, list) or not digests or not all(isinstance(d, str) for d in digests):
        return frozenset()
    return frozenset(digests)


def pin_drift(root, pins):
    """[{'file', 'expected', 'actual'}] for every pinned file under root matching no audited digest.
    A pin table that is not a non-empty dict is itself drift (file '<pins>')."""
    if not isinstance(pins, dict) or not pins:
        return [{'file': '<pins>', 'expected': pins, 'actual': None}]
    drift = []
    for relative, value in pins.items():
        actual = sha(Path(root) / relative)
        if actual not in accepted(value):
            drift.append({'file': relative, 'expected': value, 'actual': actual})
    return drift
