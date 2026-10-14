import importlib.util
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
def load(name):
    path = ROOT / (name + '.py')
    assert path.exists(), f'{name} implementation missing'
    spec = importlib.util.spec_from_file_location('compute_test_' + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_shared_bindings_persist_and_isolate(tmp_path):
    m = load('ownership')
    store = m.OwnershipStore(tmp_path / 'registry.db')
    key = '["vast","acct","zone","42"]'
    a = dict(gateway_id='g1', profile='default', session_id='s1')
    b = dict(gateway_id='g2', profile='default', session_id='s1')
    c = dict(gateway_id='g1', profile='other', session_id='s1')
    for identity in (a, a, b, c):
        store.attach(key, **identity)
    assert store.owners(key) == [a, c, b]
    m.OwnershipStore(tmp_path / 'registry.db').detach(key, **a)
    assert store.owners(key) == [c, b]
    store.detach(key, **a)
    for bad in ('', ' ', None, 'bad\nidentity'):
        with pytest.raises(ValueError):
            store.attach(key, gateway_id='g', profile='p', session_id=bad)
    for bad in ('abc', '[]', '["a","b","c"]', '[1,"b","c","d"]'):
        with pytest.raises(ValueError):
            store.attach(bad, **a)
