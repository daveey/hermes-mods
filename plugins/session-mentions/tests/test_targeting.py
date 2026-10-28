"""Regression: `@session:default@this-device/<id>` sent from a shared gateway (nous-1) resolved on
whichever Desktop drained the outbox first. The MacBook and the mini both drain nous-1, so probes for
real MacBook sessions came back `404 Session not found` from the mini.

Fix: the tool stamps `route` (the gateway the target names); couriers drain with their device name,
aliases and reach list; a request naming a live courier's device goes to that courier only, and
`this-device` is honoured only by the gateway's own (home) courier.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("sm_relay_files_t", Path(__file__).resolve().parent.parent / "relay_files.py")
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)  # type: ignore[union-attr]

_pspec = importlib.util.spec_from_file_location("sm_plugin_t", Path(__file__).resolve().parent.parent / "__init__.py")
sm = importlib.util.module_from_spec(_pspec)
sys.modules["sm_plugin_t"] = sm
_pspec.loader.exec_module(sm)  # type: ignore[union-attr]


def _base(tmp_path: Path) -> Path:
    for sub in ("outbox", "claimed", "results"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _req(base: Path, rid: str, route: str = "") -> None:
    rf.atomic_write(base / "outbox" / f"{rid}.json", {"id": rid, "action": "send", "route": route, "created_at": time.time(), "ttl": 600})


# The two Desktops draining nous-1, as they name things.
MBP = dict(courier="macbook", aliases="macbook,david-mbp", reach="mac-mini,nous-1,skint-polycentrism")
MINI = dict(courier="mac-mini", aliases="mac-mini,daveey-mini", reach="macbook,nous-1,skint-polycentrism")


@pytest.mark.parametrize("target,route", [
    ("@session:default@macbook/20261019_132933_0b8012", "macbook"),
    ("`@session:alpha-follow@nous-1/20261019_210732_7010b2`", "nous-1"),
    ("@session:@session:default@mac-mini@mac-mini/x", "mac-mini"),
    ("@session:default/x", ""),
    ("nous-1:20261019_210732_7010b2", "nous-1"),
    ("@task:default@nous-1/t_e10c6f5f", "nous-1"),
    ("t_e10c6f5f", ""),
    ("@session:default@this-device/x", "this-device"),
])
def test_route_of_target(target, route):
    assert rf.route_of_target(target) == route


def test_request_for_a_mac_is_claimed_by_that_mac_even_when_another_desktop_reaches_it(tmp_path):
    base = _base(tmp_path)
    # Both couriers have drained recently (registered), then a request for the MacBook arrives.
    rf.claim_pending(base, home=False, **MBP)
    rf.claim_pending(base, home=False, **MINI)
    _req(base, "for-mbp", "macbook")
    _req(base, "for-mbp-host", "david-mbp")
    assert rf.claim_pending(base, home=False, **MINI) == []  # the mini reaches `macbook` but is not it
    assert sorted(r["id"] for r in rf.claim_pending(base, home=False, **MBP)) == ["for-mbp", "for-mbp-host"]


def test_this_device_is_only_honoured_by_the_home_courier(tmp_path):
    base = _base(tmp_path)
    _req(base, "amb", "this-device")
    assert rf.claim_pending(base, home=False, **MBP) == []
    assert rf.claim_pending(base, home=False, **MINI) == []
    assert rf.claim_pending(base, home=False) == []  # legacy drain too
    assert [r["id"] for r in rf.claim_pending(base, home=True, **MBP)] == ["amb"]


def test_unowned_route_goes_to_a_courier_that_reaches_it(tmp_path):
    base = _base(tmp_path)
    _req(base, "gw", "nous-1")
    _req(base, "nobody", "some-other-box")
    _req(base, "any", "")
    got = sorted(r["id"] for r in rf.claim_pending(base, home=False, **MINI))
    assert got == ["any", "gw"]
    assert (base / "outbox" / "nobody.json").exists()  # left for a Desktop that knows it


def test_dead_owner_lets_a_reaching_courier_serve(tmp_path):
    base = _base(tmp_path)
    rf.claim_pending(base, home=False, **MBP)
    stale = time.time() - rf.COURIER_FRESH_SECONDS - 5
    import os
    os.utime(base / "couriers" / "macbook.json", (stale, stale))
    _req(base, "for-mbp", "macbook")
    assert [r["id"] for r in rf.claim_pending(base, home=False, **MINI)] == ["for-mbp"]


def test_legacy_drain_does_not_steal_a_live_devices_request(tmp_path):
    base = _base(tmp_path)
    rf.claim_pending(base, home=False, **MBP)
    _req(base, "for-mbp", "macbook")
    _req(base, "plain", "")
    assert [r["id"] for r in rf.claim_pending(base, home=False)] == ["plain"]


def test_device_info_prefers_config_then_hostname(monkeypatch):
    import types
    monkeypatch.setitem(sys.modules, "hermes_cli.config", types.SimpleNamespace(load_config=lambda: {"session_mentions": {"device_name": "MacBook"}}))
    monkeypatch.setattr("socket.gethostname", lambda: "David-MBP.local")
    monkeypatch.setattr(rf, "gateway_name", lambda: "")
    assert rf.device_info() == {"device": "macbook", "aliases": ["david-mbp", "macbook"], "gateway": ""}
    monkeypatch.setitem(sys.modules, "hermes_cli.config", types.SimpleNamespace(load_config=lambda: {}))
    monkeypatch.delenv("HERMES_DEVICE_NAME", raising=False)
    assert rf.device_info() == {"device": "david-mbp", "aliases": ["david-mbp"], "gateway": ""}


@pytest.fixture
def tool(tmp_path, monkeypatch):
    base = _base(tmp_path)
    monkeypatch.setattr(sm.rf, "root", lambda: base)
    monkeypatch.setattr(sm, "_sender_info", lambda sid: {"session_id": sid or ""})
    monkeypatch.setattr(sm, "PICKUP_TIMEOUT", 0.6)
    return base


def test_tool_stamps_route_on_the_request(tool, monkeypatch):
    seen = []
    real = sm.rf.atomic_write

    def write(path, data):
        real(path, data)
        if Path(path).parent.name == "outbox":
            seen.append(data)
            real(tool / "results" / f"{data['id']}.json", {"ok": True})

    monkeypatch.setattr(sm.rf, "atomic_write", write)
    assert json.loads(sm.handle({"action": "read", "target": "@session:default@macbook/abc"}))["ok"] is True
    assert seen[-1]["route"] == "macbook"
    sm.handle({"action": "list", "gateway": "Mac Mini"})
    assert seen[-1]["route"] == "mac-mini"


def test_tool_rejects_this_device_from_a_gateway_without_its_own_desktop(tool):
    rf_mod = sm.rf
    rf_mod.claim_pending(tool, home=False, **MBP)  # MacBook drains nous-1
    out = json.loads(sm.handle({"action": "read", "target": "@session:default@this-device/20261019_132933_0b8012"}))
    assert out["ok"] is False
    assert "ambiguous" in out["error"] and "macbook" in out["error"]
    assert not list((tool / "outbox").glob("*.json"))


def test_unreachable_route_times_out_with_a_named_error(tool):
    out = json.loads(sm.handle({"action": "read", "target": "@session:default@nowhere/x"}))
    assert out["ok"] is False and "`nowhere`" in out["error"]


def test_dashboard_routes_pass_courier_and_reach(tmp_path, monkeypatch):
    """The routes are mounted once per backend; they forward every drain parameter and re-load
    relay_files.py when it changes, so a protocol fix needs no backend restart next time."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import types
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setitem(sys.modules, "hermes_constants", types.SimpleNamespace(get_hermes_home=lambda: tmp_path))
    spec = importlib.util.spec_from_file_location("sm_api_t", Path(__file__).resolve().parent.parent / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)  # type: ignore[union-attr]
    monkeypatch.delitem(sys.modules, "_session_mentions_relay_files", raising=False)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)

    ping = client.get("/ping").json()
    assert ping["targeting"] is True and ping["device"]
    base = tmp_path / "session_mentions"
    _req(base, "for-mbp", "macbook")
    _req(base, "plain", "")
    got = client.get("/outbox", params={"courier": "mac-mini", "aliases": "mac-mini", "reach": "macbook,nous-1"}).json()
    assert [r["id"] for r in got["requests"]] == ["for-mbp", "plain"]  # no live macbook courier yet
    client.get("/outbox", params={"courier": "macbook", "aliases": "macbook", "reach": "nous-1"})
    _req(base, "for-mbp-2", "macbook")
    assert client.get("/outbox", params={"courier": "mac-mini", "aliases": "mac-mini", "reach": "macbook"}).json()["requests"] == []
    assert client.get("/outbox").json()["requests"] == []  # legacy drain doesn't steal it either
    assert [r["id"] for r in client.get("/outbox", params={"courier": "macbook", "reach": ""}).json()["requests"]] == ["for-mbp-2"]


