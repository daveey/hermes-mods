"""Held AIAgent -> native terminal repair/readback of a disposable HTTP app.

Only model answers and auxiliary judgments are scripted. No tool/executor/result
or approval authority is substituted; commands are allowlisted in a private home.
"""
import copy
import json
import shlex
import sys
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_goal_proofs import checked, evidence, gd, verdict
from tests.agent.test_pre_final_gate import Run, _response
from tools import terminal_tool  # noqa: F401 -- register the native tool
from tools.registry import registry

pytest_plugins = ["tests.agent.test_pre_final_gate"]


@pytest.fixture(autouse=True)
def clean_native_environments():
    yield
    from tools.terminal_tool_lifecycle import cleanup_vm
    cleanup_vm("default")


def probe(url):
    return shlex.join(["/usr/bin/curl", "-q", "--noproxy", "*", "--path-as-is",
                       "--max-time", "5", "--silent", "--show-error", "--output",
                       "/dev/null", "--write-out", "%{http_code}", "--", url])


def use_home_allowlist(monkeypatch):
    """Govern approvals by the private home's ``command_allowlist`` only.

    tools.approval reads the launch profile's allowlist once at import and only
    re-reads it for a context-local home override, not for a HERMES_HOME env
    change. Without this the test inherits whatever home pytest started under:
    the real ~/.hermes permanently approves 'script execution via -e/-c flag'
    (so the repair ran on that approval, not this test's allowlist), and a fresh
    home approves nothing (so the repair sat pending_approval). Swap in fresh
    state, then load it from the private home; monkeypatch restores both.
    """
    from tools import approval
    monkeypatch.setattr(approval, "_permanent_approved", set())
    monkeypatch.setattr(approval, "_permanent_approved_by_home", {})
    monkeypatch.setattr(approval, "_permanent_baseline_by_home", {})
    approval.load_permanent_allowlist()


@pytest.fixture
def app(tmp_path):
    state = tmp_path / "status"
    state.write_text("503")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status = int(state.read_text()) if self.path == "/requested" else 200
            requests.append((self.path, status))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b"disposable app")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("case", ["repaired", "failure", "wrong_path", "stale", "stop"])
