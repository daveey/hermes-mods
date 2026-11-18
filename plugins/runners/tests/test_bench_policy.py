"""Hardware policy tests use synthetic CPU, never sleep or benchmark noise."""
import pytest
import test_bench_inventory as bench


@pytest.mark.parametrize('actions', [None, 'false', 'TRUE', 'true'])
def test_absolute_hardware_policy(monkeypatch, tmp_path, actions):
    monkeypatch.setenv('CI', 'true')
    monkeypatch.delenv('RUNNERS_BENCH_REQUIRE_ABSOLUTE', raising=False)
    monkeypatch.delenv('HERMES_MODS_PUSH_GATE', raising=False)  # this suite may itself run inside the gate
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


@pytest.mark.parametrize('require, expected', [(None, pytest.skip.Exception), ('1', AssertionError)])
def test_push_gate_skips_absolute_unless_required(monkeypatch, tmp_path, require, expected):
    # The pre-push gate runs on whatever host pushes; the hardware proof (check_inventory_hardware.py sets
    # RUNNERS_BENCH_REQUIRE_ABSOLUTE=1) must still never skip, even when started from inside the gate.
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.setenv('HERMES_MODS_PUSH_GATE', '1')
    if require is None:
        monkeypatch.delenv('RUNNERS_BENCH_REQUIRE_ABSOLUTE', raising=False)
    else:
        monkeypatch.setenv('RUNNERS_BENCH_REQUIRE_ABSOLUTE', require)
    monkeypatch.setattr(bench, 'replay', lambda *a, **kw: 0.7 if kw['card_l'] else 10.0)
    with pytest.raises(expected):
        bench.test_inventory_replay_absolute_budget(tmp_path, False)


@pytest.mark.parametrize('regression', ['decoded_bytes', 'projected_rows'])
def test_relative_still_fails_in_the_push_gate(monkeypatch, tmp_path, regression):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.setenv('HERMES_MODS_PUSH_GATE', '1')

    def replay(*args, **kwargs):
        work = dict(decoded_bytes=1000, projected_rows=1000)
        if kwargs['card_l']:
            work = dict(decoded_bytes=10, projected_rows=10)
            work[regression] = 1000
        kwargs['work'].update(work)
        return 0.0

    monkeypatch.setattr(bench, 'replay', replay)
    with pytest.raises(AssertionError, match=f'{regression} work budget'):
        bench.test_inventory_replay_budget(tmp_path, False)


@pytest.mark.parametrize('regression', ['decoded_bytes', 'projected_rows'])
def test_relative_still_fails_on_actions(monkeypatch, tmp_path, regression):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')

    def replay(*args, **kwargs):
        work = dict(decoded_bytes=1000, projected_rows=1000)
        if kwargs['card_l']:
            work = dict(decoded_bytes=10, projected_rows=10)
            work[regression] = 1000
        kwargs['work'].update(work)
        return 0.0  # A fast/noisy clock must not conceal a work regression.

    monkeypatch.setattr(bench, 'replay', replay)
    with pytest.raises(AssertionError, match=f'{regression} work budget'):
        bench.test_inventory_replay_budget(tmp_path, False)


def test_absolute_fixed_cardinality(monkeypatch, tmp_path):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.delenv('HERMES_MODS_PUSH_GATE', raising=False)
    monkeypatch.setattr(bench, 'RUNNERS', 1)
    monkeypatch.setattr(bench, 'replay', lambda *a, **kw: 0.01 if kw['card_l'] else 10.0)
    with pytest.raises(AssertionError, match='1900'):
        getattr(bench, 'test_inventory_replay_absolute_budget', bench.test_inventory_replay_budget)(tmp_path, False)
