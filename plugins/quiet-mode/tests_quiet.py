import importlib.util, os, tempfile, time
from pathlib import Path
os.environ["HERMES_QUIET_MODE_FILE"] = str(Path(tempfile.mkdtemp()) / "q.json")
spec = importlib.util.spec_from_file_location("qm", Path(__file__).parent / "__init__.py")
qm = importlib.util.module_from_spec(spec); spec.loader.exec_module(qm)
hook = qm._pre_tool_call

def blocked(t, a=None): return (hook(tool_name=t, args=a or {}) or {}).get("action") == "block"

def test_off_allows_everything():
    qm.handle("off")
    assert not blocked("computer_use") and not blocked("terminal", {"command": "open -a Safari"})

def test_on_blocks_disruptive():
    qm.handle("on")
    for t, a in [("computer_use", {}), ("text_to_speech", {"text": "hi"}), ("browser_exec", {"code": "x"}),
                 ("desktop_preview", {}), ("spotify_playback", {}), ("mcp__chrome_devtools__click", {}),
                 ("terminal", {"command": "open https://x.com"}), ("terminal", {"command": "cd x && say hello"}),
                 ("terminal", {"command": "osascript -e 'beep'"}), ("terminal", {"command": "afplay a.mp3"}),
                 ("execute_code", {"code": "terminal('open -a Music')"}),
                 ("ha_call_service", {"domain": "media_player", "service": "play_media"}),
                 ("ha_call_service", {"service": "light.turn_on"}),
                 ("mcp__tool_call", {"calls": [{"name": "mcp__chrome_devtools__navigate_page", "arguments": {}}]})]:
        assert blocked(t, a), (t, a)

def test_on_allows_quiet_work():
    qm.handle("on")
    for t, a in [("read_file", {"path": "x"}), ("terminal", {"command": "git status && pytest -q"}),
                 ("terminal", {"command": "ls ~/.hermes/plugins/spotify"}), ("web_search", {"query": "q"}),
                 ("browser_vault_list", {}), ("ha_get_state", {}),
                 ("ha_call_service", {"domain": "climate", "service": "set_temperature"}),
                 ("terminal", {"command": "python -c 'f=open(\"a\")'"})]:
        assert not blocked(t, a), (t, a)

def test_duration_and_expiry():
    assert qm.parse_duration("2h") == 7200 and qm.parse_duration("90") == 5400 and qm.parse_duration("1h30m") == 5400
    qm.handle("on 2h"); assert qm.active_state()
    qm.write_state(True, until=time.time() - 1); assert qm.active_state() is None
    assert not blocked("computer_use")
    qm.handle("off"); assert "ON" in qm.handle(""); assert "OFF" in qm.handle("")
