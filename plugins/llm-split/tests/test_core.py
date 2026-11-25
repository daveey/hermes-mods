"""llm-split core: quota reading, weight maths, in-place config writes, modes, the auto tick."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("llm_split_core_under_test", ROOT / "core.py")
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)

NOW = 1_800_000_000.0

CONFIG = """# top comment
model:
  default: class:standard
plugins:
  enabled:
    - account-usage   # keep me
model_routing:
  enabled: true
  policy_version: 1
  routes:
    deep:
      - provider: anthropic
        model: claude-opus-5-5
        billing: subscription
        weight: 1
        eligible: true
        funding_verified: true
        capabilities: {tools: true, context_tokens: 200000}
      - provider: openai-codex
        model: gpt-6-astra
        billing: subscription
        weight: 1   # trailing
        eligible: true
        funding_verified: true
        capabilities: {tools: true, context_tokens: 200000}
      - provider: xai-oauth
        model: grok-4.7
        billing: subscription
        weight: 1
        eligible: true
        funding_verified: true
        capabilities: {tools: true, context_tokens: 200000}
    fast:
    - provider: anthropic
      model: claude-haiku-5-5
      billing: subscription
      weight: 1
      eligible: true
      funding_verified: true
      capabilities: {tools: true, context_tokens: 200000}
    - provider: openai-codex
      model: gpt-6-luna
      billing: subscription
      weight: 1
      eligible: true
      funding_verified: true
      capabilities: {tools: true, context_tokens: 200000}
"""


def _history(root, rows):
    """account-usage's own history file: one weekly reading per (provider, account)."""
    p = root / "state" / "account-usage-history.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps({"ts": t, "p": prov, "a": a, "u": u, "r": None}) + "\n" for t, prov, a, u in rows))


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(CONFIG)
    prof = tmp_path / "profiles" / "coach"
    prof.mkdir(parents=True)
    (prof / "config.yaml").write_text(CONFIG)
    (tmp_path / "profiles" / "plain").mkdir()
    (tmp_path / "profiles" / "plain" / "config.yaml").write_text("model: {}\n")
    au = Path(__file__).resolve().parents[2] / "account-usage"
    monkeypatch.setattr(core, "_forecast_module", lambda r: _load_forecast(au))
    # The live fixture headroom: Claude 122, GPT 805, Grok 6 points.
    rows = [(NOW - 60, "anthropic", f"c{i}", u) for i, u in enumerate([14, 64] + [100] * 8)]
    rows += [(NOW - 60, "openai-codex", f"g{i}", u) for i, u in enumerate([0, 0, 0, 1, 3, 7, 26, 28, 61, 69, 100, 100])]
    rows += [(NOW - 60, "xai-oauth", "x0", 94)]
    _history(tmp_path, rows)
    return tmp_path


def _load_forecast(base):
    name = "_llm_split_test_forecast"
    if name not in sys.modules:
        s = importlib.util.spec_from_file_location(name, base / "forecast.py")
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        sys.modules[name] = m
    return sys.modules[name]


def weights_in(path):
    import yaml
    routes = yaml.safe_load(path.read_text())["model_routing"]["routes"]
    return {lvl: {r["provider"]: r["weight"] for r in rs} for lvl, rs in routes.items()}


def test_quota_is_headroom_share(root):
    q = core.read_quota(root, NOW)
    assert q["source"] == "account-usage"
    assert round(q["share"]["anthropic"]) == 13 and round(q["share"]["openai-codex"]) == 86
    assert round(q["share"]["xai-oauth"], 1) == 0.6
    assert q["accounts"]["anthropic"] == {"n": 10, "out": 8, "headroom": 122.0}
    assert q["accounts"]["openai-codex"]["out"] == 2


def test_newest_reading_per_account_wins(root):
    _history(root, [(NOW - 600, "anthropic", "c0", 0), (NOW - 60, "anthropic", "c0", 100),
                    (NOW - 60, "openai-codex", "g0", 50)])
    q = core.read_quota(root, NOW)
    assert q["share"] == {"openai-codex": 100.0, "anthropic": 0.0}
    assert q["dry"] == ["anthropic"]


def test_pacer_fallback_when_no_history(root):
    (root / "state" / "account-usage-history.jsonl").unlink()
    (root / "llm_pacing.json").write_text(json.dumps({"v2": {"computed_at": NOW - 30, "pools": {
        "anthropic": {"ratio": 0.25}, "openai-codex": {"ratio": 0.75}, "xai-oauth": {"ratio": 0.5, "at_reserve": True}}}}))
    q = core.read_quota(root, NOW)
    assert q["source"] == "pacer" and q["share"] == {"anthropic": 25.0, "openai-codex": 75.0, "xai-oauth": 0.0}
    assert core.read_quota(root, NOW + core.PACING_STALE_S + 60)["source"] is None


