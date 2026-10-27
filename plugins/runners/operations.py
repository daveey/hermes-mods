"""Guarded runner management operations (stdlib only; shared by broker and plugin).

Engine runs inside the management broker under its own OS identity. It is the
policy enforcement point: operation allowlist, host/service identity checks,
host-scoped lock, fresh provider activity plus local worker-process checks.
It refuses whenever activity is busy or cannot be determined.

Supported mutation: ``service_start`` of an exact, policy-listed *canary* unit
that is already stopped (inactive/failed). Starting a stopped listener cannot
interrupt a job, so no quiesce is required.

Restart/drain/quiesce of a running listener are DISABLED: there is no tested
race-free quiesce for a GitHub Actions listener. GitHub has no API to stop job
assignment to one online runner; SIGTERM of an idle Runner.Listener races job
assignment; removing custom labels does not stop default-label jobs; a
maintenance mark or elapsed time is not quiescence.

Install/upgrade/unregister/cache deletion/job cancellation/signing changes need
a separate explicit approval. This broker holds no approval authority, so every
such request is refused and any supplied approval is invalid by construction.
"""
import contextlib
import fcntl
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
import uuid

from . import credentials

SCHEMA_VERSION = 1
API = 'management'
READ_ONLY = frozenset({'diagnose'})
MUTATING = frozenset({'service_start'})
ALLOWLIST = READ_ONLY | MUTATING
QUIESCE_EXPLANATION = (
    'Restart, drain and quiesce of a running GitHub Actions listener are disabled: no tested race-free quiesce '
    'exists. GitHub has no API to stop job assignment to one online runner, SIGTERM of an idle Runner.Listener '
    'races job assignment, and removing custom labels does not stop default-label jobs; maintenance marks or '
    'elapsed time are not quiescence. Use a guarded manual procedure that independently prevents assignment '
    'and verifies no worker, or start a listener that is already stopped.')
DISABLED = {'service_restart': QUIESCE_EXPLANATION, 'quiesce': QUIESCE_EXPLANATION, 'drain': QUIESCE_EXPLANATION}
APPROVAL_GATED = frozenset({'install', 'upgrade', 'unregister', 'cache_delete', 'job_cancel', 'signing_change'})
APPROVAL_EXPLANATION = ('This operation needs a separate explicit approval, even inside a repair session. The broker '
                        'holds no approval authority; request a decision card instead.')
ACTIVITY_MAX_AGE = 30.0
MUTATION_COOLDOWN = 120.0
GITHUB_API = 'https://api.github.com'
REQUEST_KEYS = frozenset({'version', 'api', 'operation', 'target_id', 'host_id', 'request_id', 'repair', 'evidence', 'approval'})
TOOL_KEYS = frozenset({'operation', 'target_id', 'repair_id', 'evidence'})
MAX_EVIDENCE = 4096
MAX_UNTRUSTED = 2000

SERVICE_ID = re.compile(r'service_[0-9a-f]{16,64}')
REPAIR_ID = re.compile(r'repair_[0-9a-f]{32}')
REQUEST_ID = re.compile(r'request_[0-9a-f]{32}')
HOST_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
UNIT = re.compile(r'[A-Za-z0-9][A-Za-z0-9@_.:-]{0,120}\.service')
FRAGMENT = re.compile(r'/(etc/systemd/system|usr/lib/systemd/system|lib/systemd/system)/[A-Za-z0-9@_.:-]{1,120}\.service')
REPO = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}')
ABS_PATH = re.compile(r'/[A-Za-z0-9_./@-]{1,255}')


class Refusal(Exception):
    def __init__(self, code, detail=''):
        super().__init__(code)
        self.code, self.detail = code, detail


class ActivityUnknown(Exception):
    """Provider or local activity could not be determined (never carries secrets)."""


def untrusted(text, secrets=()):
    """Wrap host/provider/log text as quoted data that never carries authority."""
    return dict(trust='untrusted_data', text=credentials.redact(str(text), secrets)[:MAX_UNTRUSTED])


# --- policy -------------------------------------------------------------------

def _require(cond, message='invalid_policy'):
    if not cond:
        raise ValueError(message)


