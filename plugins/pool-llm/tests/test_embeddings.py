"""pool_llm.embeddings / POST /v1/embeddings: OpenRouter passthrough."""
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _load import load  # noqa: E402

pl = load("pool_llm")
KEY = "fixture-key-not-real-123"


def fake_post(status=200, payload=None, seen=None):
    def post(url, headers, data):
        if seen is not None:
            seen.update(url=url, headers=headers, body=json.loads(data))
        return status, json.dumps(payload if payload is not None else
                                  {"data": [{"embedding": [0.1, 0.2], "index": 0}], "model": "openai/x"}).encode()
    return post


def test_bare_model_prefixed_and_restored():
    seen = {}
    out = pl.embeddings({"model": "text-embedding-3-large", "input": ["a"]}, get_key=lambda: KEY,
                        post=fake_post(seen=seen))
    assert seen["url"] == "https://openrouter.ai/api/v1/embeddings"
    assert seen["body"]["model"] == "openai/text-embedding-3-large"
    assert seen["headers"]["authorization"] == f"Bearer {KEY}"
    assert out["model"] == "text-embedding-3-large"
    assert out["object"] == "list" and out["usage"] == {"prompt_tokens": 0, "total_tokens": 0}


def test_prefixed_model_kept_and_existing_fields_preserved():
    seen = {}
    payload = {"object": "list", "data": [{"embedding": [1], "index": 0}], "usage": {"prompt_tokens": 3, "total_tokens": 3}}
    out = pl.embeddings({"model": "openai/text-embedding-3-small", "input": "a"}, get_key=lambda: KEY,
                        post=fake_post(payload=payload, seen=seen))
    assert seen["body"]["model"] == "openai/text-embedding-3-small"
    assert out["model"] == "openai/text-embedding-3-small" and out["usage"]["prompt_tokens"] == 3


def test_error_status_passthrough_and_key_redacted():
    with pytest.raises(pl.UpstreamError) as e:
        pl.embeddings({"model": "m", "input": ["a"]}, get_key=lambda: KEY,
                      post=fake_post(402, {"error": {"message": f"bad key {KEY}"}}))
    assert e.value.status_code == 402 and KEY not in str(e.value)


def test_missing_key_and_bad_request():
    with pytest.raises(pl.UpstreamError) as e:
        pl.embeddings({"model": "m", "input": ["a"]}, get_key=lambda: "", post=fake_post())
    assert e.value.status_code == 401
    with pytest.raises(ValueError):
        pl.embeddings({"input": ["a"]}, get_key=lambda: KEY, post=fake_post())


def _serve(monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    threading.Thread(target=pl.serve, args=("127.0.0.1", port), daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return port


def _post(port, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/embeddings", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_route_ok_error_and_no_key_in_logs(monkeypatch, capsys):
    monkeypatch.setattr(pl, "_openrouter_key", lambda: KEY)
    mode = {"status": 200}
    monkeypatch.setattr(pl, "_http_post", lambda u, h, d: fake_post(
        mode["status"], None if mode["status"] == 200 else {"error": {"message": "no credits"}})(u, h, d))
    port = _serve(monkeypatch)
    st, out = _post(port, {"model": "text-embedding-3-large", "input": ["a"]})
    assert st == 200 and out["model"] == "text-embedding-3-large"
    mode["status"] = 429
    st, out = _post(port, {"model": "text-embedding-3-large", "input": ["a"]})
    assert st == 429 and "no credits" in out["error"]["message"]
    captured = capsys.readouterr()
    assert KEY not in captured.err + captured.out and KEY not in json.dumps(out)
