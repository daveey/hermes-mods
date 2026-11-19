"""Snapshot/result retention is bounded without changing freshness semantics."""
import shutil
from plugins.runners import service as S
from test_inventory_memo import env, svc, comparable


def test_path_cache_evicts_read_and_result_together(env, monkeypatch, tmp_path):
    cfg, path, builds, decodes = env
    monkeypatch.setattr(S, '_READS', {})
    monkeypatch.setattr(S, '_RESULTS', {})
    paths = [path]
    for i in range(4):
        clone = tmp_path / f'copy-{i}.sqlite'
        shutil.copy(path, clone)
        paths.append(clone)
    expected = svc(cfg, path, 1001).inventory()
    for clone in paths[1:]:
        assert comparable(svc(cfg, clone, 1001).inventory()) == comparable(expected)
    assert len(S._READS) <= 4
    assert len(S._RESULTS) <= 4
    assert str(path) not in S._READS and str(path) not in S._RESULTS
    nbuild, ndecode = len(builds), len(decodes)
    assert comparable(svc(cfg, path, 1001).inventory()) == comparable(expected)
    assert len(builds) == nbuild + 1 and len(decodes) == ndecode + 1


def test_inflight_build_cannot_resurrect_evicted_path(env, monkeypatch, tmp_path):
    cfg, path, *_ = env
    monkeypatch.setattr(S, '_READS', {})
    monkeypatch.setattr(S, '_RESULTS', {})
    others = []
    for i in range(4):
        clone = tmp_path / f'concurrent-{i}.sqlite'
        shutil.copy(path, clone)
        others.append(clone)
    original = S.Service._inventory
    def interleaved(service, *args, **kwargs):
        result = original(service, *args, **kwargs)
        if service.path == path:
            for clone in others:
                svc(cfg, clone, 1001).inventory()
        return result
    monkeypatch.setattr(S.Service, '_inventory', interleaved)
    assert svc(cfg, path, 1001).inventory()['data']['runners']
    assert str(path) not in S._READS and str(path) not in S._RESULTS
    assert set(S._RESULTS) <= set(S._READS)
