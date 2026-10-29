"""Exercise both provider paths against a fake HTTP upstream and rotating disk credentials."""
import json
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _load import load
from test_rotation import FakePool, HTTPErr

pl = load("pool_llm")


class DiskPool(FakePool):
    def __init__(self, path, ids):
        super().__init__(ids)
        self.path = path
        self.refreshes = []
        self.save({i: "old-" + i for i in ids})
        for entry in self._entries:
            entry.runtime_api_key = "old-" + entry.id

    def save(self, tokens):
        self.path.write_text(json.dumps(tokens))

    def try_refresh_matching(self, *, credential_id):
        self.refreshes.append(credential_id)
        entry = next(e for e in self._entries if e.id == credential_id)
        entry.runtime_api_key = json.loads(self.path.read_text())[entry.id]
        return entry


@contextmanager
def upstream(pool, *, persistent=False):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            token = self.headers["authorization"]
            seen.append(token)
            failed = token == "old-a" or (persistent and token == "new-a")
            if failed:
                # Another process has replaced the stored credential while this
                # daemon's pool still holds the old one.
                pool.save({e.id: "new-" + e.id for e in pool.entries()})
            payload = json.dumps({"error": "OAuth access token has been revoked"} if failed else {"ok": True}).encode()
            self.send_response(401 if failed else 200)
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def wire_client(entry, url):
    # Capture token at client construction, as the real SDKs do. A retry must
    # construct a new client rather than reuse one with the revoked token.
    token = entry.runtime_api_key

    def request(**body):
        status, raw = pl._http_post(url, {"authorization": token}, json.dumps(body).encode())
        if status != 200:
            raise HTTPErr(status, raw.decode())
        return NS(model_dump=lambda **kw: json.loads(raw))

    @contextmanager
    def stream(**body):
        result = request(**body)
        yield NS(get_final_response=lambda: result)

    return NS(messages=NS(create=request), responses=NS(stream=stream))


@pytest.mark.parametrize("provider", ["anthropic", "codex"])
@pytest.mark.parametrize("persistent", [False, True])
def test_same_daemon_recovers_or_rotates(provider, persistent, tmp_path, monkeypatch):
    pool = DiskPool(tmp_path / "tokens.json", ["a", "b"])
    route = {}
    with upstream(pool, persistent=persistent) as (url, seen):
        monkeypatch.setattr(pl, provider + "_client", lambda entry: wire_client(entry, url))
        monkeypatch.setattr(pl, "load_pool", lambda provider: pool)
        monkeypatch.setattr(pl, "resolve_model", lambda *args: "fixture")
        import http.server
        import urllib.request
        ready = threading.Event()
        servers = []

        def create_server(*args):
            server = ThreadingHTTPServer(*args)
            servers.append(server)
            ready.set()
            return server

        monkeypatch.setattr(http.server, "ThreadingHTTPServer", create_server)
        daemon = threading.Thread(target=pl.serve, args=("127.0.0.1", 0), daemon=True)
        daemon.start()
        assert ready.wait(5)
        body = {"model": "fixture", "messages": [], "input": "ping", "max_tokens": 10}
        path = "/v1/messages" if provider == "anthropic" else "/v1/responses"
        try:
            for _ in range(2):  # same running serve instance, no restart
                req = urllib.request.Request(f"http://127.0.0.1:{servers[0].server_port}{path}",
                                             data=json.dumps(body).encode(), method="POST")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    assert resp.status == 200
                    assert json.load(resp) == {"ok": True}
                    route = json.loads(resp.headers["x-hermes-pool-route"])
        finally:
            servers[0].shutdown()
            servers[0].server_close()
            daemon.join(timeout=5)
    assert pool.refreshes == ["a"]
    assert pool.exhausted == (["a"] if persistent else [])
    assert seen == (["old-a", "new-a", "old-b", "old-b"] if persistent else ["old-a", "new-a", "new-a"])
    assert route["account"] == ("acct-b" if persistent else "acct-a")


@pytest.mark.parametrize("message", ["token has been revoked", "invalid_grant"])
def test_auth_text_without_401_recovers_once(message, tmp_path):
    pool = DiskPool(tmp_path / "tokens.json", ["a", "b"])
    seen = []

    def call(entry):
        seen.append(entry.id)
        if entry.id == "a":
            raise RuntimeError(message)
        return "ok"

    assert pl.with_rotation(call, pool) == "ok"
    assert seen == ["a", "a", "b"]
    assert pool.refreshes == ["a"]


@pytest.mark.parametrize("recovery", ["none", "raises"])
def test_failed_refresh_rotates_without_unbounded_retry(recovery, tmp_path, monkeypatch):
    pool = DiskPool(tmp_path / "tokens.json", ["a", "b"])

    def refresh(**kwargs):
        if recovery == "raises":
            raise RuntimeError("invalid_grant")
        return None

    monkeypatch.setattr(pool, "try_refresh_matching", refresh)
    seen = []

    def call(entry):
        seen.append(entry.id)
        raise HTTPErr(401, "revoked")

    with pytest.raises(pl.PoolExhausted):
        pl.with_rotation(call, pool)
    assert seen == ["a", "b"]
    assert pool.exhausted == ["a", "b"]


def test_non_auth_retry_failure_is_not_hidden(tmp_path):
    pool = DiskPool(tmp_path / "tokens.json", ["a", "b"])
    seen = []

    def call(entry):
        seen.append(entry.id)
        raise HTTPErr(401 if len(seen) == 1 else 400, "bad request")

    with pytest.raises(HTTPErr) as exc:
        pl.with_rotation(call, pool)
    assert exc.value.status_code == 400
    assert seen == ["a", "a"]
    assert pool.exhausted == []
