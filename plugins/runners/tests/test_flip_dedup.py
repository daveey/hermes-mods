"""Numeric threshold dedup must retain the original arithmetic semantics."""
import math

import pytest

from plugins.runners import service as S


def reference(stamps, ttls, now):
    best = math.inf
    for x in stamps:
        if type(x) not in (int, float):
            continue
        try:
            if not math.isfinite(x):
                continue
        except OverflowError:
            continue
        for d in ttls:
            if now <= x + d and x + d - S.FLIP_EARLY_S < best:
                best = x + d - S.FLIP_EARLY_S
    return best


@pytest.mark.parametrize('stamps,ttls,now', [
    ([float(2**53), 2**53], (1,), 2**53 + 1),
    ([2**53, float(2**53)], (1,), 2**53 + 1),
    ([1000, 1000.0, 1000] * 5, (0, 30), 1030),
    ([True, False, float('nan'), float('inf'), 10**400, 1000], (30,), 1030),
    ([1000.000001] * 5, (30,), 1030.000001),
])
def test_dedup_preserves_numeric_thresholds(stamps, ttls, now):
    assert S._next_flip(stamps, ttls, now) == reference(stamps, ttls, now)
