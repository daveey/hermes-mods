"""Hardware policy tests use synthetic CPU, never sleep or benchmark noise."""
import pytest
import test_bench_inventory as bench


@pytest.mark.parametrize('actions', [None, 'false', 'TRUE', 'true'])
def test_absolute_hardware_policy(monkeypatch, tmp_path, actions):
    monkeypatch.setenv('CI', 'true')
    monkeypatch.delenv('RUNNERS_BENCH_REQUIRE_ABSOLUTE', raising=False)
    if actions is None:
        monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    else:
        monkeypatch.setenv('GITHUB_ACTIONS', actions)
    monkeypatch.setattr(bench, 'replay', lambda *a, **kw: 0.7 if kw['card_l'] else 10.0)
    expected = pytest.skip.Exception if actions == 'true' else AssertionError
    with pytest.raises(expected):
        getattr(bench, 'test_inventory_replay_absolute_budget', bench.test_inventory_replay_budget)(tmp_path, False)


def test_required_absolute_never_skips(monkeypatch, tmp_path):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('RUNNERS_BENCH_REQUIRE_ABSOLUTE', '1')
    monkeypatch.setattr(bench, 'replay', lambda *a, **kw: 0.7 if kw['card_l'] else 10.0)
    with pytest.raises(AssertionError, match='absolute inventory CPU'):
        getattr(bench, 'test_inventory_replay_absolute_budget', bench.test_inventory_replay_budget)(tmp_path, False)


def test_relative_still_fails_on_actions(monkeypatch, tmp_path):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setattr(bench, 'replay', lambda *a, **kw: 1.0)
    with pytest.raises(AssertionError):
        bench.test_inventory_replay_budget(tmp_path, False)


def test_absolute_fixed_cardinality(monkeypatch, tmp_path):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.setattr(bench, 'RUNNERS', 1)
    monkeypatch.setattr(bench, 'replay', lambda *a, **kw: 0.01 if kw['card_l'] else 10.0)
    with pytest.raises(AssertionError, match='1900'):
        getattr(bench, 'test_inventory_replay_absolute_budget', bench.test_inventory_replay_budget)(tmp_path, False)
