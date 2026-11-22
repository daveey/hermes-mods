"""Leading-edge send, coalescing and opt-in server stage timing."""
import time
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_view import load


def stream_app(api, monkeypatch, state, compute_s=0.0, calls=None):
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    monkeypatch.setattr(api, 'change_cursor', lambda: state['cursor'])

    def view(*args):
        seen = state['status']
        if calls is not None:
            calls.append(seen)
        time.sleep(compute_s)
        return {'status': seen}
    monkeypatch.setattr(api, 'lane_view', view)
    monkeypatch.setattr(api, 'WATCH_S', .02)
    monkeypatch.setattr(api, 'MIN_PROJECT_GAP_S', .1, raising=False)
    monkeypatch.setattr(api, 'HEARTBEAT_S', 30)
    app = FastAPI()
    app.include_router(api.router)
    return app


def test_first_dirty_tick_after_quiet_projects_without_a_fixed_wait(monkeypatch):
    api = load()
    state = {'status': 'a', 'cursor': 0}
    with TestClient(stream_app(api, monkeypatch, state)) as client:
        with client.websocket_connect('/v1/lanes/stream') as ws:
            assert ws.receive_json()['view']['status'] == 'a'
            time.sleep(.3)
            start = time.monotonic()
            state.update(status='b', cursor=1)
            assert ws.receive_json()['view']['status'] == 'b'
            assert time.monotonic() - start < .5, 'the old 1 s trailing debounce is back'


def test_changes_during_a_slow_projection_coalesce_into_one_gapped_follow_up(monkeypatch):
    api = load()
    state = {'status': 'a', 'cursor': 0}
    calls = []
    with TestClient(stream_app(api, monkeypatch, state, compute_s=.4, calls=calls)) as client:
        with client.websocket_connect('/v1/lanes/stream?debug=true') as ws:
            assert ws.receive_json()['view']['status'] == 'a'
            state.update(status='b', cursor=1)
            time.sleep(.15)
            for step in range(2, 7):
                state.update(status=f's{step}', cursor=step)
                time.sleep(.02)
            first = ws.receive_json()
            second = ws.receive_json()
            assert second['view']['status'] == 's6'
            assert len(calls) == 3, calls
            gap = second['timing']['project_start'] - first['timing']['project_start']
            # Compute exceeds the gap: follow-up starts when the active job ends,
            # not another whole gap after that end.
            assert .4 <= gap < .49, gap


def test_continuous_churn_bounds_projection_duty_and_preserves_latest(monkeypatch):
    api = load()
    state = {'status': '0', 'cursor': 0}
    starts = []
    app = stream_app(api, monkeypatch, state, compute_s=.12, calls=starts)
    # Scaled production case: a 1.2 s projection and revisions every .95 s.
    monkeypatch.setattr(api, 'WATCH_S', .025)
    stop = threading.Event()

    def churn():
        for revision in range(1, 12):
            if stop.wait(.095):
                return
            state.update(status=str(revision), cursor=revision)

    with TestClient(app) as client:
        with client.websocket_connect('/v1/lanes/stream?debug=true') as ws:
            frames = [ws.receive_json()]
            thread = threading.Thread(target=churn)
            thread.start()
            try:
                while frames[-1]['view']['status'] != '11':
                    frames.append(ws.receive_json())
            finally:
                stop.set()
                thread.join()
            assert len(starts) <= 12, starts
            for first, second in zip(frames, frames[1:]):
                gap = second['timing']['project_start'] - first['timing']['project_start']
                assert gap >= .1, gap
            time.sleep(.15)
            monkeypatch.setattr(api, 'WATCH_S', .25)
            changed = time.monotonic()
            state.update(status='quiet', cursor=12)
            assert ws.receive_json()['view']['status'] == 'quiet'
            assert time.monotonic() - changed < .6


def test_debug_viewer_census_includes_plain_clients_and_releases_on_disconnect(monkeypatch):
    import os
    api = load()
    state = {'status': 'a', 'cursor': 0}
    with TestClient(stream_app(api, monkeypatch, state)) as client:
        with client.websocket_connect('/v1/lanes/stream') as plain:
            assert 'load' not in plain.receive_json()
            with client.websocket_connect('/v1/lanes/stream?debug=true') as debug:
                assert debug.receive_json()['load'] == {'pid': os.getpid(), 'stream_viewers': 2}
            # A new debug frame after the second context closes must see only
            # itself and the original plain viewer, not the disconnected one.
            with client.websocket_connect('/v1/lanes/stream?debug=true') as debug:
                assert debug.receive_json()['load']['stream_viewers'] == 2
        with client.websocket_connect('/v1/lanes/stream?debug=true') as debug:
            assert debug.receive_json()['load']['stream_viewers'] == 1
    assert api._active_streams == 0


def test_debug_viewer_census_releases_on_projection_error(monkeypatch):
    api = load()
    state = {'status': 'a', 'cursor': 0}
    app = stream_app(api, monkeypatch, state)
    def broken(*args):
        raise RuntimeError('controlled projection failure')
    monkeypatch.setattr(api, 'lane_view', broken)
    from starlette.websockets import WebSocketDisconnect
    import pytest
    with TestClient(app) as client:
        with client.websocket_connect('/v1/lanes/stream?debug=true') as ws:
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert api._active_streams == 0


def test_debug_frames_carry_ordered_server_stage_timings_only_on_request(monkeypatch):
    api = load()
    state = {'status': 'a', 'cursor': 0}
    with TestClient(stream_app(api, monkeypatch, state, compute_s=.05)) as client:
        with client.websocket_connect('/v1/lanes/stream') as ws:
            ws.receive_json()
            state.update(status='b', cursor=1)
            assert 'timing' not in ws.receive_json()
        with client.websocket_connect('/v1/lanes/stream?debug=true') as ws:
            ws.receive_json()
            state.update(status='c', cursor=2)
            t = ws.receive_json()['timing']
            order = [t[k] for k in ('cursor_poll', 'detected', 'released', 'project_start', 'project_end', 'sent')]
            assert order == sorted(order) and t['project_end'] - t['project_start'] >= .05, t
