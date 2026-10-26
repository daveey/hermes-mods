"""Deterministic HTTP provider with a first-primary barrier for real UI controls."""
import json
import time
from http.server import BaseHTTPRequestHandler
from test_goal_disposition_transport import TEXTS

class Model(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append(body)
        primary_count = sum(c.get('model') == 'acceptance-primary' and bool(c.get('stream'))
                            for c in self.server.calls)
        if body.get('model') == 'acceptance-primary' and body.get('stream') and primary_count == 1:
            (self.server.home / 'primary-started').touch()
            deadline = time.monotonic() + 120
            while not self.server.release.is_set() and not (self.server.home / 'release-primary').exists():
                if time.monotonic() > deadline:
                    raise TimeoutError('Electron did not release held primary')
                time.sleep(.05)
        analysis = primary_count > (1 if self.server.control == 'stop' else 3)
        if body.get('model') == 'acceptance-judge':
            content = json.dumps(dict(goal='Repair example app', actionable=not analysis,
                unfinished=not analysis, disposition='analysis_only' if analysis else 'abandoned',
                evidence_refs=['user:0', 'candidate'], boundary_kind=None,
                continuation_ref=None, confidence=.99))
        elif body.get('model') == 'acceptance-primary' and body.get('stream'):
            content = TEXTS['analysis' if analysis else 'abandoned']
        else:
            content = 'Local acceptance title'
        chunk = dict(id='local-acceptance', object='chat.completion', model=body.get('model'),
            choices=[dict(index=0, message=dict(role='assistant', content=content), finish_reason='stop')],
            usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if body.get('stream') else 'application/json')
        self.end_headers()
        if body.get('stream'):
            for part in (content[:20], content[20:]):
                frame = dict(chunk, object='chat.completion.chunk', choices=[
                    dict(index=0, delta=dict(content=part), finish_reason=None)])
                self.wfile.write(('data: ' + json.dumps(frame) + '\n\n').encode())
                self.wfile.flush()
            frame = dict(chunk, object='chat.completion.chunk', choices=[dict(index=0, delta={}, finish_reason='stop')])
            self.wfile.write(('data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n').encode())
        else:
            self.wfile.write(json.dumps(chunk).encode())

