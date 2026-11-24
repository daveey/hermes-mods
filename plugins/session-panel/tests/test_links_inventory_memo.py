"""Cursor/ledger, alias and scope certificates for the board lane inventory."""
from types import SimpleNamespace

from test_selective_producer import load_api


def fixture(tmp_path, monkeypatch):
    api = load_api()
    r = api.CoreReaders(tmp_path / 'state.db')
    state = {'scope': 'A', 'cursor': 1, 'alias': 1, 'now': 0.0}
    calls = []
    def read(**kwargs):
        calls.append(dict(state))
        return ([{'id': str(state['cursor'])}], [{'alias': state['alias']}])
    ksa = SimpleNamespace(_origin_scope=lambda: state['scope'],
                          _links_fingerprint=lambda: state['cursor'],
                          remote_links_and_origin_rows=read)
    monkeypatch.setattr(r, '_stat', lambda path: state['alias'])
    monkeypatch.setattr(api.time, 'monotonic', lambda: state['now'])
    return r, ksa, state, calls


def test_inventory_reuses_only_matching_cursor_alias_and_scope(tmp_path, monkeypatch):
    r, ksa, state, calls = fixture(tmp_path, monkeypatch)
    first = r._links_inventory(ksa)
    first[0][0]['id'] = 'caller mutation'
    assert r._links_inventory(ksa)[0][0]['id'] == '1'
    assert len(calls) == 1
    for key, value in [('cursor', 2), ('alias', 2), ('scope', 'B'), ('scope', 'A'), ('now', 15.0)]:
        state[key] = value
        r._links_inventory(ksa)
    assert len(calls) == 6


def test_inventory_does_not_certify_a_racing_write(tmp_path, monkeypatch):
    r, ksa, state, calls = fixture(tmp_path, monkeypatch)
    original = ksa.remote_links_and_origin_rows
    def racing(**kwargs):
        result = original(**kwargs)
        state['cursor'] += 1
        return result
    ksa.remote_links_and_origin_rows = racing
    r._links_inventory(ksa)
    ksa.remote_links_and_origin_rows = original
    assert r._links_inventory(ksa)[0][0]['id'] == '2'
    assert len(calls) == 2


def test_old_core_without_certificate_keeps_strict_reads(tmp_path, monkeypatch):
    r, ksa, state, calls = fixture(tmp_path, monkeypatch)
    del ksa._links_fingerprint
    r._links_inventory(ksa)
    r._links_inventory(ksa)
    assert len(calls) == 2
