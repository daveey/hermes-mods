"""Composed dispatcher -> hub -> Desktop slider -> real config/flow readback."""
import copy
import http.server
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

base = pytest.importorskip('tests.hermes_cli.test_kanban_lanes')
pytest.importorskip('hermes_cli.kanban_lane_samples')
from hermes_cli import kanban_db as kb, kanban_lanes as lanes
from hermes_cli.kanban_db_dispatch import dispatch_once
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_metrics_core import api_module

conn = base.conn
configured = base.configured
person = base.person


def test_failed_flow_limits_disabled_through_real_projection_dom(tmp_path):
    root = Path(__file__).resolve().parents[3]
    node_tests = root / 'desktop-plugins/lanes/tests'
    if not shutil.which('node') or not (node_tests / 'node_modules/jsdom').exists():
        pytest.skip('pinned Node DOM dependencies unavailable')
    api = api_module()
    snap = dict(configured=True, lanes={'babybear': dict(target=.3, actual=.2, open=1)},
                flow_complete=False, flow_error='board-a: interrupted')
    projected = api._sibling('view').project(snap, dict(configured=False, reason='board-a: interrupted'), True)
    presented = api._sibling('metrics').present(projected, 24)
    assert 'limits' not in presented['lanes']['babybear']
    path = tmp_path / 'failed-flow.json'
    path.write_text(json.dumps(presented))
    result = subprocess.run(['node', str(node_tests / 'render-unknown-cap.mjs'), str(path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_dispatcher_red_dom_and_pointer_api_core_readback(conn, configured, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    node_tests = root / 'desktop-plugins/lanes/tests'
    if not shutil.which('node') or not (node_tests / 'node_modules/jsdom').exists():
        pytest.skip('pinned Node DOM dependencies unavailable')
    mapping = copy.deepcopy(base.LANES)
    mapping['babybear']['max_workers'] = 1
    configured(mapping)
    assert kb.claim_task(conn, base._ready_in(conn, 'babybear', 'running'))
    base._ready_in(conn, 'babybear', 'waiting')
    dispatch_once(conn, spawn_fn=lambda *_a, **_k: pytest.fail('cap prevents spawn'),
                  max_spawn=2, max_in_progress=10, reconcile_orphans=False)
    api = api_module()
    monkeypatch.setattr(api._sibling('metrics'), 'fleet_slots', lambda: 6)
    app = FastAPI()
    app.include_router(api.router)
    seen = []
    with TestClient(app) as client:
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, response):
                self.send_response(response.status_code)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(response.content)

            def do_GET(self):
                self.reply(client.get(self.path))

            def do_PUT(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                response = client.put(self.path, json=payload)
                if response.status_code == 200:
                    # Independent core policy read after the real config writer.
                    cap = lanes.policy().lanes['babybear'].max_workers
                    assert cap == payload['max_workers']
                    seen.append(cap)
                self.reply(response)

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(['node', str(node_tests / 'render-core-controls.mjs'),
                                     f'http://127.0.0.1:{server.server_port}'],
                                    capture_output=True, text=True, timeout=45)
            assert result.returncode == 0, result.stdout + result.stderr
            assert seen == [2, None, 0]
            assert lanes.policy().lanes['babybear'].max_workers == 0
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
            if api._observations.cache_info().currsize:
                api._observations().join(2)
