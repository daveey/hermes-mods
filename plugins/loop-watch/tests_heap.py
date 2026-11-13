import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import tracemalloc

import pytest

_dir = Path(__file__).parent
spec = importlib.util.spec_from_file_location('heap_test', _dir / 'heap.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
cspec = importlib.util.spec_from_file_location('heap_census_test', _dir / 'census.py')
c = importlib.util.module_from_spec(cspec)
cspec.loader.exec_module(c)


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.delattr(sys, '_hermes_loop_watch_heap_census', raising=False)
    was = tracemalloc.is_tracing()
    yield
    monkeypatch.delattr(sys, '_hermes_loop_watch_heap_census', raising=False)
    if tracemalloc.is_tracing() and not was:
        tracemalloc.stop()


GATEWAY = ['hermes', 'gateway', 'run', '--external-supervisor']
SERVE = ['hermes', 'serve', '--isolated']
UNTIL = int(time.time()) + 3600


def arm(home, env, **kwargs):
    """A complete opt-in: frames + a future UNTIL, in a gateway process (each overridable)."""
    kwargs.setdefault('argv', GATEWAY)
    return h.start(home, env={h.UNTIL_ENV: str(UNTIL), **env}, **kwargs)


def _probe_dir(home):
    (d,) = (p for p in (home / 'loop-watch-heap').iterdir() if p.is_dir())
    return d


def _rearm_as_new_process(monkeypatch):
    """What a restart resets: the per-process sentinel. The env and the home survive."""
    monkeypatch.delattr(sys, '_hermes_loop_watch_heap_census', raising=False)


def _hold():
    return [bytes(4096) + str(i).encode() for i in range(512)]  # ~2 MB from one line


@pytest.mark.parametrize('value,expected', [(None, 0), ('', 0), ('x', 0), ('0', 0), ('33', 0), ('1', 1), ('8', 8)])
def test_frames_env(value, expected):
    assert h.frames({} if value is None else {h.ENV: value}) == expected


def test_default_off_leaves_tracing_and_disk_alone(tmp_path, clean):
    assert h.start(tmp_path, env={}) is None
    assert not tracemalloc.is_tracing()
    assert list(tmp_path.iterdir()) == []


def test_unknown_when_not_tracing(clean):
    report = h.scan()
    assert report['status'] == 'unknown' and report['by_line'] == []


def test_scan_attributes_a_held_allocation_to_its_line(clean):
    tracemalloc.start(8)
    held = _hold()
    report = h.scan()
    assert report['status'] == 'ok' and report['traces'] > 0
    line = _hold.__code__.co_firstlineno + 1
    key = f"{h._short(str(_dir / 'tests_heap.py'))}:{line}"
    top_app = {k: (size, n) for k, size, n in report['by_app_line']}
    assert key in top_app and top_app[key][0] >= 512 * 4096 and top_app[key][1] >= 512
    assert report['traced_bytes'] >= 512 * 4096
    del held


def test_report_has_no_values(clean):
    tracemalloc.start(4)
    secret = ['TOP-SECRET-%d' % i for i in range(1000)]
    text = json.dumps(h.scan())
    assert 'TOP-SECRET' not in text
    del secret


def test_one_shot_request_writes_capped_result(tmp_path, clean):
    thread = arm(tmp_path, env={h.ENV: '4'})
    assert thread is not None and tracemalloc.is_tracing()
    assert arm(tmp_path, env={h.ENV: '4'}) is None  # process-wide sentinel
    d = _probe_dir(tmp_path)
    assert json.loads((d / 'ready.json').read_text())['command'] == 'heap-census-v1'
    time.sleep(0.01)
    req = d / 'request'
    req.write_bytes(h.COMMAND)
    req.chmod(0o600)
    thread.join(15)
    assert not thread.is_alive()
    out = d / 'result.json'
    assert (out.stat().st_mode & 0o777) == 0o600
    data = out.read_bytes()
    assert len(data) <= h.MAX_OUTPUT
    report = json.loads(data)
    assert report['kind'] == 'heap_census' and report['status'] == 'ok'
    assert report['by_line'] and report['by_app_entry']
    assert not tracemalloc.is_tracing()  # one-shot: tracing stops after the report


def test_failing_scan_still_writes_unknown(monkeypatch, clean):
    def boom():
        raise MemoryError
    monkeypatch.setattr(h, 'scan', boom)
    assert h.safe_scan() == {'schema': 1, 'kind': 'heap_census', 'status': 'unknown', 'error': 'MemoryError'}


def test_probe_failure_stops_tracing(tmp_path, clean):
    def broken(*a, **k):
        raise OSError('no dir')
    assert arm(tmp_path, env={h.ENV: '2'}, probe_factory=broken) is None
    assert not tracemalloc.is_tracing()


def test_cache_command_does_not_trigger_heap_probe(tmp_path, clean):
    probe = c.Probe(tmp_path, command=h.COMMAND, scanner=h.scan, max_output=h.MAX_OUTPUT, subdir='loop-watch-heap')
    try:
        time.sleep(0.01)
        req = probe.path / 'request'
        req.write_bytes(c.COMMAND)
        req.chmod(0o600)
        probe.tick()
        assert probe.used and not (probe.path / 'result.json').exists()
    finally:
        probe.close()


def test_cache_probe_defaults_unchanged(tmp_path):
    probe = c.Probe(tmp_path, runner=lambda: None)
    try:
        assert probe.path.parent.name == 'loop-watch-census'
        assert json.loads((probe.path / 'ready.json').read_text())['command'] == 'cache-census-v1'
    finally:
        probe.close()


@pytest.mark.parametrize('value,expected', [(None, 21600.0), ('', 21600.0), ('x', 21600.0), ('0', 21600.0),
                                            ('-5', 21600.0), ('nan', 21600.0), ('inf', 21600.0),
                                            ('60', 60.0), ('0.5', 0.5)])
def test_ttl_env(value, expected):
    assert h.ttl({} if value is None else {h.TTL_ENV: value}) == expected


class FakeClock:
    """Monotonic clock that only moves when the census thread sleeps; no real waiting.

    With `hold`, the first sleep waits until the test sets it: the thread can otherwise run the
    whole fake TTL (and stop tracing) before the test observes the armed state under load."""
    def __init__(self, on_tick=None, hold=None):
        self.now = 1000.0
        self.on_tick = on_tick
        self.sleeps = 0
        self.hold = hold

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        if self.hold is not None:
            assert self.hold.wait(15), 'test never released the census thread'
        self.now += seconds
        self.sleeps += 1
        if self.on_tick:
            self.on_tick(self.now)


def _request(d):
    req = d / 'request'
    req.write_bytes(h.COMMAND)
    req.chmod(0o600)


def test_tracing_stops_at_ttl_without_request(tmp_path, clean, caplog):
    clock = FakeClock(hold=threading.Event())
    with caplog.at_level('WARNING', logger='loop-watch'):
        thread = arm(tmp_path, env={h.ENV: '2', h.TTL_ENV: '60'}, clock=clock, sleep=clock.sleep)
        assert thread is not None and tracemalloc.is_tracing()
        clock.hold.set()
        thread.join(15)
    assert not thread.is_alive()
    assert not tracemalloc.is_tracing()  # loop-watch started it, so the TTL stopped it
    assert 60 <= clock.sleeps <= 61  # expired at the TTL, not before or long after
    d = _probe_dir(tmp_path)
    report = json.loads((d / 'result.json').read_text())
    assert report == {'schema': 1, 'kind': 'heap_census', 'status': 'expired', 'ttl_s': 60.0, 'until': UNTIL, 'frames': 2,
                      'tracing_left_on': False}
    assert ((d / 'result.json').stat().st_mode & 0o777) == 0o600
    _request(d)  # the opportunity is spent: a late request is never consumed
    assert (d / 'request').exists() and not (d / 'consumed').exists()
    start_line = next(r.getMessage() for r in caplog.records if 'armed' in r.getMessage())
    assert h.ENV + '=2' in start_line and f'pid {os.getpid()}' in start_line
    assert '60s' in start_line and h.TTL_ENV in start_line
    assert any('expired' in r.getMessage() for r in caplog.records)


def test_request_before_ttl_still_reports(tmp_path, clean):
    box = {}

    def on_tick(now):
        if now == 1030.0:  # half way through the TTL
            _request(box['dir'])
    clock = FakeClock(on_tick)
    # The probe directory exists once start() returns, but the thread may already be sleeping; hand it over
    # through a factory so the request lands at a fixed fake time.
    real = c.Probe

    def factory(*a, **k):
        probe = real(*a, **k)
        box['dir'] = probe.path
        return probe
    thread = arm(tmp_path, env={h.ENV: '4', h.TTL_ENV: '60'}, probe_factory=factory, clock=clock,
                     sleep=clock.sleep)
    thread.join(15)
    assert not thread.is_alive()
    assert clock.sleeps == 30
    report = json.loads((box['dir'] / 'result.json').read_text())
    assert report['kind'] == 'heap_census' and report['status'] == 'ok' and report['by_line']
    assert not tracemalloc.is_tracing()


def test_ttl_never_stops_tracing_started_elsewhere(tmp_path, clean, caplog):
    tracemalloc.start(1)
    clock = FakeClock()
    with caplog.at_level('WARNING', logger='loop-watch'):
        thread = arm(tmp_path, env={h.ENV: '3', h.TTL_ENV: '5'}, clock=clock, sleep=clock.sleep)
        thread.join(15)
    assert not thread.is_alive()
    assert tracemalloc.is_tracing() and tracemalloc.get_traceback_limit() == 1  # untouched
    d = _probe_dir(tmp_path)
    report = json.loads((d / 'result.json').read_text())
    assert report['status'] == 'expired' and report['tracing_left_on'] is True
    assert any('already on' in r.getMessage() for r in caplog.records)
    tracemalloc.stop()


# --- the opt-in is one-shot per arming (window, process kind, one claim per home) ---

@pytest.mark.parametrize('value,expected', [
    (None, 0.0), ('', 0.0), ('x', 0.0), ('nan', 0.0), ('inf', 0.0), ('0', 0.0),
    ('999', 0.0), ('1000', 0.0),  # past, and exactly now
    ('1001', 1001.0), ('87400', 87400.0),  # inside the 24 h cap
    ('87401', 0.0), ('1794568835000', 0.0),  # further than 24 h (e.g. a millisecond epoch)
])
def test_until_env(value, expected):
    assert h.until({} if value is None else {h.UNTIL_ENV: value}, now=1000.0) == expected


@pytest.mark.parametrize('argv,expected', [
    (GATEWAY, 'gateway'), (SERVE, 'serve'), (['hermes', 'dashboard'], 'dashboard'),
    (['hermes'], 'other'), (['hermes', 'chat', '-q', 'hi'], 'other'), (['hermes', 'gateway', 'status'], 'other'),
    (['hermes', 'kanban', 'serve'], 'other'), ([], 'other'),
])
def test_process_kind(argv, expected):
    assert h.process_kind(argv) == expected


@pytest.mark.parametrize('value,expected', [
    (None, {'gateway'}), ('', {'gateway'}), ('bogus', {'gateway'}), ('serve', {'serve'}),
    (' Serve , gateway,x', {'serve', 'gateway'}), ('other,dashboard', {'other', 'dashboard'}),
])
def test_kinds_env(value, expected):
    assert h.kinds({} if value is None else {h.KINDS_ENV: value}) == expected


def _assert_not_armed(home, caplog):
    assert not tracemalloc.is_tracing()
    assert not [t for t in threading.enumerate() if t.name == 'loop-watch-heap-census']
    root = home / 'loop-watch-heap'
    assert not root.exists() or not [p for p in root.iterdir() if p.is_dir()]  # no request directory
    messages = [r.getMessage() for r in caplog.records]
    assert not any('heap census armed' in m for m in messages)
    assert any('heap census skipped' in m for m in messages)  # visible, not silent


@pytest.mark.parametrize('stale', [None, '', 'soon', str(int(time.time()) - 1), str(int(time.time()) - 86400),
                                   str(int(time.time()) + 2 * 86400)])
def test_missing_or_stale_until_does_not_arm(tmp_path, clean, caplog, stale):
    env = {h.ENV: '10'} if stale is None else {h.ENV: '10', h.UNTIL_ENV: stale}
    with caplog.at_level('WARNING', logger='loop-watch'):
        assert h.start(tmp_path, env=env, argv=GATEWAY) is None
    _assert_not_armed(tmp_path, caplog)
    assert h.UNTIL_ENV in caplog.text


def test_restarted_process_with_same_env_does_not_arm(tmp_path, clean, caplog, monkeypatch):
    env = {h.ENV: '2', h.TTL_ENV: '3'}
    clock = FakeClock()
    first = arm(tmp_path, env, clock=clock, sleep=clock.sleep)  # the diagnostic process: arms, then expires
    assert first is not None
    first.join(15)
    assert not first.is_alive() and not tracemalloc.is_tracing()
    claim = tmp_path / 'loop-watch-heap' / f'armed-{UNTIL}'
    assert claim.read_text() == f'{os.getpid()}\n' and (claim.stat().st_mode & 0o777) == 0o600
    before = sorted(p.name for p in (tmp_path / 'loop-watch-heap').iterdir())
    _rearm_as_new_process(monkeypatch)  # the restart: same env, same home, still inside the window
    caplog.clear()  # drop the first process's own 'armed'/'expired' lines
    with caplog.at_level('WARNING', logger='loop-watch'):
        assert arm(tmp_path, env) is None
    assert not tracemalloc.is_tracing()
    assert sorted(p.name for p in (tmp_path / 'loop-watch-heap').iterdir()) == before
    assert 'already armed' in caplog.text and 'heap census armed' not in caplog.text


def test_a_new_until_arms_once_more(tmp_path, clean, monkeypatch):
    clock = FakeClock()
    first = arm(tmp_path, {h.ENV: '2', h.TTL_ENV: '2'}, clock=clock, sleep=clock.sleep)
    first.join(15)
    _rearm_as_new_process(monkeypatch)
    clock = FakeClock()
    second = h.start(tmp_path, env={h.ENV: '2', h.TTL_ENV: '2', h.UNTIL_ENV: str(UNTIL + 60)}, argv=GATEWAY,
                     clock=clock, sleep=clock.sleep)
    assert second is not None  # a deliberate new arming (new UNTIL value) is honoured
    second.join(15)
    assert not second.is_alive() and not tracemalloc.is_tracing()


@pytest.mark.parametrize('argv', [SERVE, ['hermes', 'dashboard'], ['hermes'], ['hermes', 'chat', '-q', 'x'],
                                  ['hermes', 'gateway', 'status']])
def test_serve_dashboard_and_cli_processes_do_not_arm(tmp_path, clean, caplog, argv):
    with caplog.at_level('WARNING', logger='loop-watch'):
        assert arm(tmp_path, {h.ENV: '10'}, argv=argv) is None
    _assert_not_armed(tmp_path, caplog)
    assert not (tmp_path / 'loop-watch-heap').exists()  # a skipped kind never spends the home's one claim
    assert h.KINDS_ENV in caplog.text


def test_serve_arms_only_when_the_opt_in_names_it(tmp_path, clean):
    clock = FakeClock()
    thread = arm(tmp_path, {h.ENV: '2', h.TTL_ENV: '2', h.KINDS_ENV: 'serve'}, argv=SERVE, clock=clock,
                 sleep=clock.sleep)
    assert thread is not None
    thread.join(15)
    assert not thread.is_alive() and not tracemalloc.is_tracing()


def test_kinds_naming_serve_excludes_the_gateway(tmp_path, clean, caplog):
    with caplog.at_level('WARNING', logger='loop-watch'):
        assert arm(tmp_path, {h.ENV: '2', h.KINDS_ENV: 'serve'}) is None
    _assert_not_armed(tmp_path, caplog)


def test_ttl_stop_calls_tracemalloc_stop(tmp_path, clean, monkeypatch):
    """Settles the 76090 question: at the TTL the census thread calls tracemalloc.stop() and tracing is off.

    (Gateway 76090 kept tracing past 6 h because it started on the pre-TTL heap.py, which had no expire().)
    """
    calls = []
    real_stop = tracemalloc.stop
    clock = FakeClock()

    def stop():
        calls.append(clock.now)
        real_stop()
    monkeypatch.setattr(h.tracemalloc, 'stop', stop)
    thread = arm(tmp_path, {h.ENV: '2', h.TTL_ENV: '60'}, clock=clock, sleep=clock.sleep)
    thread.join(15)
    assert not thread.is_alive()
    assert calls == [1060.0]  # exactly once, at start + TTL on the census clock
    assert not tracemalloc.is_tracing()
    assert json.loads((_probe_dir(tmp_path) / 'result.json').read_text())['status'] == 'expired'


def test_wall_clock_until_stops_tracing_before_the_ttl(tmp_path, clean, monkeypatch):
    """A monotonic clock that stands still (macOS host asleep) cannot outlive the wall-clock window."""
    calls = []
    real_stop = tracemalloc.stop
    monkeypatch.setattr(h.tracemalloc, 'stop', lambda: (calls.append(1), real_stop()))
    wall = {'now': 5000.0}
    ticks = []

    def sleep(_):
        ticks.append(1)
        wall['now'] += 10
    thread = h.start(tmp_path, env={h.ENV: '2', h.UNTIL_ENV: '5030'}, argv=GATEWAY, clock=lambda: 0.0,
                     sleep=sleep, wall=lambda: wall['now'])
    assert thread is not None
    thread.join(15)
    assert not thread.is_alive() and len(ticks) == 3 and calls == [1]
    assert not tracemalloc.is_tracing()
    report = json.loads((_probe_dir(tmp_path) / 'result.json').read_text())
    assert report['status'] == 'expired' and report['until'] == 5030


def test_unwritable_claim_does_not_arm(tmp_path, clean, caplog):
    (tmp_path / 'loop-watch-heap').write_text('not a directory')
    with caplog.at_level('WARNING', logger='loop-watch'):
        assert arm(tmp_path, {h.ENV: '2'}) is None
    assert not tracemalloc.is_tracing() and 'heap census armed' not in caplog.text


def test_register_gate_reads_the_real_process_environment(tmp_path, clean):
    """Through loop-watch's register() in real child processes: only a gateway inside the window traces."""
    code = (
        "import importlib.util, logging, os, sys, tracemalloc\n"
        "logging.basicConfig(level=logging.WARNING, stream=sys.stdout)\n"
        "sys.argv = ['hermes'] + sys.argv[1:]\n"
        f"spec = importlib.util.spec_from_file_location('lw', {str(_dir / '__init__.py')!r})\n"
        "lw = importlib.util.module_from_spec(spec); spec.loader.exec_module(lw)\n"
        "lw.watch.start = lambda *a, **k: None\n"
        "lw.register(None)\n"
        "print('TRACING', tracemalloc.is_tracing())\n"
    )
    base = {k: v for k, v in os.environ.items() if not k.startswith('HERMES_')}
    base['HERMES_LOOP_WATCH_HEAP_CENSUS'] = '10'
    homes = []

    def run(argv, **extra):
        home = tmp_path / f'home{len(homes)}'
        home.mkdir()
        homes.append(home)
        out = subprocess.run([sys.executable, '-c', code, *argv], env={**base, 'HERMES_HOME': str(home), **extra},
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        return out.stdout

    now = int(time.time())
    stale = run(['serve'], HERMES_LOOP_WATCH_HEAP_CENSUS_UNTIL=str(now - 60))
    assert 'TRACING False' in stale and 'heap census armed' not in stale and 'heap census skipped' in stale
    bare = run(['gateway', 'run'])  # the key alone, as it sat in the mini's .env
    assert 'TRACING False' in bare and 'heap census armed' not in bare
    serve = run(['serve'], HERMES_LOOP_WATCH_HEAP_CENSUS_UNTIL=str(now + 600))
    assert 'TRACING False' in serve and 'heap census armed' not in serve
    gateway = run(['gateway', 'run'], HERMES_LOOP_WATCH_HEAP_CENSUS_UNTIL=str(now + 600))
    assert 'TRACING True' in gateway and 'heap census armed' in gateway