def load_policy(raw):
    _require(isinstance(raw, dict))
    _require(set(raw) <= {'version', 'host_id', 'machine_id_sha256', 'management_operations', 'clients', 'targets'})
    _require(raw.get('version') == SCHEMA_VERSION)
    _require(isinstance(raw.get('host_id'), str) and HOST_ID.fullmatch(raw['host_id']))
    if 'machine_id_sha256' in raw:
        _require(isinstance(raw['machine_id_sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', raw['machine_id_sha256']))
    _require(type(raw.get('management_operations')) is bool)
    clients = raw.get('clients')
    _require(isinstance(clients, list) and len(clients) <= 8)
    for c in clients:
        _require(isinstance(c, dict) and set(c) == {'uid'} and type(c['uid']) is int and 0 < c['uid'] < 2**31)
    targets = raw.get('targets')
    _require(isinstance(targets, list) and len(targets) <= 64)
    seen_ids, seen_units = set(), set()
    out = []
    for t in targets:
        _require(isinstance(t, dict))
        _require(set(t) <= {'id', 'host_id', 'unit', 'canary', 'description_contains', 'fragment_path', 'operations',
                            'github', 'runner_root'})
        _require(isinstance(t.get('id'), str) and SERVICE_ID.fullmatch(t['id']))
        _require(isinstance(t.get('host_id'), str) and HOST_ID.fullmatch(t['host_id']))
        _require(isinstance(t.get('unit'), str) and UNIT.fullmatch(t['unit']))
        _require(isinstance(t.get('fragment_path'), str) and FRAGMENT.fullmatch(t['fragment_path']))
        _require(isinstance(t.get('description_contains'), str) and 1 <= len(t['description_contains']) <= 200)
        canary = t.get('canary', False)
        _require(type(canary) is bool)
        operations = t.get('operations')
        _require(isinstance(operations, list) and operations and set(operations) <= ALLOWLIST
                 and len(set(operations)) == len(operations))
        if set(operations) & MUTATING:
            # Mutation is only ever enabled for a dedicated canary registration.
            _require(canary, 'mutation_requires_canary')
            _require('github' in t, 'mutation_requires_provider_check')
        if 'github' in t:
            g = t['github']
            _require(isinstance(g, dict) and set(g) == {'api', 'repo', 'runner_id', 'credential_ref'})
            _require(g['api'] == GITHUB_API, 'endpoint_not_allowlisted')
            _require(isinstance(g['repo'], str) and REPO.fullmatch(g['repo']) and '..' not in g['repo'])
            _require(type(g['runner_id']) is int and 0 < g['runner_id'] < 2**53)
            _require(isinstance(g['credential_ref'], str) and credentials.REF.fullmatch(g['credential_ref']))
        if 'runner_root' in t:
            _require(isinstance(t['runner_root'], str) and ABS_PATH.fullmatch(t['runner_root']) and '..' not in t['runner_root'])
        _require(t['id'] not in seen_ids and t['unit'] not in seen_units, 'duplicate_target')
        seen_ids.add(t['id'])
        seen_units.add(t['unit'])
        out.append(dict(t, canary=canary, operations=list(operations)))
    return dict(raw, clients=[dict(c) for c in clients], targets=out)


# --- ports (real implementations; tests inject fakes) -------------------------

class SystemctlPort:
    PROPERTIES = 'Id,LoadState,ActiveState,SubState,Description,FragmentPath,ControlGroup,MainPID,NRestarts,UnitFileState'

    def __init__(self, run=None):
        import subprocess
        self._run = run or (lambda argv: subprocess.run(argv, capture_output=True, timeout=20, check=False))

    def show(self, unit):
        proc = self._run(['/usr/bin/systemctl', 'show', '--no-pager', '--property=' + self.PROPERTIES, '--', unit])
        if proc.returncode != 0:
            raise ActivityUnknown('systemctl_show_failed')
        props = {}
        for line in proc.stdout.decode('utf-8', 'replace').splitlines()[:64]:
            key, sep, value = line.partition('=')
            if sep:
                props[key] = value[:500]
        return props

    def start(self, unit):
        proc = self._run(['/usr/bin/systemctl', 'start', '--no-ask-password', '--no-block', '--', unit])
        if proc.returncode != 0:
            raise Refusal('start_denied', 'systemctl start was refused for the exact unit')


class CgroupProcessPort:
    """Find Runner.Worker processes in the unit's cgroup and under its runner root."""

    def __init__(self, cgroup_root='/sys/fs/cgroup', proc_root='/proc'):
        self.cgroup_root, self.proc_root = cgroup_root, proc_root

    def _cmdline(self, pid):
        with open(os.path.join(self.proc_root, str(pid), 'cmdline'), 'rb') as f:
            return f.read(8192)

    def workers(self, control_group, runner_root=None):
        found = set()
        if control_group:
            _require(control_group.startswith('/') and '..' not in control_group, 'invalid_cgroup')
            base = os.path.join(self.cgroup_root, control_group.lstrip('/'))
            if os.path.isdir(base):
                for dirpath, _dirs, files in os.walk(base):
                    if 'cgroup.procs' in files:
                        with open(os.path.join(dirpath, 'cgroup.procs')) as f:
                            for line in f:
                                pid = int(line)
                                try:
                                    if b'Runner.Worker' in self._cmdline(pid):
                                        found.add(pid)
                                except FileNotFoundError:
                                    continue
        if runner_root:
            prefix = runner_root.rstrip('/').encode() + b'/'
            for name in os.listdir(self.proc_root):
                if not name.isdigit():
                    continue
                try:
                    cmd = self._cmdline(name)
                except (FileNotFoundError, ProcessLookupError):
                    continue
                if b'Runner.Worker' in cmd and prefix in cmd:
                    found.add(int(name))
        return sorted(found)


def _urllib_transport(url, headers, timeout):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            return resp.status, resp.read(65536)
    except urllib.error.HTTPError as exc:
        return exc.code, b''


class GitHubPort:
    def __init__(self, credential_dir, *, expected_uid, transport=None, clock=time.time):
        self.credential_dir, self.expected_uid = credential_dir, expected_uid
        self.transport, self.clock = transport or _urllib_transport, clock

    def runner(self, github):
        try:
            token = credentials.load_secret(self.credential_dir, github['credential_ref'], expected_uid=self.expected_uid)
        except credentials.CredentialError as exc:
            raise ActivityUnknown(str(exc)) from None
        url = '%s/repos/%s/actions/runners/%d' % (GITHUB_API, github['repo'], github['runner_id'])
        headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'hermes-runners-broker'}
        try:
            status, body = self.transport(url, headers, 10)
        except Exception:  # noqa: BLE001 - message may echo headers; never propagate it
            raise ActivityUnknown('provider_request_failed') from None
        if status != 200:
            raise ActivityUnknown('provider_status_%d' % int(status))
        try:
            data = json.loads(body)
        except ValueError:
            raise ActivityUnknown('provider_malformed') from None
        if not isinstance(data, dict) or data.get('id') not in (None, github['runner_id']):
            raise ActivityUnknown('provider_identity_mismatch')
        return dict(status=data.get('status'), busy=data.get('busy'), observed_at=self.clock())


