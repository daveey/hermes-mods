"""Regressions for composed cache expiry and finite safety-capture retries."""
import pytest

from test_producer_floor import configured
from test_selective_producer import load_api


@pytest.mark.parametrize('carry', [False, True])
def test_safety_capture_failure_respects_finite_backoff_and_retains_owed_work(carry):
    f, r, clock, calls = configured()
    if carry:
        f._producer_carry = {'S1'}
    start = clock['now']
    clock['now'] += 30
    original = f._panel_dirty
    f._producer_pending['S1'] = clock['now']
    def fail(_):
        raise OSError('source capture failed')
    f._panel_dirty = fail
    for attempt in range(8):
        with pytest.raises(OSError):
            f.tick()
        due = f._due(clock['now'])
        if attempt < 3:
            assert due[0] == pytest.approx((0.1, 0.2, 0.4)[attempt])
            clock['now'] += due[0]
        else:
            assert due == (None, None)
        assert f._producer_at['S1'] == start
        assert 'S1' in f._producer_pending
        assert len(calls) == 1
    # A real source event still settles the same owed work after exhaustion.
    f._panel_dirty = original
    r.stamp += 1
    f.tick()
    assert len(calls) == 2
    assert not f._producer_pending
    assert f._admission_failures == 0
    assert f._due(clock['now'])[0] == pytest.approx(30)


def test_inventory_inherits_real_core_cache_expiry(tmp_path, monkeypatch):
    ksa = pytest.importorskip('hermes_cli.kanban_session_activity')
    api = load_api()
    r = api.CoreReaders(tmp_path / 'state.db')
    state = {'now': 1000.0, 'alias': 1}
    scope = ('fixture-scope',)
    monkeypatch.setattr(ksa, '_links_cache', {})
    monkeypatch.setattr(ksa, '_origin_scope', lambda: scope)
    monkeypatch.setattr(ksa, '_links_fingerprint', lambda: ('cursor', 1))
    monkeypatch.setattr(ksa._revalidate, 'forced', lambda *a: False)
    monkeypatch.setattr(ksa.time, 'monotonic', lambda: state['now'])
    monkeypatch.setattr(r, '_stat', lambda _: state['alias'])
    monkeypatch.setattr(ksa, '_board_db_paths', lambda: [])
    def project():
        ksa._origin_rows_sink.rows = [{'alias': state['alias']}]
        return []
    monkeypatch.setattr(ksa, '_project_remote_links', project)
    assert ksa.remote_links_and_origin_rows()[1] == [{'alias': 1}]
    state.update(now=1014.0, alias=2)
    assert r._links_inventory(ksa)[1] == [{'alias': 1}]
    state['now'] = 1016.0
    assert ksa.remote_links_and_origin_rows()[1] == [{'alias': 2}]
    assert r._links_inventory(ksa)[1] == [{'alias': 2}]