# Links Hermes writes for cards forwarded across gateways name the target by its hermes-hub
# registry name (`@task:default@hub/t_..`), which can differ from the Desktop connection label
# (`hub-ec2`). Each gateway reports that name on /ping; couriers add it to their reach.

def _api(tmp_path, monkeypatch, gateway):
    import types
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setitem(sys.modules, "hermes_constants", types.SimpleNamespace(get_hermes_home=lambda: tmp_path))
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_capability_registry",
                        types.SimpleNamespace(self_gateway=lambda: gateway))
    spec = importlib.util.spec_from_file_location("sm_api_gw", Path(__file__).resolve().parent.parent / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)  # type: ignore[union-attr]
    monkeypatch.delitem(sys.modules, "_session_mentions_relay_files", raising=False)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_ping_reports_the_gateways_registry_name(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    assert _api(tmp_path, monkeypatch, "Hub").get("/ping").json()["gateway"] == "hub"
    # No registry name (hub client not installed): the field is empty, nothing breaks.
    assert _api(tmp_path / "x", monkeypatch, None).get("/ping").json()["gateway"] == ""


def test_gateway_name_survives_a_broken_registry(monkeypatch):
    import types
    def boom():
        raise RuntimeError("no hub db")
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_capability_registry", types.SimpleNamespace(self_gateway=boom))
    assert rf.gateway_name() == ""


def test_request_routed_to_a_registry_name_is_claimed_by_the_courier_reaching_that_gateway(tmp_path, monkeypatch):
    """`cross_task read @task:default@hub/t_..` on the MacBook: the MacBook Desktop (home) reaches the
    hub connection labelled `hub-ec2`, whose /ping reports `hub`, so its reach carries both names.
    The mini's Desktop also drains the MacBook gateway but is not connected to the hub."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    client = _api(tmp_path, monkeypatch, "macbook")
    base = tmp_path / "session_mentions"
    mbp = {"home": "1", "courier": "macbook", "aliases": "macbook,david-mbp", "reach": "hub,hub-ec2,mac-mini"}
    mini = {"courier": "mac-mini", "aliases": "mac-mini", "reach": "macbook,nous-1"}
    for q in (mbp, mini):
        client.get("/outbox", params=q)  # both couriers registered
    _req(base, "via-registry", rf.route_of_target("@task:default@hub/t_01a182b1a147aa45"))
    _req(base, "via-label", rf.route_of_target("@task:default@hub-ec2/t_01a182b1a147aa45"))
    _req(base, "for-mbp", rf.route_of_target("@session:default@macbook/20261028_072000_abcdef"))
    assert client.get("/outbox", params=mini).json()["requests"] == []
    got = [r["id"] for r in client.get("/outbox", params=mbp).json()["requests"]]
    assert sorted(got) == ["for-mbp", "via-label", "via-registry"]