class FlockLocks:
    """Host-scoped, cross-process exclusive lock (non-blocking)."""

    def __init__(self, directory):
        self.directory = os.fspath(directory)
        os.makedirs(self.directory, mode=0o700, exist_ok=True)

    @contextlib.contextmanager
    def acquire(self, host_id):
        _require(HOST_ID.fullmatch(host_id), 'invalid_host')
        fd = os.open(os.path.join(self.directory, host_id + '.lock'), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refusal('host_lock_held', 'another management operation holds this host') from None
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# --- engine ---------------------------------------------------------------------

def _parse(obj):
    if not isinstance(obj, dict):
        raise Refusal('invalid_request')
    extra = set(obj) - REQUEST_KEYS
    if extra:
        raise Refusal('unknown_field', 'fields not accepted: ' + ','.join(sorted(extra))[:200])
    if obj.get('version') != SCHEMA_VERSION or obj.get('api') != API:
        raise Refusal('unknown_api' if obj.get('api') != API else 'invalid_request')
    for key, pattern in (('target_id', SERVICE_ID), ('host_id', HOST_ID), ('request_id', REQUEST_ID)):
        if not isinstance(obj.get(key), str) or not pattern.fullmatch(obj[key]):
            raise Refusal('invalid_request', key)
    repair = obj.get('repair')
    if (not isinstance(repair, dict) or set(repair) != {'id', 'subject_target_id'}
            or not isinstance(repair['id'], str) or not REPAIR_ID.fullmatch(repair['id'])
            or not isinstance(repair['subject_target_id'], str) or not SERVICE_ID.fullmatch(repair['subject_target_id'])):
        raise Refusal('invalid_request', 'repair')
    if 'evidence' in obj and (not isinstance(obj['evidence'], str) or len(obj['evidence']) > MAX_EVIDENCE):
        raise Refusal('invalid_request', 'evidence')
    if not isinstance(obj.get('operation'), str):
        raise Refusal('invalid_request', 'operation')
    return obj


class Engine:
    def __init__(self, policy, *, systemd, github, processes, locks, clock=time.time, settle_seconds=20.0, audit=None):
        self.policy, self.systemd, self.github, self.processes, self.locks = policy, systemd, github, processes, locks
        self.clock, self.settle_seconds, self.audit = clock, settle_seconds, audit
        self._last_mutation = {}

    def handle(self, obj, *, peer_uid):
        request_id = obj.get('request_id') if isinstance(obj, dict) else None
        try:
            result = dict(ok=True, data=self._handle(obj, peer_uid))
        except Refusal as exc:
            result = dict(ok=False, refusal=exc.code, detail=exc.detail)
        if self.audit:
            with contextlib.suppress(Exception):
                self.audit(dict(at=self.clock(), peer_uid=peer_uid, request_id=request_id if isinstance(request_id, str) else None,
                                operation=obj.get('operation') if isinstance(obj, dict) and isinstance(obj.get('operation'), str) else None,
                                target_id=obj.get('target_id') if isinstance(obj, dict) and isinstance(obj.get('target_id'), str) else None,
                                ok=result['ok'], refusal=result.get('refusal')))
        return result

    def _handle(self, obj, peer_uid):
        if type(peer_uid) is not int or peer_uid not in {c['uid'] for c in self.policy['clients']}:
            raise Refusal('peer_not_authorized')
        req = _parse(obj)
        op = req['operation'][:64]
        if op in DISABLED:
            raise Refusal('restart_disabled_no_safe_quiesce', DISABLED[op])
        if op in APPROVAL_GATED:
            if 'approval' in req:
                raise Refusal('approval_invalid', 'no approval authority is configured in this broker; ' + APPROVAL_EXPLANATION)
            raise Refusal('separate_explicit_approval_required', APPROVAL_EXPLANATION)
        if op not in ALLOWLIST:
            raise Refusal('operation_not_allowlisted', 'allowlisted operations: ' + ', '.join(sorted(ALLOWLIST)))
        if 'approval' in req:
            raise Refusal('approval_invalid', 'approvals are not accepted for allowlisted operations')
        if req['host_id'] != self.policy['host_id']:
            raise Refusal('wrong_host', 'request is for a different host than this broker manages')
        target = next((t for t in self.policy['targets'] if t['id'] == req['target_id']), None)
        if target is None:
            raise Refusal('target_not_allowlisted')
        if target['host_id'] != self.policy['host_id']:
            raise Refusal('wrong_host', 'target is not on this broker host')
        if op not in target['operations']:
            raise Refusal('operation_not_permitted_for_target')
        if req['repair']['subject_target_id'] != target['id']:
            raise Refusal('sibling_target_refused', 'only the repair subject service may be operated; siblings never')
        evidence = untrusted(req['evidence']) if 'evidence' in req else None
        if op in READ_ONLY:
            return self._diagnose(target, op, evidence)
        if not self.policy['management_operations']:
            raise Refusal('management_operations_disabled', 'broker policy management_operations=false')
        last = self._last_mutation.get(target['id'])
        if last is not None and self.clock() - last < MUTATION_COOLDOWN:
            raise Refusal('cooldown_active')
        with self.locks.acquire(target['host_id']):
            return self._start(target, op, evidence)

    def _identity(self, target):
        try:
            props = self.systemd.show(target['unit'])
        except ActivityUnknown:
            raise Refusal('activity_unknown', 'service state unavailable') from None
        if (props.get('Id') != target['unit'] or props.get('LoadState') != 'loaded'
                or props.get('FragmentPath') != target['fragment_path']
                or target['description_contains'] not in props.get('Description', '')):
            raise Refusal('service_identity_mismatch', 'unit identity does not match the provisioned service')
        return props

    def _workers(self, target, props):
        try:
            found = self.processes.workers(props.get('ControlGroup') or None, **(
                dict(runner_root=target['runner_root']) if target.get('runner_root') else {}))
        except Exception:  # noqa: BLE001 - unknown local activity always refuses
            raise Refusal('activity_unknown', 'local worker-process state could not be determined') from None
        return 'present' if found else 'absent'

    @staticmethod
    def _service(props):
        return dict(unit=props.get('Id'), active_state=props.get('ActiveState'), sub_state=props.get('SubState'),
                    unit_file_state=props.get('UnitFileState'), n_restarts=props.get('NRestarts'))

    def _diagnose(self, target, op, evidence):
        props = self._identity(target)
        try:
            workers = self._workers(target, props)
        except Refusal:
            workers = 'unknown'
        data = dict(operation=op, target_id=target['id'], service=self._service(props), local_workers=workers,
                    untrusted=dict(description=untrusted(props.get('Description', ''))), observed_at=self.clock(),
                    restart='disabled', restart_reason=QUIESCE_EXPLANATION)
        if evidence:
            data['evidence'] = evidence
        return data

    def _start(self, target, op, evidence):
        props = self._identity(target)
        if props.get('ActiveState') not in ('inactive', 'failed'):
            raise Refusal('service_active_restart_disabled', QUIESCE_EXPLANATION)
        if self._workers(target, props) == 'present':
            raise Refusal('local_worker_present', 'a Runner.Worker process exists for this runner')
        try:
            gh = self.github.runner(target['github'])
        except Exception:  # noqa: BLE001
            raise Refusal('activity_unknown', 'fresh provider activity could not be determined') from None
        if gh.get('status') not in ('online', 'offline') or type(gh.get('busy')) is not bool:
            raise Refusal('activity_unknown', 'provider returned unrecognized activity')
        if self.clock() - gh.get('observed_at', 0) > ACTIVITY_MAX_AGE:
            raise Refusal('activity_stale', 'provider activity observation is not fresh')
        if gh['busy']:
            raise Refusal('runner_busy', 'provider reports this runner busy')
        pre = dict(service=self._service(props), local_workers='absent',
                   github=dict(status=gh['status'], busy=False, fresh=True))
        self._last_mutation[target['id']] = self.clock()
        self.systemd.start(target['unit'])
        deadline = time.monotonic() + self.settle_seconds
        while True:
            post = self._service(self._identity(target))
            if post['active_state'] == 'active' or time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        if post['active_state'] != 'active':
            raise Refusal('start_unverified', 'start issued but the unit is not active: ' + str(post['active_state']))
        data = dict(operation=op, target_id=target['id'], outcome='control_plane_started',
                    verification='restored_unverified_build', pre=pre, post=post, observed_at=self.clock())
        if evidence:
            data['evidence'] = evidence
        return data


# --- client + plugin tool (runs in the agent/gateway, never holds credentials) ---

DEFAULT_SOCKET = '/run/hermes-runners-broker/manage.sock'


class BrokerClient:
    def __init__(self, path=DEFAULT_SOCKET, timeout=60):
        self.path, self.timeout = path, timeout

    def request(self, body):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(self.timeout)
            s.connect(self.path)
            s.sendall(json.dumps(body).encode())
            s.shutdown(socket.SHUT_WR)
            data = b''
            while chunk := s.recv(65536):
                data += chunk
                if len(data) > 1 << 20:
                    raise ValueError('response_too_large')
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError('malformed_response')
        return result


ACTIVE_REPAIR_STATES = ('active', 'verifying')


def operate_tool(args, *, config, repair, client):
    def refuse(code, detail=''):
        return dict(ok=False, refusal=code, detail=detail)
    if not isinstance(args, dict):
        return refuse('invalid_request')
    extra = set(args) - TOOL_KEYS
    if extra:
        return refuse('unknown_field', ','.join(sorted(extra))[:200])
    if args.get('repair_id') != repair.get('id'):
        return refuse('repair_mismatch')
    if repair.get('state') not in ACTIVE_REPAIR_STATES:
        return refuse('repair_not_active')
    binding = next((b for b in config.get('bindings', []) if b.get('runner_id') == repair.get('entity_id')), None)
    if binding is None:
        return refuse('repair_subject_unbound', 'the repair target has no verified service binding')
    if args.get('target_id') != binding['service_ref']:
        return refuse('sibling_target_refused', 'only the repair subject service may be operated')
    operation = args.get('operation')
    if operation != 'diagnose' and not config.get('features', {}).get('management_operations'):
        return refuse('management_operations_disabled',
                      'management_operations is off; diagnosis-only. Mutations need the broker, policy and flag.')
    body = dict(version=SCHEMA_VERSION, api=API, operation=operation, target_id=binding['service_ref'],
                host_id=binding['host_id'], request_id='request_' + uuid.uuid4().hex,
                repair=dict(id=repair['id'], subject_target_id=binding['service_ref']))
    if 'evidence' in args:
        body['evidence'] = args['evidence']
    try:
        return client.request(body)
    except (OSError, ValueError):
        return refuse('broker_unavailable', 'management broker socket unreachable or malformed reply')
