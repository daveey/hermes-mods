"""Unpatched production serve, canonical tool spawn and process delivery over loopback."""
import json
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_goal_disposition_transport import Frames, isolated_env
from test_goal_serve_acceptance import _ready, _turn, _idle


class FixtureFrames(Frames):
    def receive(self):
        frame = super().receive()
        if frame.get("method") == "approval" and "id" in frame:
            assert frame["params"]["command"] in self.commands
            self.ws.send(json.dumps(dict(jsonrpc="2.0", id=frame["id"], result=dict(choice="once"))))
        return frame


class Model(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append(body)
        tool = None
        if body.get("model") == "process-judge":
            evidence = json.loads(body["messages"][-1]["content"])["evidence"]
            owned = evidence["candidate"]["content"] == "A native process is continuing the repair."
            handles = re.findall(r"proc_[0-9a-f]{12}", json.dumps(evidence))
            content = json.dumps(dict(goal="Repair example app", actionable=owned, unfinished=owned,
                disposition="owned_continuing" if owned else "analysis_only",
                evidence_refs=["user:0", "candidate"], boundary_kind=None, confidence=.99,
                continuation_ref=dict(gateway=socket.gethostname(), profile="default", process_id=handles[-1])
                if owned and handles else None))
        elif body.get("model") == "process-primary" and body.get("stream"):
            step = self.server.step
            self.server.step += 1
            if step in (0, 3):
                marker = self.server.home / ("warm-release" if step == 0 else "repair-release")
                script = ("from pathlib import Path; import threading,time; "
                          "end=time.monotonic()+60; "
                          "exec(\"while not Path(" + repr(str(marker)) + ").exists() and time.monotonic()<end: threading.Event().wait(.05)\")")
                command = "exec " + shlex.quote(sys.executable) + " -c " + shlex.quote(script)
                self.server.commands.append(command)
                tool = dict(index=0, id=f"call_fixture_{step}", type="function", function=dict(name="terminal",
                    arguments=json.dumps(dict(command=command, background=True, notify=True))))
                content = ""
            else:
                content = {1: "Fixture started.", 2: "Fixture completion observed.",
                           4: "A native process is continuing the repair."}.get(step, "Fixture complete.")
        else:
            content = "Local process fixture"
        chunk = dict(id="process-fixture", object="chat.completion", model=body.get("model"),
            choices=[dict(index=0, message=dict(role="assistant", content=content), finish_reason="stop")],
            usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
        self.end_headers()
        if body.get("stream"):
            delta = dict(tool_calls=[tool]) if tool else dict(content=content)
            frame = dict(chunk, object="chat.completion.chunk", choices=[dict(index=0, delta=delta, finish_reason=None)])
            self.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode())
            frame = dict(chunk, object="chat.completion.chunk", choices=[
                dict(index=0, delta={}, finish_reason="tool_calls" if tool else "stop")])
            self.wfile.write(("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode())
        else:
            self.wfile.write(json.dumps(chunk).encode())


def test_process_ownership_over_production_serve(tmp_path):
    from websockets.sync.client import connect
    import yaml
    home = tmp_path / "home"
    env = isolated_env(home)
    token = "synthetic-process-fixture-token"
    env.update(HERMES_DASHBOARD_SESSION_TOKEN=token, HERMES_DESKTOP_READY_FILE=str(home / "ready.json"))
    provider = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    provider.calls, provider.step, provider.home, provider.commands = [], 0, home, []
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{provider.server_port}/v1"
    config = yaml.safe_load((home / "config.yaml").read_text())
    config["model"] = dict(default="process-primary", provider="custom", base_url=endpoint)
    config["auxiliary"] = {name: dict(provider="custom", model=model, base_url=endpoint, api_key="local-test")
        for name, model in [("coach_goal_disposition", "process-judge"), ("title", "process-title")]}
    (home / "config.yaml").write_text(yaml.safe_dump(config))
    errors = home / "serve-stderr.log"
    frames = None
    child = Path(__file__).with_name("test_goal_serve_acceptance.py")
    with errors.open("w") as err, (home / "serve-stdout.log").open("w") as out:
        process = subprocess.Popen([sys.executable, str(child), "--child"], env=env, cwd=home,
                                   stdout=out, stderr=err, text=True)
        try:
            port = _ready(home / "ready.json", process, errors)
            with connect(f"ws://127.0.0.1:{port}/api/ws?token={token}", open_timeout=30) as ws:
                frames = object.__new__(FixtureFrames)
                frames.commands = provider.commands
                frames.frames, frames.ws = [], ws
                frames.until(lambda f: f.get("params", {}).get("type") == "gateway.ready")
                frames.rpc("client.capabilities", dict(server_requests=True))
                sid = frames.rpc("session.create", dict(source="desktop", cwd=str(home), title="Process fixture"))["session_id"]
                _, first = _turn(frames, sid, "Run a short local fixture.")
                assert first["status"] == "complete", (first, errors.read_text())
                _idle(frames, sid)
                start = len(frames.frames)
                (home / "warm-release").touch()
                _, completion = _turn(frames, sid, start=start)
                assert completion["status"] == "complete", completion
                _idle(frames, sid)
                _, owned = _turn(frames, sid, "Repair example app")
                assert owned["status"] == "complete", (owned, errors.read_text())
                records = [json.loads(line) for line in (home / "coach" / "log.jsonl").read_text().splitlines()]
                assert any(r.get("disposition") == "owned_continuing" for r in records), records
                _idle(frames, sid)
                (home / "repair-release").touch()
                _turn(frames, sid)
                _idle(frames, sid)
                frames.rpc("session.close", dict(session_id=sid))
        finally:
            (home / "warm-release").touch()
            (home / "repair-release").touch()
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
            provider.shutdown()
            provider.server_close()
            thread.join(5)
            (home / "wire.json").write_text(json.dumps(frames.frames if frames else [], indent=2))
            (home / "provider.json").write_text(json.dumps(provider.calls, indent=2))
            assert not thread.is_alive()
            print("SERVE ERRORS", errors.read_text()[-12000:])
            print("TOOLS", [f for f in (frames.frames if frames else [])
                            if f.get("params", {}).get("type") in ("tool.complete", "status.update")])
            print("PROVIDER STEPS", provider.step)
            print("COACH", (home / "coach" / "log.jsonl").read_text())
