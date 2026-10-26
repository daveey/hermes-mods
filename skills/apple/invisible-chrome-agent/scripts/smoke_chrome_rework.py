"""Additional real-Chrome acceptance used by smoke_chrome_lifecycle.

Only accepts the fresh isolated harness's connections and temporary root.
"""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('isolated Chrome condition timed out')


def exercise(api, ledger, lifecycle, client, tid, sid, request, root, upstream):
    dialog = threading.Event()
    client.on_event = lambda event: dialog.set() if event.get('method') == 'Page.javascriptDialogOpening' else None
    client.call('Page.enable', {}, session=sid)
    client.call('Target.setDiscoverTargets', {'discover': True})
    with ThreadPoolExecutor(2) as pool:
        evaluate = pool.submit(client.call, 'Runtime.evaluate', {'expression': 'alert("isolated dialog"); 42'}, session=sid)
        assert dialog.wait(5), 'dialog event missing'
        client.call('Page.handleJavaScriptDialog', {'accept': True}, session=sid, timeout=3)
        assert evaluate.result(timeout=5)['result']['value'] == 42
        dialog.clear()
        with ledger.transaction(readonly=True) as data:
            cap = data['targets'][tid]['capability']
        handler = api.CDP(request('/lease/' + cap + '/json/version')['webSocketDebuggerUrl'])
        try:
            handler_session = handler.call('Target.attachToTarget', {'targetId': tid, 'flatten': True})['sessionId']
            handler_dialog = threading.Event()
            handler.on_event = lambda event: handler_dialog.set() if event.get('method') == 'Page.javascriptDialogOpening' else None
            handler.call('Page.enable', {}, session=handler_session)
            evaluate = pool.submit(client.call, 'Runtime.evaluate', {'expression': 'alert("second connection"); 42'}, session=sid)
            assert dialog.wait(5) and handler_dialog.wait(5)
            handler.call('Page.handleJavaScriptDialog', {'accept': True}, session=handler_session, timeout=3)
            assert evaluate.result(timeout=5)['result']['value'] == 42
        finally:
            handler.close()
        long_eval = pool.submit(client.call, 'Runtime.evaluate', {
            'expression': 'new Promise(resolve => setTimeout(() => resolve(42), 4000))', 'awaitPromise': True}, session=sid)
        start = time.monotonic()
        observer = api.Supervisor()
        observer.lifecycle, observer.cdp = lifecycle, client
        observer.lifecycle_tick()
        assert time.monotonic() - start < 2, 'long evaluate blocked supervisor tick'
        other = request('/lifecycle/acquire', dict(installation_id='smoke-installation',
                        owner=[str(root), 'other-task', 'same-session'], timeout_s=30))
        peer = api.CDP(request('/lease/' + other['capability'] + '/json/version')['webSocketDebuggerUrl'])
        try:
            assert peer.call('Target.getTargets')['targetInfos'] == []
            try:
                peer.call('Target.attachToTarget', {'targetId': tid, 'flatten': True})
                raise AssertionError('cross-owner attach allowed')
            except api.CDPError:
                pass
            assert time.monotonic() - start < 3, 'long evaluate blocked another owner'
            assert long_eval.result(timeout=8)['result']['value'] == 42
        finally:
            peer.close()
            request('/lifecycle/complete', dict(installation_id='smoke-installation',
                    capability=other['capability'], request_id=other['request_id']))
            request('/lifecycle/release', dict(installation_id='smoke-installation', capability=other['capability']))
    client.call('Runtime.evaluate', {'expression': 'window.open("about:blank", "owned-popup")', 'userGesture': True}, session=sid)
    popup = wait_for(lambda: next((t for t in client.call('Target.getTargets')['targetInfos']
                                  if t.get('openerId') == tid), None))
    popup_sid = client.call('Target.attachToTarget', {'targetId': popup['targetId'], 'flatten': True})['sessionId']
    assert client.call('Runtime.evaluate', {'expression': '21 + 21'}, session=popup_sid)['result']['value'] == 42
    print('PASS real dialog/same-socket concurrency, long evaluate/cross-owner isolation, opener popup use', flush=True)
    download_tab_close(api, ledger, lifecycle, client, root, upstream)


def download_tab_close(api, ledger, lifecycle, client, root, upstream):
    release_body = threading.Event()
    body = b'isolated download payload\n' * 65536

    class Download(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', 'attachment; filename=lease-download.bin')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body[:32768])
            self.wfile.flush()
            if release_body.wait(15):
                self.wfile.write(body[32768:])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Download)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    observer = api.Supervisor()
    observer.lifecycle = lifecycle
    raw = api.CDP(api.cdp_version(port=upstream)['webSocketDebuggerUrl'], on_event=observer.on_event)
    destination = root / 'downloads'
    destination.mkdir()
    raw.call('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(destination), 'eventsEnabled': True})
    try:
        target = client.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        session = client.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        client.call('Page.navigate', {'url': 'http://127.0.0.1:%s/file' % server.server_port}, session=session)

        def downloading():
            with ledger.transaction(readonly=True) as data:
                return any(d.get('target') == target for d in data['downloads'].values())

        wait_for(downloading)
        client.call('Target.closeTarget', {'targetId': target})
        wait_for(lambda: not any(t['targetId'] == target for t in raw.call('Target.getTargets')['targetInfos']))
        assert downloading(), 'tab close lost in-progress download'
        release_body.set()
        result = destination / 'lease-download.bin'
        wait_for(lambda: result.exists() and result.stat().st_size == len(body), timeout=15)
        assert result.read_bytes() == body
        wait_for(lambda: not downloading())
        print('PASS real download: frame fallback, initiating tab closes, full bytes arrive, protection clears', flush=True)
    finally:
        release_body.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        raw.close()
