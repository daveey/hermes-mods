"""Real isolated workers, no network or credentials: fault injection precedes IO."""
import os
import time

import pytest

from plugins.runners.providers import runtime


def call(client):
    return client('GET', 'https://api.github.com/x',
                  headers={'Authorization': 'Bearer fictional-deadline-token'},
                  timeout=0.2, allow_redirects=False)


@pytest.mark.parametrize('stage', ['dns', 'headers', 'body', 'close'])
def test_real_worker_deadline_kills_and_reaps(monkeypatch, stage):
    # The production HTTPSConnection really calls the resolver; replace only
    # that resolver in the worker so no live DNS or connection can occur.
    injection = '''
import socket, time, http.client
class Reply:
    status = 200
    def getheaders(self): return []
    def read1(self, size):
        if STAGE == 'body': time.sleep(2)
        return b'{}' if not getattr(self, 'done', False) and not setattr(self, 'done', True) else b''
class Connection:
    sock = None
    def __init__(self, *a, **k): pass
    def request(self, *a, **k): pass
    def getresponse(self):
        if STAGE == 'headers': time.sleep(2)
        return Reply()
    def close(self):
        if STAGE == 'close': time.sleep(2)
def stalled_dns(*a, **k):
    time.sleep(2)
    raise OSError('offline resolver')
if STAGE == 'dns': socket.getaddrinfo = stalled_dns
else: http.client.HTTPSConnection = Connection
'''.replace('STAGE', repr(stage))
    # An explicit hook is not necessary: only the static worker program changes.
    if hasattr(runtime, '_HTTP_CHILD'):
        monkeypatch.setattr(runtime, '_HTTP_CHILD', injection + runtime._HTTP_CHILD)
    else:
        # Baseline regression: the old in-process resolver blocks the caller.
        import socket
        def stalled_dns(*args, **kwargs):
            time.sleep(2)
            raise OSError('offline resolver')
        monkeypatch.setattr(socket, 'getaddrinfo', stalled_dns)
    children = []
    real_popen = runtime.subprocess.Popen
    def spawn(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(runtime.subprocess, 'Popen', spawn)
    start = time.monotonic()
    with pytest.raises(runtime.RuntimeBoundaryError, match='^http_transport_failed$'):
        call(runtime.GitHubHTTPTransport())
    assert time.monotonic() - start < 1.0
    assert len(children) == 1 and children[0].poll() is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(children[0].pid, os.WNOHANG)


def test_worker_success_and_private_channels(monkeypatch, capfd):
    injection = '''
import http.client
class Reply:
    status = 200
    def getheaders(self): return [('ETag', 'offline')]
    def read1(self, size):
        if getattr(self, 'done', False): return b''
        self.done = True
        return b'{"ok":true}'
class Connection:
    sock = None
    def __init__(self, *a, **k): pass
    def request(self, *a, **k):
        assert k['headers']['authorization'] == 'Bearer fictional-deadline-token'
    def getresponse(self): return Reply()
    def close(self): pass
http.client.HTTPSConnection = Connection
'''
    production_program = runtime._HTTP_CHILD
    assert 'fictional-deadline-token' not in production_program
    monkeypatch.setattr(runtime, '_HTTP_CHILD', injection + production_program)
    real_popen = runtime.subprocess.Popen
    def spawn(argv, **kwargs):
        # Fixture text is in the injected program, but request values must never
        # be additional arguments, environment variables, or captured stdout.
        assert argv[-1].isdigit()
        assert kwargs['stdout'] == runtime.subprocess.DEVNULL
        assert kwargs['stderr'] == runtime.subprocess.DEVNULL
        assert kwargs['stdin'] == runtime.subprocess.DEVNULL
        assert 'fictional-deadline-token' not in repr(kwargs.get('env'))
        assert kwargs['close_fds'] and len(kwargs['pass_fds']) == 1
        return real_popen(argv, **kwargs)
    monkeypatch.setattr(runtime.subprocess, 'Popen', spawn)
    result = call(runtime.GitHubHTTPTransport())
    assert result.status == 200 and result.body == {'ok': True}
    assert result.headers == {'etag': 'offline'}
    assert capfd.readouterr() == ('', '')


@pytest.mark.parametrize('program', [
    "import os; os._exit(1)",
    "import time; time.sleep(2)",
    "raise RuntimeError('fictional-worker-error')",
])
def test_repeated_worker_failures_are_sanitized_and_close_fds(monkeypatch, capfd, program):
    monkeypatch.setattr(runtime, '_HTTP_CHILD', program)
    before = len(os.listdir('/proc/self/fd')) if os.path.isdir('/proc/self/fd') else None
    client = runtime.GitHubHTTPTransport(max_timeout=0.1)
    start = time.monotonic()
    for _ in range(3):
        with pytest.raises(runtime.RuntimeBoundaryError) as error:
            call(client)
        assert str(error.value) == 'http_transport_failed'
        assert error.value.__suppress_context__
    assert time.monotonic() - start < 1.0
    if before is not None:
        assert len(os.listdir('/proc/self/fd')) == before
    assert capfd.readouterr() == ('', '')


def test_invalid_default_request_does_not_launch_worker(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid request launched worker')
    monkeypatch.setattr(runtime.subprocess, 'Popen', forbidden)
    with pytest.raises(runtime.RuntimeBoundaryError, match='http_transport_failed'):
        runtime.GitHubHTTPTransport()('GET', 'https://example.invalid/', headers={},
                                      timeout=1, allow_redirects=False)
