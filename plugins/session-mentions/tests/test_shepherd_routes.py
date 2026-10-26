"""Route contract tests; all dispatches are captured, never sent."""
import ast
import hashlib
import json
import os
from pathlib import Path

import pytest
from test_cross_task import sm, _replica, relay


@pytest.mark.parametrize("rows,board,expected", [
    ([("hub", "work/t_abc")], "work", ("hub", "default/work")),
    ([("hub", "owner/work/t_abc")], "work", ("hub", "owner/work")),
    ([("hub", "owner/other/t_abc"), ("hub", "owner/work/t_abc")], "work", ("hub", "owner/work")),
    ([("hub", "owner/other/t_abc")], "work", None),
    ([("hub", "owner/work/t_abc"), ("hub", "second/work/t_abc")], "work", None),
    ([("hub", "owner/work/t_abc"), ("other", "owner/work/t_abc")], None, None),
    ([("hub", "work/t_abc"), ("hub", "default/work/t_abc")], "work", ("hub", "default/work")),
    ([("hub", "owner/work/t_abc"), ("macbook", "owner/work/t_abc")], None, None),
    ([("hub", "owner/../t_abc")], None, None),
    ([("hub", "a/b/c/t_abc")], None, None),
])
def test_route_selection(tmp_path, rows, board, expected):
    assert sm._shepherd.locate("t_abc", board, _replica(tmp_path, rows=rows)) == expected


@pytest.mark.parametrize("action", ["read", "comment"])
@pytest.mark.parametrize("route,owner", [("work/t_abc", "default"), ("owner/work/t_abc", "owner")])
def test_entrypoints_preserve_owner_and_receiver_contract(tmp_path, monkeypatch, relay, action, route, owner):
    db = _replica(tmp_path, rows=[("hub", route)])
    spawned = []
    real = sm._shepherd.maybe_shepherd
    monkeypatch.setattr(sm, "_sender_info", lambda sid: {"session_id": sid, "profile": "sender"})
    monkeypatch.setattr(sm._shepherd, "maybe_shepherd", lambda t, s, p, d: real(t, s, p, d, db=db, cli="hub", spawn=spawned.append))
    args = {"action": action, "target": "@task:work@hub/t_abc", "message": "hello"}
    sm.handle_task(args, session_id="session")
    sm.handle_task(args, session_id="session")
    assert len(spawned) == 1
    argv = spawned[0]
    assert argv[4] == f"{owner}/work/t_abc"
    assert "profile=sender" in argv
    assert decode_contract(argv[4]) == (owner, "work", "t_abc")
    with pytest.raises(ValueError):
        decode_contract("work/t_abc")


def decode_contract(key):
    # Execute the actual installed receiver's pure validator when available. CI
    # without the optional client uses its explicit, isolated three-part contract.
    path = Path(os.environ.get("HERMES_HUB_CLIENT", str(Path.home() / ".local/share/hermes-mail/hermes_hub.py")))
    if path.is_file():
        tree = ast.parse(path.read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "decode_task_key"]
        assert len(nodes) == 1
        scope = {"_safe_name": lambda p: bool(sm._shepherd._SAFE.fullmatch(str(p)))}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), scope)
        return scope["decode_task_key"](key)
    parts = key.split("/")
    if len(parts) != 3 or not all(sm._shepherd._SAFE.fullmatch(p) and p not in (".", "..") for p in parts):
        raise ValueError("invalid task route")
    return tuple(parts)


def test_rejected_legacy_id_is_not_reused_and_aliases_dedupe(tmp_path):
    db = _replica(tmp_path, rows=[("hub", "work/t_abc"), ("hub", "default/work/t_abc")])
    old_key = "session|hub/work/t_abc"
    old_id = "shep-" + hashlib.sha256(old_key.encode()).hexdigest()[:32]
    (tmp_path / sm._shepherd.LEDGER_NAME).write_text(json.dumps({old_key: 1}))
    spawned = []
    call = lambda: sm._shepherd.maybe_shepherd("@task:work@hub/t_abc", "session", "sender", tmp_path, db=db, cli="hub", spawn=spawned.append)
    rid = call()
    canonical = "session|hub/default/work/t_abc"
    assert rid == "shep-" + hashlib.sha256(canonical.encode()).hexdigest()[:32]
    assert rid != old_id
    assert call() is None
    assert len(spawned) == 1
    assert canonical in json.loads((tmp_path / sm._shepherd.LEDGER_NAME).read_text())
