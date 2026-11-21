"""Leading-edge send, coalescing and opt-in server stage timing."""
import time

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


def test_changes_during_a_slow_projection_coalesce_into_one_immediate_follow_up(monkeypatch):
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
            gap = second['timing']['project_start'] - first['timing']['project_end']
            assert 0 <= gap < .2, gap


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
