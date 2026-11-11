"""Receipt tool admission: old loaded core must not silently discard evidence."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def plugin(monkeypatch):
    spec = importlib.util.spec_from_file_location("receipt_plugin", Path(__file__).parents[1] / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_interactive", lambda **kw: True)
    monkeypatch.setattr(mod, "_outcome_api", lambda: None)
    return mod


def receipt(disposition="delivered"):
    item = {"scope": "bounded assessment", "disposition": disposition,
            "evidence": [{"ref": "artifact:assessment", "observed_at": "2026-01-01T00:00:00Z",
                          "verification": "reported"}], "remaining": []}
    if disposition == "superseded":
        item["superseded_by"] = "decision:replacement"
    return {"version": 1, "items": [item]}


@pytest.mark.parametrize("api", [None, SimpleNamespace(), SimpleNamespace(validate_declaration=lambda value: {"kind": "done", "summary": value["summary"]})])
def test_unsupported_loaded_core_refuses_before_declaration(plugin, monkeypatch, api):
    calls = []
    if api is not None:
        api.declare_outcome = lambda *args: calls.append(args)
    monkeypatch.setattr(plugin, "_outcome_api", lambda: api)
    result = json.loads(plugin.handle({"kind": "done", "summary": "Assessed.", "completion_receipt": receipt()}, session_id="chat"))
    assert result["ok"] is False
    assert "completion_receipt" in result["error"]
    assert not calls


@pytest.mark.parametrize("kind", ["wait", "decide"])
def test_receipt_on_non_done_refused_before_side_effects(plugin, monkeypatch, kind):
    monkeypatch.setattr(plugin, "_user_steps", lambda: pytest.fail("must not create decisions"))
    args = {"kind": kind, "completion_receipt": receipt(), "on": [{"kind": "time", "id": "later"}],
            "items": [{"question": "Choose", "choices": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}]}]}
    result = json.loads(plugin.handle(args, session_id="chat"))
    assert not result["ok"]
    assert "completion_receipt" in result["error"]


def test_schema_exposes_bounded_receipt(plugin):
    schema = plugin.SCHEMA["parameters"]["properties"]["completion_receipt"]
    assert schema["properties"]["version"]["enum"] == [1]
    items = schema["properties"]["items"]
    assert items["maxItems"] == 32
    assert items["items"]["properties"]["disposition"]["enum"] == ["delivered", "superseded", "contained", "rejected"]
