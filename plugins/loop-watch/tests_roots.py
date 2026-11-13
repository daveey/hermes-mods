import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import types

import pytest

_dir = Path(__file__).parent
spec = importlib.util.spec_from_file_location('roots_test', _dir / 'roots.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

GATEWAY = ['hermes', 'gateway', 'run', '--external-supervisor']
SERVE = ['hermes', 'serve', '--isolated']
MB = 1 << 20


def _module(name, **members):
    module = types.ModuleType(name)
    vars(module).update(members)
    return module


def _blob(tag, n=2000, size=1000):
    """About n * size bytes of distinct strings."""
    return [f'{tag}-{i}-' + 'x' * size for i in range(n)]


def _rows(report):
    return {label: (size, count) for label, size, count in report['top']}


def _report(**members):
    return r.scan(modules={'fakemod': _module('fakemod', **members)}, top=10 ** 6)


def _scan(**members):
    return _rows(_report(**members))


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.delattr(sys, r.SENTINEL, raising=False)
    yield
    monkeypatch.delattr(sys, r.SENTINEL, raising=False)


def _arm_file(home, spec=None, *, mode=0o600, raw=None):
    d = home / r.SUBDIR
    d.mkdir(mode=0o700, exist_ok=True)
    path = d / r.ARM
    fresh = d / 'arm.new'  # replaced atomically: an armed process re-reads the file while tests rewrite it
    fresh.write_bytes(raw if raw is not None else json.dumps(spec).encode())
    fresh.chmod(mode)
    os.replace(fresh, path)
    return path


def _probe_dir(home):
    (d,) = (p for p in (home / r.SUBDIR).iterdir() if p.is_dir())
    return d


def _request(d):
    time.sleep(0.01)
    req = d / 'request'
    req.write_bytes(r.COMMAND)
    req.chmod(0o600)


# --- the scan -------------------------------------------------------------------------------------------

def test_a_module_global_owns_what_it_holds():
    rows = _scan(cache={i: v for i, v in enumerate(_blob('a'))}, small=['y'])
    size, count = rows['fakemod.cache']
    assert 2 * MB < size < 2.5 * MB and count == 2001  # the dict and its 2,000 strings; int keys are not counted
    assert rows['fakemod.small'][0] < 1000


def test_a_shared_object_is_counted_once_for_the_nearest_owner():
    shared = _blob('s')
    rows = _scan(direct=shared, nested={'a': {'b': {'c': shared}}})
    assert rows['fakemod.direct'][0] > 2 * MB
    assert rows['fakemod.nested'][0] < 2000  # three small dicts: the list went to the nearer owner


def test_an_instance_global_gets_a_row_per_attribute_two_levels_deep():
    class Store:
        def __init__(self):
            self.rows = _blob('r')
            self.index = {'k': _blob('i', n=500)}

    class Runner:
        def __init__(self):
            self.store = Store()
            self.queue = _blob('q', n=1000)

    rows = _scan(runner=Runner())
    assert rows['fakemod.runner.store.rows'][0] > 2 * MB
    assert 0.5 * MB < rows['fakemod.runner.store.index'][0] < 0.7 * MB
    assert 1 * MB < rows['fakemod.runner.queue'][0] < 1.2 * MB
    assert rows['fakemod.runner'][0] < 2000


def test_a_class_attribute_is_an_owner():
    class Registry:
        entries = _blob('c')

    Registry.__module__ = 'fakemod'
    Registry.__qualname__ = 'Registry'
    rows = _scan(Registry=Registry)
    assert rows['fakemod.Registry.entries'][0] > 2 * MB


def test_a_blocked_threads_locals_are_owned_by_its_frame():
    release = threading.Event()
    ready = threading.Event()

    def holder():
        held = _blob('t')
        ready.set()
        release.wait(30)
        return len(held)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert ready.wait(10)
        rows = _scan()
    finally:
        release.set()
        thread.join()
    label = 'test_a_blocked_threads_locals_are_owned_by_its_frame.<locals>.holder>'
    (size,) = (size for name, (size, _) in rows.items() if name.startswith('<frame tests_roots.py:') and label in name)
    assert size > 2 * MB


def test_a_suspended_generators_locals_are_owned_by_its_frame():
    def gen():
        held = _blob('g')
        yield len(held)

    g = gen()
    next(g)
    rows = _scan()
    (size,) = (size for name, (size, _) in rows.items() if name.endswith('.<locals>.gen>'))
    assert size > 2 * MB
    g.close()


class Pause:
    def __await__(self):
        yield


def test_a_suspended_coroutines_locals_are_owned_by_its_frame():
    async def coro():
        held = _blob('co')
        await Pause()
        return len(held)

    c = coro()
    c.send(None)
    rows = _scan()
    (size,) = (size for name, (size, _) in rows.items() if name.endswith('.<locals>.coro>'))
    assert size > 2 * MB
    c.close()


def test_a_container_nothing_owns_is_listed_by_type():
    loose = _blob('u')  # held only by this thread's stack
    rows = _scan()
    assert rows['<unowned list>'][0] > 2 * MB
    assert loose


def test_closure_state_is_owned_by_the_code_of_its_module():
    def make():
        memo = _blob('m')
        return lambda: memo

    fn = make()
    rows = _scan()
    assert rows[f'<code {__name__}>'][0] > 2 * MB
    assert fn()


class Holder:
    pass


def test_an_unreached_instance_is_owned_by_its_class_with_attribute_rows():
    h = Holder()  # held only by this thread's stack, which the census never walks
    h.data = _blob('h')
    rows = _scan()
    assert rows[f'<inst {__name__}.Holder>.data'][0] > 2 * MB
    assert rows[f'<inst {__name__}.Holder>'][0] < 1000


def test_many_unreached_instances_get_one_row_and_no_materialized_dicts():
    items = [Holder() for _ in range(r.SPLIT_INSTANCES + 50)]
    for item in items:
        item.data = 'x' * 1000 + str(id(item))
    rows = _scan()
    assert f'<inst {__name__}.Holder>.data' not in rows
    assert rows[f'<inst {__name__}.Holder>'][0] > len(items) * 1000


def test_the_report_has_names_sizes_and_counts_but_no_values_or_keys():
    secret = {f'KEY-SECRET-{i}': f'VALUE-SECRET-{i}' for i in range(1000)}

    class Odd:
        pass

    odd = Odd()
    vars(odd)['not an identifier SECRET'] = ['VALUE-SECRET']
    text = json.dumps(_report(table=secret, odd=odd))
    assert 'SECRET' not in text and 'fakemod.table' in text


def test_names_that_are_not_identifiers_are_masked():
    module = _module('fakemod')
    vars(module)['user-token ABC123 /home/x'] = _blob('n', n=100)

    class K:
        pass

    K.__module__ = 'fakemod'
    K.__qualname__ = 'K'
    setattr(K, 'tok en SECRET', _blob('k', n=100))
    vars(module)['K'] = K
    weird = _module('weird mod SECRET', cache=_blob('w', n=100))

    def fn():
        pass

    fn.__module__ = 'plug.ins/alice@example.com'
    fn.memo = _blob('f', n=100)
    report = r.scan(modules={'fakemod': module, 'weird mod SECRET': weird}, top=10 ** 6)
    text = json.dumps(report)
    assert not [bad for bad in ('ABC123', 'tok en', 'SECRET', 'alice') if bad in text]
    rows = _rows(report)
    assert rows['fakemod.?'][0] > 100_000 and rows['fakemod.K.?'][0] > 100_000
    assert rows['?.cache'][0] > 100_000 and rows['<code ?>'][0] > 100_000
    assert r._frame_label(compile('1', '/tmp/SECRET FILE.py', 'eval')) == '<frame ?:<module>>'
    assert r._frame_label(test_names_that_are_not_identifiers_are_masked.__code__) == (
        '<frame tests_roots.py:test_names_that_are_not_identifiers_are_masked>')
    assert r._dotted('a.b-c.<locals>.d') == 'a.b-c.<locals>.d' and r._dotted('é') == '?' and r._ident('é') == '?'


def test_class_type_and_function_names_that_are_not_identifiers_are_masked():
    class Named:
        entries = _blob('q', n=100)

    Named.__module__ = 'fakemod'
    Named.__qualname__ = 'Bad Name SECRET'

    class Loose:
        pass

    Loose.__qualname__ = 'Loose SECRET'
    loose = Loose()  # held only by this thread's stack
    loose.data = _blob('l', n=100)

    def gen():
        held = _blob('g', n=100)
        yield len(held)

    gen.__code__ = gen.__code__.replace(co_qualname='gen SECRET', co_filename='/tmp/SECRET dir/SECRET file.py')
    g = gen()
    next(g)
    report = _report(Named=Named)
    assert 'SECRET' not in json.dumps(report)
    rows = _rows(report)
    assert rows['fakemod.?.entries'][0] > 100_000
    assert rows[f'<inst {__name__}.?>.data'][0] > 100_000
    assert rows['<frame ?:?>'][0] > 100_000
    g.close()


def test_hostile_types_are_skipped_not_fatal():
    """In a child process: a failing assert here would have pytest repr the hostile objects."""
    code = (
        "import importlib.util, json, types\n"
        f"spec = importlib.util.spec_from_file_location('roots', {str(_dir / 'roots.py')!r})\n"
        "roots = importlib.util.module_from_spec(spec); spec.loader.exec_module(roots)\n"
        "ran = []\n"
        "class Meta(type):\n"
        "    def __eq__(cls, other): ran.append('eq'); raise RuntimeError('eq')\n"
        "    __hash__ = None\n"
        "    def __getattribute__(cls, name): ran.append('getattribute'); raise RuntimeError('getattribute')\n"
        "class Weird(metaclass=Meta): pass\n"
        "class BadSize:\n"
        "    def __sizeof__(self): raise RuntimeError('sizeof')\n"
        "loose = [object.__new__(Weird), BadSize()]\n"
        "mod = types.ModuleType('fakemod')\n"
        "vars(mod).update(weird=object.__new__(Weird), items=[object.__new__(Weird), BadSize()], Weird=Weird,\n"
        "                 after=['p%d' % i + 'x' * 1000 for i in range(100)])\n"
        "del ran[:]\n"
        "report = roots.safe_scan.__globals__['scan'](modules={'fakemod': mod}, top=10 ** 6)\n"
        "rows = {name: size for name, size, _ in report['top']}\n"
        "print(json.dumps([report['status'], rows.get('fakemod.after', 0), ran]))\n"
    )
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    status, after, ran = json.loads(out.stdout)
    assert status == 'ok' and after > 100_000
    assert ran == []  # no metaclass __eq__/__hash__/__getattribute__ was called


def test_numbers_are_not_counted_and_strings_are():
    rows = _scan(numbers=list(range(100000, 101000)), words=[str(i) for i in range(100000, 101000)])
    assert rows['fakemod.numbers'][1] == 1
    assert rows['fakemod.words'][1] == 1001


def test_the_census_does_not_count_itself():
    """In a fresh process: the bitmap, the label table and the object list are no owner's bytes."""
    code = (
        "import importlib.util, json\n"
        f"spec = importlib.util.spec_from_file_location('roots', {str(_dir / 'roots.py')!r})\n"
        "roots = importlib.util.module_from_spec(spec); spec.loader.exec_module(roots)\n"
        "big = ['x' * 1000 + str(i) for i in range(20000)]\n"
        "print(json.dumps([roots.scan(top=2000), roots.scan(top=2000)]))\n"
    )
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    first, second = json.loads(out.stdout)
    assert r.scan.__code__.co_cellvars == ()  # a cell of scan's frame would be in gc.get_objects()
    for report in (first, second):
        rows = _rows(report)
        assert rows['__main__.big'][0] > 20 * MB
        assert not [name for name in rows if '_Walk' in name or 'bytearray' in name]
        assert rows.get('<unowned cell>', (0, 0))[0] < 50_000
        assert report['bytes'] < rows['__main__.big'][0] + 12 * MB  # the interpreter and its stdlib imports
    assert abs(second['bytes'] - first['bytes']) < MB


def test_the_scan_leaves_nothing_behind():
    import gc
    _scan(cache=_blob('l', n=10))
    assert not [o for o in gc.get_objects() if type(o) is r._Walk]  # no frame cycle keeps the bitmap alive


def test_a_lazy_module_is_not_imported_by_the_scan():
    class Lazy(types.ModuleType):
        touched = []

        def __getattribute__(self, name):
            type(self).touched.append(name)
            return super().__getattribute__(name)

    lazy = Lazy('lazy')
    object.__getattribute__(lazy, '__dict__')['cache'] = _blob('z', n=100)
    report = r.scan(modules={'lazy': lazy}, top=10 ** 6)
    assert Lazy.touched == [] and _rows(report)['lazy.cache'][0] > 100_000


def test_a_raising_class_property_does_not_break_the_scan():
    class Proxy:
        @property
        def __class__(self):
            raise ReferenceError('gone')

    rows = _scan(proxy=[Proxy()], after=_blob('p', n=100))
    assert rows['fakemod.after'][0] > 100_000


def test_the_deadline_truncates_but_still_reports():
    ticks = iter(range(10 ** 9))
    report = r.scan(clock=lambda: float(next(ticks)), deadline_s=3.0,
                    modules={'fakemod': _module('fakemod', cache=_blob('d'))})
    assert report['status'] == 'ok' and report['truncated'] is True


def test_a_passed_deadline_skips_every_later_loop():
    calls = [0]

    def clock():
        calls[0] += 1
        return 0.0 if calls[0] == 1 else 100.0

    loose = [[i] for i in range(200000)]
    report = r.scan(clock=clock, deadline_s=1.0, modules={'fakemod': _module('fakemod', cache=_blob('d'))})
    assert report['truncated'] is True and report['status'] == 'ok'
    assert report['gc_objects'] > 200000 and report['objects'] < 100  # no pass over the heap went on
    assert calls[0] < 10 and loose


def test_every_pass_over_the_heap_goes_through_the_deadline_checked_chunks():
    import inspect
    source = inspect.getsource(r.scan)
    assert source.count('in w.chunks(everything)') == 5 and source.count('in w.chunks(mods, 16)') == 1
    assert 'in everything' not in source and 'enumerate(' not in source
    # ...and chunks stops at the deadline, between slices
    now = [0.0]
    w = r._Walk(lambda: now[0], 10.0)
    seen = []
    for chunk in w.chunks(list(range(10)), 3):
        seen.append(chunk)
        now[0] = 11.0
    assert seen == [[0, 1, 2]] and w.truncated is True
    assert list(r._Walk(lambda: 0.0, 10.0).chunks(list(range(7)), 3)) == [[0, 1, 2], [3, 4, 5], [6]]


def test_the_deadline_is_checked_before_a_big_container(monkeypatch):
    monkeypatch.setattr(r, 'BIG_CONTAINER', 1000)
    calls = [0]

    def clock():  # passes the deadline only after the module pass has queued its roots
        calls[0] += 1
        return 0.0 if calls[0] <= 3 else 100.0

    big = [str(i) for i in range(5000)]
    report = r.scan(clock=clock, deadline_s=1.0, modules={'fakemod': _module('fakemod', big=big)}, top=10 ** 6)
    assert report['truncated'] is True
    assert _rows(report).get('fakemod.big', (0, 0))[1] < 10  # its 5,000 strings were not walked


def test_the_owner_table_and_the_top_list_are_capped(monkeypatch):
    assert len(r.scan(top=3)['top']) == 3
    monkeypatch.setattr(r, 'MAX_LABELS', 5)
    report = _report(**{f'g{i}': [f'v{i}'] for i in range(50)})
    assert report['owners'] <= 6 and '<other owners>' in _rows(report)


def test_report_fields():
    report = _report(cache=_blob('f', n=10))
    assert report['kind'] == 'roots_census' and report['status'] == 'ok' and report['truncated'] is False
    parts = [report[k] for k in ('module_bytes', 'frame_bytes', 'code_bytes', 'instance_bytes', 'unowned_bytes')]
    assert sum(parts) == report['bytes'] and report['objects'] > 0 and report['gc_objects'] > 0
    assert len(json.dumps(r.scan()).encode()) <= r.MAX_OUTPUT


def test_failing_scan_reports_unknown(monkeypatch):
    def boom():
        raise MemoryError

    monkeypatch.setattr(r, 'scan', boom)
    assert r.safe_scan() == {'schema': 1, 'kind': 'roots_census', 'status': 'unknown', 'error': 'MemoryError'}


# --- arming ---------------------------------------------------------------------------------------------

def test_default_off_writes_nothing(tmp_path, clean, caplog):
    with caplog.at_level('WARNING', logger='loop-watch'):
        assert r.start(tmp_path, argv=GATEWAY) is None
    assert list(tmp_path.iterdir()) == [] and caplog.text == ''


@pytest.mark.parametrize('spec', [
    {'until': 1}, {'until': 'soon'}, {}, [], {'until': time.time() + r.MAX_WINDOW_S + 3600},
    {'until': float('inf')}, {'until': True}])
def test_an_invalid_or_stale_arm_file_does_not_arm(tmp_path, clean, caplog, spec):
    _arm_file(tmp_path, spec)
    with caplog.at_level('WARNING', logger='loop-watch'):
        assert r.start(tmp_path, argv=GATEWAY) is None
    assert 'roots census not armed' in caplog.text and not hasattr(sys, r.SENTINEL)
    assert [p.name for p in (tmp_path / r.SUBDIR).iterdir()] == [r.ARM]


def test_read_arm_never_raises(tmp_path):
    _arm_file(tmp_path, raw=b'{"until": 1' + b'0' * 400 + b'}')  # float() overflows
    assert r.read_arm(tmp_path)[0] == 0.0
    _arm_file(tmp_path, raw=b'\xff\xfe{')
    assert r.read_arm(tmp_path)[0] == 0.0


def test_a_hard_linked_arm_file_does_not_arm(tmp_path, clean):
    path = _arm_file(tmp_path, {'until': time.time() + 600})
    os.link(path, tmp_path / 'second-name')
    assert r.start(tmp_path, argv=GATEWAY) is None


def test_removing_the_arm_file_disarms_and_a_new_one_arms_again(tmp_path, clean, caplog):
    path = _arm_file(tmp_path, {'until': time.time() + 600})
    fast = dict(argv=GATEWAY, recheck_s=0.0, sleep=lambda s: time.sleep(0.01))
    thread = r.start(tmp_path, **fast)
    assert thread is not None and hasattr(sys, r.SENTINEL)
    path.unlink()
    with caplog.at_level('WARNING', logger='loop-watch'):
        thread.join(10)
    assert not thread.is_alive() and not hasattr(sys, r.SENTINEL) and 'disarmed' in caplog.text
    (first,) = (p for p in (tmp_path / r.SUBDIR).iterdir() if p.is_dir())
    _request(first)
    time.sleep(0.2)
    assert (first / 'request').exists() and not (first / 'result.json').exists()  # a disarmed probe is spent
    (first / 'request').unlink()
    _arm_file(tmp_path, {'until': time.time() + 600})
    again = r.start(tmp_path, **fast)
    assert again is not None
    (second,) = (p for p in (tmp_path / r.SUBDIR).iterdir() if p.is_dir() and p != first)
    _request(second)
    again.join(60)
    assert json.loads((second / 'result.json').read_text())['status'] == 'ok'
    assert hasattr(sys, r.SENTINEL)  # scanned: this process never arms again
    assert r.start(tmp_path, **fast) is None


def test_concurrent_registrations_arm_one_probe(tmp_path, clean):
    _arm_file(tmp_path, {'until': time.time() + 600})
    barrier = threading.Barrier(8)
    got = []

    def register():
        barrier.wait(10)
        got.append(r.start(tmp_path, argv=GATEWAY))

    workers = [threading.Thread(target=register) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
    armed = [thread for thread in got if thread is not None]
    assert len(got) == 8 and len(armed) == 1
    d = _probe_dir(tmp_path)  # exactly one probe directory
    _request(d)
    armed[0].join(60)
    assert (d / 'result.json').exists()


def test_a_thread_that_cannot_start_does_not_hold_the_claim(tmp_path, clean, monkeypatch):
    _arm_file(tmp_path, {'until': time.time() + 600})

    def refuse(self):
        raise RuntimeError("can't start new thread")

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, 'start', refuse)
        assert r.start(tmp_path, argv=GATEWAY) is None
    assert not hasattr(sys, r.SENTINEL)
    thread = r.start(tmp_path, argv=GATEWAY)
    assert thread is not None
    (tmp_path / r.SUBDIR / r.ARM).unlink()


def test_a_rewritten_arm_file_moves_the_window_and_can_exclude_this_kind(tmp_path, clean):
    now = [1000.0]
    _arm_file(tmp_path, {'until': 1010.0})
    fast = dict(argv=GATEWAY, recheck_s=0.0, sleep=lambda s: time.sleep(0.01), wall=lambda: now[0])
    thread = r.start(tmp_path, **fast)
    assert thread is not None
    _arm_file(tmp_path, {'until': 5000.0})
    time.sleep(0.2)
    now[0] = 2000.0  # past the first window, inside the rewritten one
    time.sleep(0.2)
    assert thread.is_alive()
    _arm_file(tmp_path, {'until': 5000.0, 'kinds': ['serve']})
    thread.join(10)
    assert not thread.is_alive() and not hasattr(sys, r.SENTINEL)


def test_an_arm_file_that_is_not_private_does_not_arm(tmp_path, clean, caplog):
    _arm_file(tmp_path, {'until': time.time() + 600}, mode=0o644)
    with caplog.at_level('WARNING', logger='loop-watch'):
        assert r.start(tmp_path, argv=GATEWAY) is None
    assert 'not a private regular 0600 file' in caplog.text


def test_a_symlinked_or_oversized_arm_file_does_not_arm(tmp_path, clean):
    target = tmp_path / 'elsewhere'
    target.write_text(json.dumps({'until': time.time() + 600}))
    target.chmod(0o600)
    (tmp_path / r.SUBDIR).mkdir()
    (tmp_path / r.SUBDIR / r.ARM).symlink_to(target)
    assert r.start(tmp_path, argv=GATEWAY) is None
    (tmp_path / r.SUBDIR / r.ARM).unlink()
    _arm_file(tmp_path, raw=json.dumps({'until': time.time() + 600}).encode() + b' ' * r.MAX_ARM_BYTES)
    assert r.start(tmp_path, argv=GATEWAY) is None


@pytest.mark.parametrize('argv,kinds,arms', [
    (GATEWAY, None, True), (SERVE, None, False), (['hermes', 'chat'], None, False),
    (SERVE, ['serve'], True), (GATEWAY, ['serve'], False), (GATEWAY, ['bogus'], True),
    (['hermes', 'dashboard'], ['dashboard', 'gateway'], True)])
def test_only_the_named_process_kinds_arm(tmp_path, clean, argv, kinds, arms):
    spec = {'until': time.time() + 600}
    if kinds is not None:
        spec['kinds'] = kinds
    _arm_file(tmp_path, spec)
    thread = r.start(tmp_path, argv=argv)
    assert (thread is not None) == arms
    if thread:
        _request(_probe_dir(tmp_path))
        thread.join(30)


def test_one_shot_request_writes_a_private_capped_result(tmp_path, clean, caplog):
    _arm_file(tmp_path, {'until': time.time() + 600})
    with caplog.at_level('WARNING', logger='loop-watch'):
        thread = r.start(tmp_path, argv=GATEWAY)
    assert thread is not None and 'roots census armed' in caplog.text
    assert r.start(tmp_path, argv=GATEWAY) is None  # a plugin reload registers again: still one probe
    d = _probe_dir(tmp_path)
    assert json.loads((d / 'ready.json').read_text())['command'] == 'roots-census-v1'
    _request(d)
    thread.join(60)
    assert not thread.is_alive()
    out = d / 'result.json'
    assert (out.stat().st_mode & 0o777) == 0o600
    data = out.read_bytes()
    assert len(data) <= r.MAX_OUTPUT
    report = json.loads(data)
    assert report['kind'] == 'roots_census' and report['status'] == 'ok' and report['top']
    (d / 'request').write_bytes(r.COMMAND)  # the one opportunity is spent
    (d / 'request').chmod(0o600)
    time.sleep(1.5)
    assert (d / 'request').exists()


def test_the_probe_stops_waiting_at_until(tmp_path, clean):
    _arm_file(tmp_path, {'until': time.time() + 600})
    now = [time.time()]
    thread = r.start(tmp_path, argv=GATEWAY, wall=lambda: now[0], sleep=lambda s: time.sleep(0.01), recheck_s=1e9)
    assert thread is not None and thread.is_alive()
    now[0] += 601
    thread.join(10)
    assert not thread.is_alive()
    d = _probe_dir(tmp_path)
    _request(d)  # past until nobody consumes a request
    time.sleep(0.2)
    assert (d / 'request').exists() and not (d / 'result.json').exists()


def test_a_wrong_command_spends_the_request_without_a_scan(tmp_path, clean):
    _arm_file(tmp_path, {'until': time.time() + 600})
    thread = r.start(tmp_path, argv=GATEWAY)
    d = _probe_dir(tmp_path)
    time.sleep(0.01)
    (d / 'request').write_bytes(b'heap-census-v1\n')
    (d / 'request').chmod(0o600)
    thread.join(10)
    assert not thread.is_alive() and not (d / 'result.json').exists()


def test_register_arms_from_the_arm_file_in_a_real_gateway_process_only(tmp_path, clean):
    """Through loop-watch's register() in real child processes, with no env opt-in at all."""
    code = (
        "import importlib.util, logging, os, sys, threading\n"
        "logging.basicConfig(level=logging.WARNING, stream=sys.stdout)\n"
        "sys.argv = ['hermes'] + sys.argv[1:]\n"
        f"spec = importlib.util.spec_from_file_location('lw', {str(_dir / '__init__.py')!r})\n"
        "lw = importlib.util.module_from_spec(spec); spec.loader.exec_module(lw)\n"
        "lw.watch.start = lambda *a, **k: None\n"
        "lw.register(None)\n"
        "lw.register(None)\n"
        "print('THREADS', sum(t.name == 'loop-watch-roots-census' for t in threading.enumerate()))\n"
    )
    base = {k: v for k, v in os.environ.items() if not k.startswith('HERMES_')}

    def run(argv, armed=True):
        home = tmp_path / f'home{len(list(tmp_path.iterdir()))}'
        home.mkdir()
        if armed:
            _arm_file(home, {'until': time.time() + 600})
        out = subprocess.run([sys.executable, '-c', code, *argv], env={**base, 'HERMES_HOME': str(home)},
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        return out.stdout

    assert 'THREADS 0' in run(['gateway', 'run'], armed=False)
    serve = run(['serve'])
    assert 'THREADS 0' in serve and 'roots census not armed' in serve
    gateway = run(['gateway', 'run'])
    assert 'THREADS 1' in gateway and 'roots census armed' in gateway