def test_weights_for_floors_and_bounds():
    w = core.weights_for({"anthropic": 0, "openai-codex": 99.7, "xai-oauth": 0.3}, ["anthropic", "openai-codex", "xai-oauth"])
    assert w == {"anthropic": 1, "openai-codex": 98, "xai-oauth": 1}
    assert core.weights_for({"anthropic": 50, "openai-codex": 50}, ["anthropic", "openai-codex"]) == {"anthropic": 1, "openai-codex": 1}
    w = core.weights_for({"anthropic": 30, "openai-codex": 69, "xai-oauth": 1}, ["anthropic", "openai-codex", "xai-oauth"])
    assert w == {"anthropic": 30, "openai-codex": 69, "xai-oauth": 1}
    assert all(1 <= v <= 100 for v in w.values()) and sum(w.values()) <= 100


def test_manual_drag_writes_every_class_and_home_only_weight_tokens(root):
    before = (root / "config.yaml").read_text()
    snap = core.apply(root, "manual", {"anthropic": 30, "openai-codex": 69, "xai-oauth": 1}, NOW)
    for home in (root, root / "profiles" / "coach"):
        w = weights_in(home / "config.yaml")
        assert w["deep"] == {"anthropic": 30, "openai-codex": 69, "xai-oauth": 1}
        assert w["fast"] == {"anthropic": 30, "openai-codex": 69}
    after = (root / "config.yaml").read_text()
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(before.splitlines()) == len(after.splitlines())
    assert changed and all("weight:" in a and "weight:" in b for a, b in changed)
    assert "weight: 69   # trailing" in after and "# keep me" in after
    assert (root / "profiles" / "plain" / "config.yaml").read_text() == "model: {}\n"
    assert snap["mode"] == "manual" and round(snap["traffic"]["share"]["anthropic"]) == 30
    assert sorted(Path(p).name for p in snap["written"]) == sorted([root.name, "coach"])
    assert (root / "config.yaml.pre-llm-split-20270115").read_text() == before


def test_manual_share_normalised_and_validated(root):
    core.apply(root, "manual", {"anthropic": 1, "openai-codex": 3, "xai-oauth": 0}, NOW)
    assert weights_in(root / "config.yaml")["deep"] == {"anthropic": 25, "openai-codex": 74, "xai-oauth": 1}
    for bad in ({"anthropic": -1, "openai-codex": 1}, {"anthropic": 0, "openai-codex": 0},
                {"bogus": 1, "anthropic": 1}, {"anthropic": True}, None):
        with pytest.raises(ValueError):
            core.apply(root, "manual", bad, NOW)
    with pytest.raises(ValueError):
        core.apply(root, "sideways", None, NOW)


def test_auto_follows_quota_and_tick(root):
    snap = core.apply(root, "auto", now=NOW)
    assert snap["mode"] == "auto"
    assert snap["traffic"]["weights"] == {"anthropic": 13, "openai-codex": 86, "xai-oauth": 1}
    assert core.tick(root, NOW) is None  # nothing moved
    # Claude runs dry: tick re-applies, Claude keeps the floor of 1.
    _history(root, [(NOW, "anthropic", "c0", 100), (NOW, "openai-codex", "g0", 20), (NOW, "xai-oauth", "x0", 100)])
    done = core.tick(root, NOW + 10)
    assert done["traffic"]["weights"] == {"anthropic": 1, "openai-codex": 1, "xai-oauth": 1} or \
        done["traffic"]["weights"]["openai-codex"] > 90
    assert done["traffic"]["weights"]["anthropic"] == 1


def test_tick_ignores_manual(root):
    core.apply(root, "manual", {"anthropic": 50, "openai-codex": 50, "xai-oauth": 0}, NOW)
    assert core.tick(root, NOW) is None


def test_auto_without_quota_refuses(root):
    (root / "state" / "account-usage-history.jsonl").unlink()
    with pytest.raises(ValueError, match="no quota"):
        core.apply(root, "auto", now=NOW)


def test_real_router_allocation_follows_weights(root):
    """200 allocations through core's own weighted round-robin land 60 on Claude under 30/70."""
    hermes = Path.home() / ".hermes" / "hermes-agent"
    if not (hermes / "agent" / "model_routing_state.py").exists():
        pytest.skip("no Hermes core checkout")
    sys.path.insert(0, str(hermes))
    try:
        from agent.model_routing_state import RoutingState
    finally:
        sys.path.remove(str(hermes))
    core.apply(root, "manual", {"anthropic": 30, "openai-codex": 70, "xai-oauth": 0}, NOW)
    import yaml
    routes = yaml.safe_load((root / "config.yaml").read_text())["model_routing"]["routes"]
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE counters (pool TEXT PRIMARY KEY, position INTEGER)")
    picks = [RoutingState._choose(db, "deep", routes["deep"], 1)["provider"] for _ in range(200)]
    assert abs(picks.count("anthropic") - 60) <= 1  # deep: 30/69/1 (Grok keeps the floor of 1)
    # fast has no Grok route: 30/69, so Claude gets 30/99 of it, within one round-robin cycle.
    picks = [RoutingState._choose(db, "fast", routes["fast"], 1)["provider"] for _ in range(990)]
    assert picks.count("anthropic") == 300


def test_eligible_process():
    assert core.eligible_process(["hermes", "gateway", "run"], {})
    assert not core.eligible_process(["hermes", "chat"], {})
    assert not core.eligible_process(["hermes", "gateway"], {"HERMES_KANBAN_TASK": "t_1"})
