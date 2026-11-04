import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from tests_reqtrace import rt


def req(path='/api/sessions'):
    return rt._Req(1, 'GET', path, 0, None)


def totals(rows):
    seen = set()
    n = slow = 0
    for r in rows:
        if r['kind'] != 'request_stats':
            continue
        identity = r.get('record_id', id(r))
        if identity in seen:
            continue
        seen.add(identity)
        for s in r['paths'].values():
            n += s['n']
            slow += s['slow']
    return n, slow


def test_repeated_flush_rollover_and_replacement():
    rows = []
    for _ in range(2):
        t = rt.ReqTrace(sink=rows.append)
        t._record_stats(req(), 3, 100)
        t.flush_stats()
        t.flush_stats()
        t._record_stats(req(), .1, 100)
        t._record_stats(req(), 3, 101)
        t.close_stats()
        t.close_stats()
    assert totals(rows) == (6, 4)
    stats = [r for r in rows if r['kind'] == 'request_stats']
    assert len({r['record_id'] for r in stats}) == len(stats)
    assert {r['hour_epoch'] for r in stats} == {360000, 363600}


def test_all_path_counts_survive_emit_cap():
    rows = []
    t = rt.ReqTrace(sink=rows.append)
    for i in range(rt.STATS_MAX_PATHS + 20):
        t._record_stats(req('/route/%d' % i), 3, 100)
    t.flush_stats()
    assert totals(rows) == (rt.STATS_MAX_PATHS + 20,) * 2


def test_concurrent_record_and_flush_exactly_once():
    rows = []
    t = rt.ReqTrace(sink=rows.append)
    def produce():
        for i in range(300):
            t._record_stats(req(), 3 if i % 2 else .1, 100 + i // 150)
    def flush():
        for _ in range(60):
            t.flush_stats()
    threads = [threading.Thread(target=produce) for _ in range(6)] + [threading.Thread(target=flush)]
    for th in threads: th.start()
    for th in threads: th.join()
    t.close_stats()
    assert totals(rows) == (1800, 900)


def test_failed_sink_retry_has_stable_identity_and_hour():
    rows = []
    failing = [True]
    def sink(r):
        rows.append(r)
        if failing[0] and r['kind'] == 'request_stats':
            raise OSError('ambiguous write')
    t = rt.ReqTrace(sink=sink)
    t._record_stats(req(), 3, 100)
    t.flush_stats()
    t._record_stats(req(), .1, 101)
    failing[0] = False
    t.flush_stats()
    assert totals(rows) == (2, 1)
    assert rows[0]['record_id'] == rows[1]['record_id']


def test_real_process_shutdown_restart_and_hard_death(tmp_path):
    out = tmp_path / 'stats.jsonl'
    code = '''
import importlib.util, sys, time
from pathlib import Path
s = importlib.util.spec_from_file_location('rt', sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
t = m.ReqTrace(sink=m.default_sink(Path(sys.argv[2])))
t.start_stats(interval_s=.05)
t._record_stats(m._Req(1, 'GET', '/probe', 0, None), 3, 100)
if sys.argv[3] == 'graceful':
    sys.exit(0)
while not any('request_stats"' in line for line in Path(sys.argv[2]).read_text().splitlines()):
    time.sleep(.01)
t._record_stats(m._Req(2, 'GET', '/probe', 0, None), .1, 100)
print('ready', flush=True)
time.sleep(60)
'''
    args = [sys.executable, '-c', code, str(Path(rt.__file__)), str(out)]
    for _ in range(2):
        subprocess.run(args + ['graceful'], check=True, timeout=10)
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert totals(rows) == (2, 2)
    out.unlink()
    p = subprocess.Popen(args + ['hard'], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == 'ready'
        p.kill(); p.wait(timeout=10)
    finally:
        if p.poll() is None: p.kill(); p.wait()
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert totals(rows)[1] == 1
    assert 1 <= totals(rows)[0] <= 2  # kill may race the next flush; no duplicates
    assert not any(r.get('reason') == 'shutdown' for r in rows)
    assert any(r.get('interval_s') == .05 for r in rows)


def test_timer_flushes_without_watcher_tick_and_lifespan_closes():
    import asyncio
    rows = []
    flushed = threading.Event()
    def sink(r):
        rows.append(r)
        if r['kind'] == 'request_stats':
            flushed.set()
    t = rt.ReqTrace(sink=sink)
    t.start_stats(interval_s=.02)
    try:
        t._record_stats(req(), 3, 100)
        assert flushed.wait(2)  # no tick / no running event loop
        async def lifespan(scope, receive, send):
            t._record_stats(req(), .1, 100)
        asyncio.run(t.wrap(lifespan)({'type': 'lifespan'}, None, None))
        assert totals(rows) == (2, 1)
        assert rows[-1]['reason'] == 'shutdown'
        t._record_stats(req(), .1, 100)  # draining old app after teardown
        assert totals(rows) == (3, 1)
    finally:
        t.close_stats()
        t._stats_thread.join(2)
    assert not t._stats_thread.is_alive()


def test_outage_memory_bound_and_explicit_loss():
    rows = []
    failure = [True]
    def sink(r):
        if failure[0]: raise OSError('storage offline')
        rows.append(r)
    t = rt.ReqTrace(sink=sink)
    for hour in range(10, 20):
        t._record_stats(req(), 3, hour)
    assert len(t._stats) <= 2
    failure[0] = False
    t.close_stats()
    assert totals(rows)[0] + rows[-1]['dropped_requests'] == 10
    assert rows[-1]['dropped_requests'] > 0


def test_default_sink_failure_is_not_silently_acknowledged(tmp_path, monkeypatch):
    out = tmp_path / 'stats.jsonl'
    t = rt.ReqTrace(sink=rt.default_sink(out))
    t._record_stats(req(), 3, 100)
    real = rt.os.fsync
    def fail(_): raise OSError('fsync failed')
    monkeypatch.setattr(rt.os, 'fsync', fail)
    t.flush_stats()
    assert t._stats_pending is not None
    monkeypatch.setattr(rt.os, 'fsync', real)
    t.close_stats()
    assert totals([json.loads(x) for x in out.read_text().splitlines()]) == (1, 1)