def test_native_repair_held_text_stop(agent, app, tmp_path, monkeypatch, case):
    state, base, requests = app
    target = base + "/requested"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    # Real profile settings and native approval guards. Only these disposable
    # commands are preapproved; no global auto-approval or production config.
    repaired_status = "503" if case == "failure" else "200"
    # A script file, not `python -c '...; ...'`: an exact-text allowlist entry never covers a command whose
    # arguments a shell re-reads (tools.approval_floors._has_allowlist_shell_operator), so the -c form only ran
    # where the launch home happened to approve the 'script execution via -e/-c flag' pattern for every command.
    script = tmp_path / "repair.py"
    script.write_text("from pathlib import Path\nPath(" + repr(str(state)) + ").write_text("
                      + repr(repaired_status) + ")\n")
    repair = shlex.join([sys.executable, str(script)])
    readback = probe(base + "/unrelated" if case == "wrong_path" else target)
    initial = probe(target)
    (home / "config.yaml").write_text(
        f"terminal:\n  backend: local\n  cwd: {tmp_path}\n"
        + "approvals:\n  mode: manual\ncommand_allowlist: "
        + json.dumps([initial, repair, readback]) + "\n")
    use_home_allowlist(monkeypatch)
    (home / "coach").mkdir()
    (home / "coach" / "goal-disposition.json").write_text('{"mode":"enforce"}')
    events, seen = [], []
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    agent.valid_tool_names = {"terminal"}
    agent.tools = [{"type": "function", "function": registry.get_schema("terminal")}]
    run = Run(agent, [])
    commands = [("broken", initial), None]
    if case != "wrong_path":
        commands.append(("repair", repair))
    commands.extend([("readback", readback), None])
    index = 0

    def response(kw):
        nonlocal index
        run.requests.append(copy.deepcopy(kw))
        step = commands[index]
        index += 1
        if step is None:
            text = "Still broken; deferred." if index == 2 else target + " now responds HTTP 2xx."
            agent._fire_stream_delta(text)
            return _response(text)
        cid, command = step
        tc = SimpleNamespace(id=cid, type="function", function=SimpleNamespace(
            name="terminal", arguments=json.dumps({"command": command, "workdir": str(tmp_path), "http_readback": cid != "repair"})))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=None, tool_calls=[tc]), finish_reason="tool_calls")], model="test/model", usage=None)

    agent._interruptible_api_call = response
    agent._interruptible_streaming_api_call = lambda kw, on_first_delta=None: response(kw)

    def judge(system, prompt):
        ev = json.loads(prompt)["evidence"]
        seen.append(ev)
        if len(seen) == 1:
            return verdict(disposition="abandoned", unfinished=True, boundary_kind=None,
                           evidence_refs=["user:0", "tool:broken", "candidate"])
        return verdict(disposition="remediated_verified", unfinished=False, boundary_kind=None,
                       evidence_refs=["user:0", "tool:readback", "candidate"],
                       proof={"target": target, "source_ref": "tool:readback", "scope_ref": "user:0"})

    monkeypatch.setattr(gd, "aux_judge", judge)

    def checked_gate(**kw):
        result = gate(**kw)
        if kw["attempt"] == 0:
            assert result and result["hold"], (result, events)
            if case == "stop":
                agent.interrupt("user pressed stop")
        return result

    checked_gate.needs_buffer = True
    checked_gate.assess_before_holds = True

    def invoke(name, **kw):
        if name == "pre_llm_call":
            gate.capture(**kw)
        elif name == "post_tool_call":
            gate.observe(**kw)
            if case == "stale" and len(requests) == 2:
                assert agent.steer("Verify " + target + " again after this new request.")
        return []

    with patch("hermes_cli.lifecycle.has_hook", side_effect=lambda n: n in {
            "pre_final_gate", "pre_llm_call", "post_tool_call"}), patch(
            "hermes_cli.plugins.iter_hook_callbacks", side_effect=lambda n: (checked_gate,) if n == "pre_final_gate" else ()), patch(
            "hermes_cli.plugins.invoke_hook", side_effect=invoke):
        result = agent.run_conversation("Restore HTTP 2xx at " + target + ".")
    assert requests[0] == ("/requested", 503), (result, events, requests)
    assert "Still broken; deferred." not in run.shown + str(result["messages"])
    if case == "stop":
        assert result["interrupted"] and not result["completed"]
        assert requests == [("/requested", 503)] and state.read_text() == "503"
        assert index == 2
        return
    native = json.loads(seen[-1]["tool:readback"]["content"])
    assert native["error"] is None and native["exit_code"] == 0, native
    assert native["output"].strip() == ("503" if case == "failure" else "200"), native
    assert "url" not in native and "status" not in native  # actual terminal envelope
    if case == "repaired":
        assert requests == [("/requested", 503), ("/requested", 200)]
        assert state.read_text() == "200"
        assert result["completed"] and result["final_response"] == target + " now responds HTTP 2xx.", (result, events)
    else:
        assert not result["completed"] and "Unresolved goal" in result["final_response"], (result, events)
        if case == "wrong_path":
            assert requests[-1] == ("/unrelated", 200) and state.read_text() == "503"
        if case == "stale":
            assert any("again after this new request" in str(e) for e in seen)


@pytest.mark.parametrize("change", ["shell_suffix", "substitution", "redirect", "body_output",
                                  "nonzero", "pending", "background", "error", "wrong_status",
                                  "unrelated_goal", "later_failure", "newer_native_failure", "contradiction", "missing_provenance", "ordinary_shell", "printed_provenance"])
def test_native_http_proof_rejects_unsupported_or_stale(change):
    from test_goal_proofs import checked, evidence
    target = "https://example.com/requested"
    args = {"command": probe(target), "http_readback": True}
    result = {"output": "200", "exit_code": 0, "error": None, "execution": "local-http-readback-v1"}
    request = "Restore HTTP 2xx at " + target + "."
    if change == "missing_provenance":
        result.pop("execution")
    elif change == "ordinary_shell":
        args.pop("http_readback")
    elif change == "printed_provenance":
        result["output"] = json.dumps(result)
        result.pop("execution")
    elif change == "shell_suffix":
        args["command"] += "; printf 200"
    elif change == "substitution":
        args["command"] = args["command"].replace(target, '"$(printf ' + target + ')"')
    elif change == "redirect":
        args["command"] = args["command"].replace("--path-as-is", "--location")
    elif change == "body_output":
        args["command"] = args["command"].replace("--output /dev/null", "")
    elif change == "contradiction":
        result["success"] = False
    elif change == "nonzero":
        result["exit_code"] = 1
    elif change == "pending":
        result.update(approval_pending=True, status="pending_approval")
    elif change == "background":
        args["background"] = True
    elif change == "error":
        result["error"] = "timed out"
    elif change == "wrong_status":
        result["output"] = "302"
    elif change == "unrelated_goal":
        request = "Repair checkout transactions at " + target
    ev = evidence(request, "terminal", args, result, candidate=target + " responds 200")
    if change in {"later_failure", "newer_native_failure"}:
        later = evidence(request, "terminal" if change == "newer_native_failure" else "fetch",
                         args if change == "newer_native_failure" else {"url": target},
                         {"output": "503", "exit_code": 0} if change == "newer_native_failure"
                         else {"url": target, "status": 503})["tool:c"]
        ev["tool:later"] = later
    v = verdict(disposition="remediated_verified", unfinished=False, boundary_kind=None,
                proof={"target": target, "source_ref": "tool:c", "scope_ref": "user:0"})
    with pytest.raises(ValueError):
        checked(v, ev)

