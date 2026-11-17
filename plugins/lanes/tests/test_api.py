"""plugin_api: answers without lanes support, the read cache, validated writes (FastAPI only)."""
import contextlib
import importlib.util
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
API = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"


@pytest.fixture
def api():
    spec = importlib.util.spec_from_file_location("lanes_api_under_test", API)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Core:
    """Stand-in for core's kanban_db_connect / kanban_lanes / kanban_lanes_cli."""

    def __init__(self):
        self.snapshots, self.sets, self.admits = 0, [], []
        self.configured = True

    @contextlib.contextmanager
    def connect_closing(self):
        yield object()

    def snapshot(self, _conn, *, hours):
        self.snapshots += 1
        if not self.configured:
            return {"configured": False}
        return {"configured": True, "window_hours": hours, "lanes": {"product": {"share": 35}}}

    def set_lane(self, lane, **changes):
        if lane != "product":
            raise ValueError(f"unknown lane: {lane}")
        self.sets.append((lane, changes))

    def held(self, _conn, lane):
        return [{"id": "t_1", "title": "infra", "tenant": lane or "infra", "created_at": 5}]

    def admit(self, _conn, card, *, actor, reason):
        self.admits.append((card, actor, reason))
        return card == "t_1"


@pytest.fixture
def core(api, monkeypatch):
    fake = Core()
    monkeypatch.setattr(api, "core", lambda: (fake, fake, fake))
    return fake


def test_core_without_lanes_says_so(api, monkeypatch):
    monkeypatch.setattr(api, "core", lambda: None)
    assert api.read(6.0) == {"configured": False, "reason": "this Hermes core has no lanes support yet"}
    with pytest.raises(fastapi.HTTPException) as err:
        api.change("product", {"share": 1})
    assert err.value.status_code == 409


def test_unconfigured_lanes_carry_a_reason(api, core):
    core.configured = False
    out = api.read(6.0)
    assert out["configured"] is False and "kanban.admission.lanes" in out["reason"]


def test_reads_are_cached_per_window_and_a_write_drops_the_cache(api, core):
    api.read(6.0, now=100)
    api.read(6.0, now=105)
    assert core.snapshots == 1
    api.read(24.0, now=105)
    api.read(6.0, now=100 + api.CACHE_S + 1)
    assert core.snapshots == 3
    api.change("product", {"share": 50, "paused": True})
    assert core.sets == [("product", {"share": 50, "admit_per_day": None, "paused": True})]
    assert core.snapshots == 4  # the write returned a fresh read


@pytest.mark.parametrize("body", [{"weight": 3}, {"paused": "yes"}])
def test_bad_write_bodies_are_refused(api, core, body):
    with pytest.raises(ValueError):
        api.change("product", body)
    assert not core.sets


def test_unknown_lane_is_refused(api, core):
    with pytest.raises(ValueError):
        api.change("nope", {"share": 1})


def test_held_and_admit(api, core):
    assert api.held("infra")["cards"] == [{"id": "t_1", "title": "infra", "lane": "infra", "created_at": 5}]
    assert api.admit("t_1", "needed") == {"admitted": True}
    assert api.admit("t_2", "needed") == {"admitted": False}
    assert core.admits[0] == ("t_1", "user (lanes panel)", "needed")
