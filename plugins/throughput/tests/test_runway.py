"""Runway from the pacing governor's stored samples, against hand-computed fixtures."""
import json

import pytest

from throughput_under_test import runway as R

NOW = 1_800_000_000.0
WEEK = 7 * 86400


def _write(tmp_path, data):
    path = tmp_path / "llm_pacing.json"
    path.write_text(json.dumps(data))
    return path


def test_pool_runway_hand_computed(tmp_path):
    reset_a, reset_b, reset_c = NOW + 3 * 86400, NOW + 5 * 86400, NOW + 86400
    path = _write(tmp_path, {"openai-codex": {"read_at": NOW, "samples": [
        [NOW - 3000, "a@example.com", "seven_day", 70.0, reset_a],  # oldest pair inside 10-60 min: +5 % in 3000 s
        [NOW - 1800, "a@example.com", "seven_day", 73.0, reset_a],
        [NOW, "a@example.com", "seven_day", 75.0, reset_a],
        [NOW - 3000, "b@example.com", "seven_day", 3.0, reset_b],  # idle
        [NOW, "b@example.com", "seven_day", 3.0, reset_b],
        [NOW, "c@example.com", "seven_day", 100.0, reset_c],  # exhausted
        [NOW, "a@example.com", "five_hour", 90.0, NOW + 3600],  # not a weekly window: ignored
    ]}})
    (pool,) = R.pools(path, NOW)
    burn_per_s = 5.0 / 3000
    assert pool["provider"] == "openai-codex" and pool["accounts"] == 3 and pool["exhausted"] == 1
    assert pool["remaining_pct"] == pytest.approx(25 + 97 + 0)
    assert pool["burn_pct_per_h"] == pytest.approx(6.0)
    assert pool["runway_s"] == round(122 / burn_per_s)  # 73 200 s = 20 h 20 min
    assert pool["next_reset_at"] == reset_c and pool["read_at"] == NOW
    assert "example.com" not in json.dumps(pool)  # account names never leave the module


def test_no_burn_means_no_runway_and_resets_do_not_count_as_burn(tmp_path):
    path = _write(tmp_path, {"anthropic": {"samples": [
        [NOW - 1800, "a", "seven_day", 100.0, NOW - 60],  # the previous window (reset passed)
        [NOW, "a", "seven_day", 2.0, NOW + WEEK],  # a new window: a different reset, so no slope across it
        [NOW - 300, "b", "seven_day", 40.0, NOW + 86400],  # only 5 minutes of history: under the minimum span
        [NOW, "b", "seven_day", 41.0, NOW + 86400],
    ]}})
    (pool,) = R.pools(path, NOW)
    assert pool["burn_pct_per_h"] == 0 and pool["runway_s"] is None
    assert pool["remaining_pct"] == pytest.approx(98 + 59) and pool["next_reset_at"] == NOW + 86400


def test_stale_accounts_and_malformed_input(tmp_path):
    path = _write(tmp_path, {
        "anthropic": {"samples": [
            [NOW, "a", "seven_day", 50.0, None],
            [NOW - 7200, "gone", "seven_day", 10.0, None],  # not read in the last 20 minutes: not current
            ["x", "bad", "seven_day", 1.0, None], [1, 2],  # malformed rows are skipped
        ]},
        "xai-oauth": {"samples": []},  # no weekly samples: no entry
        "broken": "nope",
    })
    (pool,) = R.pools(path, NOW)
    assert pool["accounts"] == 1 and pool["remaining_pct"] == 50 and pool["next_reset_at"] is None
    assert R.pools(tmp_path / "missing.json", NOW) == []
    (tmp_path / "bad.json").write_text("{")
    assert R.pools(tmp_path / "bad.json", NOW) == []
    assert R.pools(_write(tmp_path, ["list"]), NOW) == []


def test_a_provider_the_governor_stopped_reading_is_not_reported(tmp_path):
    path = _write(tmp_path, {"anthropic": {"samples": [[NOW - R.STALE_S - 1, "a", "seven_day", 10.0, None]]},
                             "openai-codex": {"samples": [[NOW - R.STALE_S + 1, "a", "seven_day", 10.0, None]]}})
    assert [p["provider"] for p in R.pools(path, NOW)] == ["openai-codex"]


def test_providers_are_sorted(tmp_path):
    sample = [[NOW, "a", "seven_day", 10.0, None]]
    path = _write(tmp_path, {"openai-codex": {"samples": sample}, "anthropic": {"samples": sample}})
    assert [p["provider"] for p in R.pools(path, NOW)] == ["anthropic", "openai-codex"]
