"""The gate against the REAL hermes_state_outcome (not the fake), on a temp HERMES_HOME state.db.

Core checkout: HERMES_OUTCOME_CORE_DIR, HERMES_CORE_CHECKOUT, or ~/.hermes/hermes-agent.
A named core with missing core/dependencies fails rather than silently removing integration
coverage; with no core at all (public plugin-suites CI) these tests skip.
Run with a dependency-equipped interpreter via scripts/test-all -k session-outcome.
"""
import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("HERMES_OUTCOME_CORE_DIR") or os.environ.get("HERMES_CORE_CHECKOUT")
            or Path.home() / ".hermes" / "hermes-agent").expanduser()

SUMMARY = "Rebased the branch, fixed the two failing tests and pushed; CI is green on the new head."


@pytest.fixture
def real(tmp_path, monkeypatch):
    # No core at all (public plugin-suites CI: core is private) -> skip. A named core
    # (HERMES_OUTCOME_CORE_DIR, or HERMES_CORE_CHECKOUT which scripts/test-all sets whenever it found
    # one) that is missing or broken still fails below, never skips.
    named = os.environ.get("HERMES_OUTCOME_CORE_DIR") or os.environ.get("HERMES_CORE_CHECKOUT")
    if not named and not (CORE / "hermes_state_outcome.py").is_file():
        pytest.skip(f"needs Hermes core (none at {CORE}; HERMES_CORE_CHECKOUT unset)")
    assert (CORE / "hermes_state_outcome.py").is_file(), f"missing real core at {CORE}"
    # This is a library test, not a launch: never provision/re-exec for the disposable home.
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "desktop")
    for k in ("HERMES_KANBAN_TASK", "HERMES_SESSION_OUTCOME_OFF", "HERMES_PLATFORM", "HERMES_SESSION_PLATFORM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.syspath_prepend(str(CORE))
    # import core from the worktree, not whatever hermes_state an earlier import resolved
    for name in [m for m in sys.modules if m == "hermes_state_outcome" or m.startswith("hermes_state")
                 or m == "hermes_constants"]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    try:
        so = importlib.import_module("hermes_state_outcome")
        import hermes_state  # noqa: F401
    except ImportError as exc:
        pytest.fail(f"real-core dependencies missing in {sys.executable}: {exc}")
    assert Path(so.__file__).resolve().parent == CORE.resolve()
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", tmp_path / "state.db", raising=False)
    so._reset_hooks_for_tests()
    db = hermes_state.SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("chat", source="desktop")
        db.append_message("chat", role="user", content="fix the build")
    finally:
        db.close()
    spec = importlib.util.spec_from_file_location("session_outcome_plugin_real_core", ROOT / "__init__.py")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    plugin.register(type("Ctx", (), {"register_tool": lambda *a, **k: None,
                                     "register_hook": lambda *a, **k: None})())
    yield so, plugin
    so._reset_hooks_for_tests()


@pytest.mark.parametrize("source,allowed", [
    ("local", True), ("desktop", True), ("tui", True), ("cli", True), ("gui", True), ("acp", True),
    ("cron", False), ("kanban", False), ("subagent", False),
    ("oneshot", False), ("slack", False), ("unknown", False),
])
def test_bound_local_transport_catalog_and_dispatch(real, tmp_path, source, allowed):
    """Real session row + ContextVars + registry + writer; not a mocked source predicate."""
    import contextvars
    import json
    from hermes_state import SessionDB
    from gateway.session_context import set_session_vars
    from tools.registry import ToolRegistry

    so, plugin = real
    sid = "local-" + "b" * 64
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(sid, source=source)
        db.append_message(sid, role="user", content="Check the build")
    finally:
        db.close()
    registry = ToolRegistry()
    plugin.register(type("Ctx", (), {
        "register_tool": lambda self, **kw: registry.register(**kw),
        "register_hook": lambda *a: None,
    })())
    plugin._llm = lambda *a: "NO"

    def run():
        set_session_vars(platform="local", source="", session_id=sid)
        assert plugin._row_source(sid) == source
        definitions = registry.get_definitions({plugin.TOOL})
        assert [d["function"]["name"] for d in definitions] == ([plugin.TOOL] if allowed else [])
        held = plugin.pre_final_gate(session_id=sid, platform="local", final_response="Checking.")
        assert bool(held and held.get("hold")) is allowed
        raw = registry.dispatch(plugin.TOOL, {"kind": "done", "summary": SUMMARY}, session_id=sid)
        assert isinstance(raw, str)
        result = json.loads(raw)
        assert result["ok"] is allowed, result
        if allowed:
            assert result["declared"]
            assert so.get_outcome(sid)["summary"] == SUMMARY
        else:
            assert so.get_outcome(sid) is None
    contextvars.copy_context().run(run)


def test_native_local_exclusions(real, tmp_path, monkeypatch):
    import contextvars
    import json
    from hermes_state import SessionDB
    from gateway.session_context import set_session_vars

    so, plugin = real
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("native", source="local")
    finally:
        db.close()

    def run():
        set_session_vars(platform="local", session_id="native")
        for flag in ("HERMES_KANBAN_TASK", "HERMES_SESSION_OUTCOME_OFF"):
            with monkeypatch.context() as m:
                m.setenv(flag, "1")
                assert not plugin.check_fn()
                assert not json.loads(plugin.handle({"kind": "done", "summary": SUMMARY}, session_id="native"))["ok"]
                assert plugin.pre_final_gate(session_id="native", platform="local", final_response="Checking.") is None
        set_session_vars(platform="subagent", source="local", session_id="native")
        assert not plugin.check_fn()
        assert not json.loads(plugin.handle({"kind": "done", "summary": SUMMARY}, session_id="native"))["ok"]
        assert plugin.pre_final_gate(session_id="native", platform="subagent", final_response="Checking.") is None
        set_session_vars(platform="local", source="", session_id="missing")
        assert not plugin.check_fn()  # transport alone is not evidence of an interactive chat
        assert so.get_outcome("native") is None
    contextvars.copy_context().run(run)


def gate(plugin, reply, msgs=None, attempt=0):
    return plugin.pre_final_gate(session_id="chat", platform="desktop", final_response=reply,
                                 turn_messages=msgs or [], attempt=attempt, hold_budget=2)


@pytest.mark.parametrize("keep,corrupt", [(False, False), (True, False), (False, True)])
def test_done_cron_lifecycle_against_real_core(real, tmp_path, keep, corrupt):
    import json
    from cron import jobs

    so, plugin = real
    with jobs.use_cron_store(tmp_path):
        job = jobs.create_job(prompt="Check status", schedule="every 1h", owner_session_id="chat")
        if corrupt:
            (tmp_path / "cron" / "jobs.json").write_text("broken", encoding="utf-8")
        result = json.loads(plugin.handle({"kind": "done", "summary": "Installed.", "keep_crons": keep}, session_id="chat"))
        assert result["declared"]
        assert so.get_outcome("chat")["kind"] == "done"
        if corrupt:
            assert result["cron_release_error"]
            assert (tmp_path / "cron" / "jobs.json").read_text(encoding="utf-8") == "broken"
        elif keep:
            assert jobs.get_job(job["id"])["owner_session_id"] == "chat"
        else:
            assert result["released_crons"][0]["id"] == job["id"]
            assert not jobs.get_job(job["id"])["owner_session_id"]


def test_gate_against_real_core(real, monkeypatch):
    so, plugin = real
    # A live turn marker would make plain derive say "running"; the gate must look past it.
    monkeypatch.setattr(so, "_live_turn", lambda db, sid, *_: True)  # core added `concluding`
    assert so.derive_outcome("chat")["kind"] == "running"
    # it reads the temp state.db: the seeded user row is the tail when no reply is pending
    assert so.derive_outcome("chat", ignore_live_turn=True)["reason"] == "interrupted"

    plugin._llm = lambda s, u: "NO"
    held = gate(plugin, "Looking at it.")
    assert held and held["hold"] and plugin.TAG in held["nudge"]
    # second miss in the same turn passes
    assert gate(plugin, "Looking at it.", msgs=[{"role": "user", "content": held["nudge"]}], attempt=1) is None

    plugin._llm = lambda s, u: "YES"
    assert gate(plugin, SUMMARY) is None

    # Core may use its heuristic on judge outage, but the gate requires a declaration.
    def down(s, u):
        raise RuntimeError("pool down")
    plugin._llm = down
    plugin._JUDGED.clear()
    assert so.derive_outcome("chat", ignore_live_turn=True, pending_reply=SUMMARY)["kind"] == "done"
    assert gate(plugin, SUMMARY + " ")["hold"]
    assert gate(plugin, "Hmm?")["hold"]


def test_card_read_failure_does_not_mask_declaration_gate(real, monkeypatch):
    """Exercise core's actual early card-read-failed branch, then the recovered negative judge."""
    from hermes_state import SessionDB

    so, plugin = real
    monkeypatch.setattr(so, "_live_turn", lambda *a: False)
    reply = "Not fixed yet; another worker owns the repair."
    plugin._llm = lambda *a: "NO"

    def unavailable(*a):
        raise RuntimeError("card source unavailable")

    monkeypatch.setattr(so, "_card_items", unavailable)
    before = so.derive_outcome("chat", ignore_live_turn=True, pending_reply=reply)
    assert before["kind"] == "unresolved" and before["reason"] == "card_read_failed"
    held = gate(plugin, reply)
    assert held and held["hold"] and plugin.TAG in held["nudge"]
    assert so.get_outcome("chat") is None  # the gate never publishes an outcome

    monkeypatch.setattr(so, "_card_items", lambda *a: ([], []))
    db = SessionDB()
    try:
        db.append_message("chat", role="assistant", content=reply)
    finally:
        db.close()
    after = so.refresh_outcome("chat", trigger="manual")
    assert after["kind"] == "unresolved" and after["reason"] == "no_summary"
    assert gate(plugin, reply, msgs=[{"role": "user", "content": held["nudge"]}], attempt=1) is None


def test_negative_verdict_survives_informational_tail_and_judge_outage(real, monkeypatch):
    from hermes_state import SessionDB

    so, plugin = real
    plugin.PLUGINS = ROOT.parent
    monkeypatch.setattr(so, "_live_turn", lambda *a: False)
    monkeypatch.setattr(so, "_live_work", lambda *a: ([], [], False))
    plugin._llm = lambda *a: "NO"
    db = SessionDB()
    try:
        db.append_message("chat", role="assistant", content=SUMMARY)
        initial = so.refresh_outcome("chat", trigger="manual", db=db)
        assert initial["reason"] == "no_summary" and initial["judged"]
        db.append_message("chat", role="user", content="An unrelated completed card.",
                          display_kind="kanban_notice",
                          display_metadata={"outcome_informational": True, "stale": True})
        plugin._JUDGED.clear()
        def unavailable(*a):
            raise RuntimeError("subscription pool unavailable")
        plugin._llm = unavailable
        out = so.refresh_outcome("chat", trigger="manual", db=db)
        assert out["reason"] == "no_summary" and out["judged_msg"] == initial["judged_msg"]
    finally:
        db.close()


def test_open_step_makes_real_core_say_decision(real, monkeypatch):
    so, plugin = real
    plugin._llm = lambda s, u: "NO"
    us = plugin._user_steps()
    us.add_step("chat", "Ship it now?", ["Yes", "Later"])
    out = so.derive_outcome("chat", ignore_live_turn=True, pending_reply="Looking at it.")
    assert out["kind"] == "decision" and out["items"][0]["question"] == "Ship it now?"
    # The reply may resolve that step after the gate (user-steps' transform): the gate judges the reply itself.
    held = gate(plugin, "Looking at it.")
    assert held and held["hold"]
    plugin._llm = lambda s, u: "YES"
    plugin._JUDGED.clear()
    assert gate(plugin, SUMMARY) is None


def test_wait_on_a_card_links_it_to_the_waiting_chat(real, monkeypatch):
    """agent-884fdd96f8: a chat that declares it waits on a card must show that card in its task list. A declared
    wait only stored the outcome, so the chat said "the remaining work is ours" with nothing scheduled in view."""
    so, plugin = real
    monkeypatch.setenv("HERMES_KANBAN_HOME", os.environ["HERMES_HOME"])
    for k in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_WORKSPACES_ROOT", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.delenv(k, raising=False)
    for name in [m for m in sys.modules if m.startswith("hermes_cli.kanban")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="repair the forwarder", assignee="default")
        # A system card may already have its standing producer owner. The wait
        # must add this chat without displacing any existing subscription.
        before = kbn.list_notify_subs(conn, tid)
        assert all(s['chat_id'] != 'chat' for s in before)
    out = plugin.handle({"kind": "wait", "on": [{"kind": "card", "id": tid}, {"kind": "card", "id": "t_nowhere"}]},
                        session_id="chat")
    import json as _json
    res = _json.loads(out)
    assert res.get("declared") is True, res
    with kbc.connect_closing() as conn:
        subs = kbn.list_notify_subs(conn, tid)
    assert {(s['platform'], s['chat_id']) for s in subs} == {
        (s['platform'], s['chat_id']) for s in before} | {('tui', 'chat')}
    waiting = next(s for s in subs if s['chat_id'] == 'chat')
    assert waiting["delivery_metadata"]["reason"] == "shepherd"
    assert waiting["delivery_metadata"]["via"] == "outcome-wait"
    assert res["linked"] == [tid]
    assert "t_nowhere" in " ".join(res.get("not_linked", [])), res


def test_wait_from_a_headless_session_reports_not_linked(real, monkeypatch):
    """Core drops a tui sub for a headless ``local-<sha256>`` id (a one-shot/worker run); the plugin must say so
    instead of reporting the card linked (seen live on the hub: ``linked`` with no row written)."""
    so, plugin = real
    monkeypatch.setenv("HERMES_KANBAN_HOME", os.environ["HERMES_HOME"])
    for k in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_WORKSPACES_ROOT", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.delenv(k, raising=False)
    for name in [m for m in sys.modules if m.startswith("hermes_cli.kanban")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="repair", assignee="default")
        before = kbn.list_notify_subs(conn, tid)
    headless = "local-" + "a" * 64
    res = plugin._link_wait_cards(headless, [tid])
    with kbc.connect_closing() as conn:
        assert kbn.list_notify_subs(conn, tid) == before
    assert res[0] == [] and "not linked" in res[1][0], res


# kanban-310ed068f0: a chat whose stored kind was missing from the plugin's hand-written list got no judge verdict,
# so core's fallback turned this reply green (any 40+ char non-question passes).
INCIDENT_REPLY = ("No. The requested suite is not built yet. Its hub card is still queued and nobody has claimed it, "
                  "so nothing has run.")


@pytest.mark.parametrize("source", ["gui", "acp", "desktop", "local", "brand-new-chat-kind"])
def test_unlisted_chat_kind_is_judged_not_heuristic_green(real, tmp_path, source):
    from hermes_state import SessionDB

    so, plugin = real
    calls = []
    plugin._llm = lambda s, u: calls.append(u) or "NO"
    sid = f"chat-{source}"
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(sid, source=source)
        db.append_message(sid, role="user", content="Is the suite built?")
        db.append_message(sid, role="assistant", content=INCIDENT_REPLY)
    finally:
        db.close()
    out = so.derive_outcome(sid, ignore_live_turn=True)
    assert calls, f"{source}: no judge call; core fell back to its heuristic"
    assert out["kind"] == "unresolved", out


@pytest.mark.parametrize("source", ["kanban", "cron", "oneshot", "subagent", "tool"])
def test_background_kinds_are_not_judged(real, source):
    so, plugin = real
    calls = []
    plugin._llm = lambda s, u: calls.append(u) or "NO"
    assert plugin.judge("bg", INCIDENT_REPLY, source=source) is None
    assert calls == []


def test_every_core_session_kind_is_classified(real):
    """Guard: core's chat and machine kinds (kanban_session_provenance) must be judged / skipped by the plugin, so a
    kind core adds later can't silently fall back to the heuristic's green, and no kind sits in neither list."""
    import ast

    so, plugin = real
    path = CORE / "hermes_cli" / "kanban_session_provenance.py"
    lists = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("_INTERACTIVE_SOURCES", "MACHINE_SESSION_SOURCES"):
                lists[node.targets[0].id] = set(ast.literal_eval(node.value.args[0]))
    assert set(lists) == {"_INTERACTIVE_SOURCES", "MACHINE_SESSION_SOURCES"}, f"core lists moved: {lists}"
    chats, machines = lists["_INTERACTIVE_SOURCES"], lists["MACHINE_SESSION_SOURCES"]
    assert not chats - plugin.INTERACTIVE, f"core chat kinds without the end-of-turn check: {chats - plugin.INTERACTIVE}"
    assert not machines - plugin.BACKGROUND, f"core machine kinds the judge would call: {machines - plugin.BACKGROUND}"
    assert not plugin.INTERACTIVE & plugin.BACKGROUND
    plugin._llm = lambda s, u: "NO"
    for kind in sorted(chats):
        assert plugin.judge("guard", f"{INCIDENT_REPLY} ({kind})", source=kind) is False, kind


def test_person_wait_survives_real_core_store_and_blocks_done(real, tmp_path):
    """Real core keeps only kind/id/label/state: the person wait must come back from get_outcome and block done."""
    import json as _json

    from hermes_state import SessionDB

    so, plugin = real
    sid = "chat-person-wait"
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(sid, source="desktop")
        db.append_message(sid, role="user", content="Stage it once the example operator confirms.")
    finally:
        db.close()
    out = _json.loads(plugin.handle({"kind": "wait", "on": [{
        "kind": "person", "contact": "Slack @ExampleOperator", "channel": "slack", "thread": "https://example.com/C0EXAMPLE/1.1",
        "request_id": "key-request", "response": "confirm the key", "next_step": "run staging checks"}]}, session_id=sid))
    assert out["ok"] and out["declared"], out
    stored = so.get_outcome(sid)
    item = stored["on"][0]
    assert stored["kind"] == "waiting" and item["id"].startswith("person:")
    assert "confirm the key" in item["label"] and item["state"] == "waiting"
    refused = _json.loads(plugin.handle({"kind": "done", "summary": "parked a watch"}, session_id=sid))
    assert not refused["ok"] and "named person" in refused["error"]


@pytest.mark.parametrize('new_kind', ['user', 'kanban_notice'])
def test_durable_person_lifecycle_through_refresh_and_new_messages(real, tmp_path, monkeypatch, new_kind):
    import json
    from hermes_state import SessionDB
    so, plugin = real
    monkeypatch.setattr(so, '_live_turn', lambda *a: False)
    monkeypatch.setattr(plugin, '_open_linked_cards', lambda *a: [])
    plugin._llm = lambda *a: 'YES'
    person = {'kind': 'person', 'contact': 'Slack @ExampleOperator', 'channel': 'slack',
              'thread': 'https://example.com/thread/123', 'request_id': 'request-123',
              'response': 'rotate the example key', 'next_step': 'verify example staging'}
    def invoke(args):
        return json.loads(plugin.handle(args, session_id='chat'))
    sent = invoke({'kind': 'wait', 'on': [person]})
    assert sent['ok'], sent
    wid = sent['outcome']['on'][0]['id']
    db = SessionDB(db_path=tmp_path / 'state.db')
    try:
        db.append_message('chat', role='assistant', content=SUMMARY)
        assert so.refresh_outcome('chat', trigger='manual', db=db)['kind'] == 'waiting'
        db.append_message('chat', role='user', content='Check the example again',
                          **({'display_kind': new_kind} if new_kind != 'user' else {}))
        # Core may show running/interrupted while answering the new input, but cannot
        # permit done or discard the independent durable dependency.
        out = so.refresh_outcome('chat', trigger='manual', db=db)
        assert out['kind'] != 'done'
        assert not invoke({'kind': 'done', 'summary': 'watch parked'})['ok']
        assert plugin._open_person_waits('chat')[0]['id'] == wid
        db.append_message('chat', role='assistant', content=SUMMARY)
        assert so.refresh_outcome('chat', trigger='manual', db=db)['on'][0]['id'] == wid
        with monkeypatch.context() as live:
            live.setattr(so, '_live_turn', lambda *a: True)
            assert so.refresh_outcome('chat', trigger='manual', db=db)['kind'] == 'running'
            assert not invoke({'kind': 'done', 'summary': 'still waiting'})['ok']
        assert invoke({'kind': 'wait', 'on': [{'kind': 'time', 'id': 'later'}]})['ok']
        assert so.refresh_outcome('chat', trigger='manual', db=db)['on'][0]['id'] == wid
        assert invoke({'kind': 'wait', 'on': [{**person, 'state': 'verifying', 'evidence': 'https://example.com/reply/456'}]})['ok']
        checking = so.refresh_outcome('chat', trigger='manual', db=db)
        assert checking['kind'] == 'waiting' and checking['on'][0]['state'] == 'verifying'
        assert not invoke({'kind': 'done', 'summary': 'reply arrived'})['ok']
        assert invoke({'kind': 'done', 'summary': SUMMARY, 'closes_person_waits': [
            {'id': wid, 'verified': True, 'evidence': 'https://example.com/check/green'}]})['ok']
        assert so.refresh_outcome('chat', trigger='manual', db=db)['kind'] == 'done'
        assert plugin._people().read('chat')[0]['closure_receipt']['verified'] is True
    finally:
        db.close()


def test_real_core_passive_watch_is_green_without_person_dependency(real, monkeypatch):
    import json
    from hermes_state import SessionDB
    so, plugin = real
    monkeypatch.setattr(so, '_live_turn', lambda *a: False)
    monkeypatch.setattr(plugin, '_open_linked_cards', lambda *a: [])
    monkeypatch.setattr(so, '_watch_items', lambda *a: [{'kind': 'card', 'id': 'watch-example'}])
    assert json.loads(plugin.handle({'kind': 'done', 'summary': SUMMARY}, session_id='chat'))['ok']
    with SessionDB() as db:
        db.append_message('chat', role='assistant', content=SUMMARY)
    out = so.refresh_outcome('chat', trigger='manual')
    assert out['kind'] == 'done' and out['watches'][0]['id'] == 'watch-example'


def test_compression_and_mounted_person_api_keep_same_request(real, monkeypatch):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hermes_state import SessionDB
    so, plugin = real
    monkeypatch.setattr(plugin, '_open_linked_cards', lambda *a: [])
    person = {'kind': 'person', 'contact': 'Slack @ExampleOperator', 'channel': 'slack',
              'thread': 'https://example.com/thread/123', 'request_id': 'request-123',
              'response': 'deploy example', 'next_step': 'verify example'}
    assert json.loads(plugin.handle({'kind': 'wait', 'on': [person]}, session_id='chat'))['ok']
    wid = plugin._open_person_waits('chat')[0]['id']
    with SessionDB() as db:
        db.end_session('chat', end_reason='compressed')
        db.create_session('tip', source='desktop', parent_session_id='chat')
        db.append_message('tip', role='user', content='Check example')
        db.append_message('tip', role='assistant', content=SUMMARY)
    assert plugin.person_provider('tip')['waiting_on'][0]['id'] == wid
    assert not json.loads(plugin.handle({'kind': 'done', 'summary': SUMMARY}, session_id='tip'))['ok']
    from hermes_cli import web_server_dashboard as dashboard
    app = FastAPI()
    # Exercise core's real plugin import/router contract, not a hand-mounted factory.
    assert dashboard._mount_one_plugin_api(app, {
        'name': 'session-outcome', 'source': 'user',
        '_dir': str(ROOT / 'dashboard'), '_api_file': 'plugin_api.py',
    }, {'session-outcome'}, set())
    with TestClient(app) as client:
        response = client.get('/api/plugins/session-outcome/person-waits', params={'ids': 'chat,tip', 'profile': 'current'})
        assert response.status_code == 200
        assert len(response.json()['records']) == 1
        assert response.json()['records'][0]['thread'] == person['thread']
        assert client.get('/api/plugins/session-outcome/person-waits', params={'ids': ','.join(str(n) for n in range(33))}).status_code == 400
        update = {**person, 'state': 'verifying', 'evidence': 'https://example.com/reply'}
        assert json.loads(plugin.handle({'kind': 'wait', 'on': [update]}, session_id='tip'))['ok']
        assert json.loads(plugin.handle({'kind': 'done', 'summary': SUMMARY, 'closes_person_waits': [
            {'id': wid, 'verified': True, 'evidence': 'https://example.com/tests/green'}]}, session_id='tip'))['ok']
        assert client.get('/api/plugins/session-outcome/person-waits', params={'ids': 'chat,tip'}).json() == {'records': []}
        assert plugin._people().read('chat')[0]['state'] == 'closed'


def test_closing_a_person_wait_refreshes_the_stored_outcome_without_a_turn(real, tmp_path, monkeypatch):
    """The person ledger is `person_provider`'s source for the outcome's controls; core refreshes on no event
    for it. Closing the wait (saved after the declaration) must reach the stored outcome on its own."""
    import json
    from hermes_state import SessionDB
    so, plugin = real
    monkeypatch.setattr(so, '_live_turn', lambda *a: False)
    monkeypatch.setattr(plugin, '_open_linked_cards', lambda *a: [])
    plugin._llm = lambda *a: 'YES'
    person = {'kind': 'person', 'contact': 'Slack @ExampleOperator', 'channel': 'slack',
              'thread': 'https://example.com/thread/9', 'request_id': 'request-9',
              'response': 'confirm the window', 'next_step': 'schedule it'}
    sent = json.loads(plugin.handle({'kind': 'wait', 'on': [person]}, session_id='chat'))
    wid = sent['outcome']['on'][0]['id']
    assert wid in json.dumps(so.get_outcome('chat')['status'])
    calls = []
    quietly = so.refresh_outcomes_quietly
    monkeypatch.setattr(so, 'refresh_outcomes_quietly', lambda s, t, **k: calls.append((list(s), t)) or quietly(s, t, **k))
    seen = json.loads(plugin.handle({'kind': 'wait', 'on': [
        {**person, 'state': 'verifying', 'evidence': 'https://example.com/reply/9'}]}, session_id='chat'))
    assert seen['ok'], seen
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        db.append_message('chat', role='assistant', content=SUMMARY)
    closed = json.loads(plugin.handle({'kind': 'done', 'summary': SUMMARY, 'closes_person_waits': [
        {'id': wid, 'verified': True, 'evidence': 'https://example.com/check/9'}]}, session_id='chat'))
    assert closed['ok'], closed
    assert ('chat'.split(), 'notice') in calls
    assert wid not in json.dumps(so.get_outcome('chat')['status']), 'closed wait still in the stored controls'


def test_no_person_refresh_without_the_provider_in_this_process(real, monkeypatch):
    import json
    so, plugin = real
    so._providers.pop('person-dependencies')
    calls = []
    monkeypatch.setattr(so, 'refresh_outcomes_quietly', lambda *a, **k: calls.append(a))
    with plugin._people().edit('chat') as records:
        plugin._save_people('chat', records)
    assert calls == []