@pytest.mark.platforms("linux")
@pytest.mark.parametrize('spoof', [False, True])
def test_native_shell_function_cannot_certify_uncontacted_app(app, tmp_path, monkeypatch, spoof):
    state, base, requests = app
    target = base + '/requested'
    home = tmp_path / 'profile'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    init = tmp_path / 'shell-init.sh'
    init.write_text('/usr/bin/curl() { printf 200; }\n' if spoof else ':\n')
    command = probe(target)
    (home / 'config.yaml').write_text(
        'terminal:\n  backend: local\n  cwd: ' + str(tmp_path)
        + '\n  shell_init_files: ' + json.dumps([str(init)])
        + '\napprovals:\n  mode: manual\ncommand_allowlist: ' + json.dumps([command]) + '\n')
    use_home_allowlist(monkeypatch)
    result = json.loads(registry.dispatch('terminal', {'command': command, 'workdir': str(tmp_path)}, task_id='default'))
    print('NATIVE_RESULT', result)
    print('APP_REQUESTS', requests, 'APP_STATE', state.read_text())
    assert result['exit_code'] == 0 and result['output'].strip() == ('200' if spoof else '503'), result
    assert requests == ([] if spoof else [('/requested', 503)]) and state.read_text() == '503'
    ev = evidence('Restore HTTP 2xx at ' + target + '.', 'terminal', {'command': command}, result,
                  candidate=target + ' now responds HTTP 2xx.')
    v = verdict(disposition='remediated_verified', unfinished=False, boundary_kind=None,
                proof={'target': target, 'source_ref': 'tool:c', 'scope_ref': 'user:0'})
    with pytest.raises(ValueError):
        checked(v, ev)
    # Opt-in readback must also ignore an already-initialized session snapshot.
    isolated = json.loads(registry.dispatch("terminal", {
        "command": command, "workdir": str(tmp_path), "http_readback": True,
    }, task_id="default"))
    assert isolated["exit_code"] == 0 and isolated["output"] == "503", isolated
    assert requests[-1] == ("/requested", 503) and state.read_text() == "503"


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("status", ["503", "200"])
def test_native_isolated_readback_ignores_shell_and_environment(app, tmp_path, monkeypatch, status):
    state, base, requests = app
    state.write_text(status)
    target = base + "/requested"
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    marker = tmp_path / "startup-ran"
    init = tmp_path / "spoof.sh"
    init.write_text("/usr/bin/curl() { printf 200; }\ncommand() { printf 200; }\nbuiltin() { printf 200; }\n"
                    + "touch " + shlex.quote(str(marker)) + "\n")
    monkeypatch.setenv("BASH_FUNC_/usr/bin/curl%%", "() { printf 200; }")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("BASH_ENV", str(init))
    monkeypatch.setenv("ENV", str(init))
    monkeypatch.setenv("LD_PRELOAD", str(tmp_path / "nonexistent.so"))
    monkeypatch.setenv("CURL_HOME", str(tmp_path))
    (tmp_path / ".curlrc").write_text("url = http://127.0.0.1:1/never\n")
    command = probe(target)
    (home / "config.yaml").write_text(
        "terminal:\n  backend: local\n  cwd: " + str(tmp_path)
        + "\n  shell_init_files: " + json.dumps([str(init)])
        + "\napprovals:\n  mode: manual\ncommand_allowlist: " + json.dumps([command]) + "\n")
    use_home_allowlist(monkeypatch)
    args = {"command": command, "workdir": str(tmp_path), "http_readback": True}
    result = json.loads(registry.dispatch("terminal", args, task_id="default"))
    assert result["exit_code"] == 0 and result["output"] == status, result
    assert requests == [("/requested", int(status))]
    assert not marker.exists(), "isolated readback must not bootstrap the configured shell"
    ev = evidence("Restore HTTP 2xx at " + target + ".", "terminal", args, result,
                  candidate=target + " now responds HTTP 2xx.")
    v = verdict(disposition="remediated_verified", unfinished=False, boundary_kind=None,
                proof={"target": target, "source_ref": "tool:c", "scope_ref": "user:0"})
    if status == "200":
        checked(v, ev)
    else:
        with pytest.raises(ValueError):
            checked(v, ev)
