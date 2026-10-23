"""The same flows against the REAL hermes-hub client, when it is installed (skipped otherwise).

Proves the fake's schema/queue semantics match: a request queued offline lands in `sent` as the real
`send_queued` expects it, and the real `view_rows`/`where` agree with ours on the fixture db.
"""
import os
from pathlib import Path

import pytest

from conftest import NOW, PLUGIN, _load, build_db

CANDIDATES = [os.environ.get("HERMES_HUB_CLIENT_REAL"),
              str(Path.home() / ".local" / "share" / "hermes-mail" / "hermes_hub.py"),
              str(Path.home() / "code" / "hermes-hub" / "client" / "hermes_hub.py")]
REAL = next((Path(p) for p in CANDIDATES if p and Path(p).is_file()), None)
pytestmark = pytest.mark.skipif(REAL is None, reason="hermes-hub client not installed")


@pytest.fixture
def real(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("HERMES_HUB_STATE", str(state))
    monkeypatch.setenv("HERMES_HUB_CLIENT", str(REAL))
    # point the client's config at nothing reachable, so a send attempt is Offline (or SystemExit: no key)
    monkeypatch.setenv("HERMES_MAIL_CONFIG", str(tmp_path / "cfg" / "config.json"))
    monkeypatch.setenv("HERMES_HUB_URL", "http://127.0.0.1:9")
    client = _load(REAL, "real_hermes_hub")
    build_db(state / "hub.db", client.SCHEMA)
    hv = _load(PLUGIN / "hubview.py", "hv_real")
    monkeypatch.setattr(hv, "_now", lambda: NOW)
    return hv, client, state


def test_views_agree_with_the_real_client(real):
    hv, client, state = real
    assert hv.load_client() is not None and hv.status()["available"] is True
    db = client.open_db(state / "hub.db")
    try:
        theirs = {(r["_gw"], r["_key"]) for r in client.view_rows(db, "sessions")}
        assert [h["gateway"] for h in client.where(db, "s_mini")] == [h["gateway"] for h in hv.where("s_mini")["hits"]]
    finally:
        db.close()
    ours = {(r["gateway"], r["key"]) for r in hv.sessions(include_archived=True)["sessions"]}
    assert ours == theirs


def test_offline_request_is_queued_for_the_real_sync(real):
    hv, client, state = real
    res = hv.request({"gateway": "mini", "coll": "sessions", "key": "default/s_mini", "action": "unpin"}, wait_s=10)
    assert res["queued_locally"] is True
    db = client.open_db(state / "hub.db")
    try:
        row = db.execute("SELECT state FROM sent WHERE id = ?", (res["id"],)).fetchone()
        assert row["state"] == "local"  # the poller's next send_queued picks it up
    finally:
        db.close()
