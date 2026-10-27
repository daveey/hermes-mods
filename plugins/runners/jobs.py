"""GitHub workflow dispatch from the owner gateway (plan sections 6, 8 and 10.G).

JobDomain is the single domain service behind REST ``/workflows``/``/jobs`` and the
``runners_run``/``runners_job`` tools. It never builds locally and never selects a
"latest run": every job is correlated by its own request ID.

Submission order (each step durable before the next):
 1. Validate request, owner, allowlisted workflow, typed inputs, rate limit and
    idempotency (canonical digest; a reused key with another payload conflicts).
 2. Gates: feature ``dispatch`` (or explicit owner-side canary mode for canary
    workflows), workflow ``dispatch_enabled``, claim broker reachable, proof that
    runners reached the claim endpoint (canary mode is that proof), eligible
    runner online (else reject or queue per workflow policy, never local fallback),
    source SHA reachable from the trusted ref which must be protected.
 3. Persist intent with a fresh request ID, register it with the claim broker.
 4. Mark ``dispatching``, call GitHub. A timeout/connection loss/5xx after the
    request may have been accepted becomes ``unknown_outcome``: reconcile by
    request ID only, never resubmit. A definite 4xx is ``rejected``.

Reconciliation binds the exact run by request ID in its run-name, prefers the run
that holds the broker claim, and tracks run/attempt/job/runner and the workflow
ref SHA separately from the checked-out source SHA. Artifacts are downloaded only
after completion, checked against the provider digest, safely extracted and
validated against their provenance manifest. Expired artifacts are reported.
"""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import threading
import time
import uuid
import zipfile
import zlib

if __package__:
    from .models import validate, runner_id as provider_runner_key
else:  # pragma: no cover - disk loader fallback
    from models import validate, runner_id as provider_runner_key  # type: ignore

SHA = re.compile(r'[0-9a-f]{40}')
REQUEST_ID = re.compile(r'request_[0-9a-f]{32}')
SAFE_STRING = re.compile(r'[A-Za-z0-9._-]{1,128}')
INPUT_NAME = re.compile(r'[a-z][a-z0-9_]{0,63}')
ARTIFACT_LIMITS = dict(zip_bytes=64 * 1024 * 1024, files=64, file_bytes=48 * 1024 * 1024,
                       total_bytes=96 * 1024 * 1024)
RESERVED_INPUTS = ('request_id', 'source_sha')
# reconcile leaves a 'dispatching' record to its submitter for this long (crash recovery after).
DISPATCH_GRACE_SECONDS = 300
# An unknown_outcome request may be explicitly retried only after this long with no run carrying
# its request ID and no claim recorded, so an accepted-but-slow dispatch cannot run twice.
UNKNOWN_RETRY_WINDOW_SECONDS = 3600


class JobError(Exception):
    """Domain error mapped to the contract Error {code, retryable, reason_code}."""

    STATUS = dict(invalid_request=422, forbidden=403, feature_disabled=403, not_found=404, owner_conflict=409,
                  idempotency_conflict=409, unsafe_target=422, stale_evidence=422, dependency_unavailable=503,
                  rate_limited=429, unknown_outcome=409, internal_error=500, configuration_error=503)

    def __init__(self, code, reason_code, *, retryable=False, retry_after=None):
        super().__init__(reason_code)
        self.code, self.reason_code, self.retryable, self.retry_after = code, reason_code, retryable, retry_after

    @property
    def status(self):
        return self.STATUS.get(self.code, 500)

    def error(self):
        return dict(code=self.code, retryable=self.retryable, reason_code=self.reason_code)


class DispatchRejected(Exception):
    def __init__(self, reason_code, retry_after=None):
        super().__init__(reason_code)
        self.reason_code, self.retry_after = reason_code, retry_after


class DispatchUnknown(Exception):
    """The dispatch request may have been accepted (timeout, reset, 5xx)."""


class ArtifactError(Exception):
    pass


def _ident(prefix):
    return prefix + '_' + uuid.uuid4().hex


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _iso(ts):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))


# ------------------------------------------------------------------ private runtime config

def load_dispatch_runtime(raw):
    """Validate the private ``dispatch`` section of runners-runtime.json."""
    if not isinstance(raw, dict):
        raise JobError('configuration_error', 'dispatch_runtime_invalid')
    try:
        socket_path = raw['claims_socket']
        health_url = raw['claims_health_url']
        if type(socket_path) is not str or not socket_path.startswith('/'):
            raise ValueError
        if type(health_url) is not str or not re.fullmatch(r'http://[0-9.]{7,15}:[0-9]{2,5}/v1/health', health_url):
            raise ValueError
        max_age = raw.get('reachability_max_age_seconds', 7 * 86400)
        per_hour = (raw.get('rate') or {}).get('per_principal_per_hour', 6)
        if type(max_age) is not int or not 60 <= max_age <= 90 * 86400 or type(per_hour) is not int or not 1 <= per_hour <= 1000:
            raise ValueError
        workflows = {}
        for wid, w in raw['workflows'].items():
            entry = dict(
                repository=w['repository'], workflow_path=w['workflow_path'], ref=w['ref'],
                credential_ref=w['credential_ref'], runner_labels=list(w['runner_labels']),
                offline_policy=w.get('offline_policy', 'reject'), canary=w.get('canary', False) is True,
                artifact_name_prefix=w['artifact_name_prefix'], workflow_ref=w['workflow_ref'],
                trusted_refs=dict(w['trusted_refs']), inputs=dict(w.get('inputs', {})))
            if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/(?!\.\.?$)[A-Za-z0-9_.-]{1,100}', entry['repository'])
                    or not re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]{1,100}\.ya?ml', entry['workflow_path'])
                    or not re.fullmatch(r'refs/heads/[A-Za-z0-9_./-]{1,200}', entry['ref'])
                    or entry['offline_policy'] not in ('reject', 'queue')
                    or not re.fullmatch(r'[a-z0-9-]{1,40}', entry['artifact_name_prefix'])
                    or not entry['runner_labels'] or entry['ref'] not in entry['trusted_refs'].values()
                    or any(not re.fullmatch(r'refs/heads/[A-Za-z0-9_./-]{1,200}', r) for r in entry['trusted_refs'].values())
                    or any(not INPUT_NAME.fullmatch(n) or n in RESERVED_INPUTS for n in entry['inputs'].values())):
                raise ValueError
            workflows[wid] = entry
        return dict(claims_socket=socket_path, claims_health_url=health_url, reachability_max_age_seconds=max_age,
                    per_principal_per_hour=per_hour, workflows=workflows)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise JobError('configuration_error', 'dispatch_runtime_invalid') from None


# ------------------------------------------------------------------ artifact safety

def safe_extract(blob, destination, *, limits=ARTIFACT_LIMITS):
    """Extract a zip into a private directory, refusing unsafe members.

    Rejects absolute paths, ``..``, backslashes, drive letters, symlinks/special
    files, duplicate names, file/directory conflicts, too many files, oversized
    members, inflated totals and entries whose declared size differs from the
    inflated byte count. Extraction happens in a fresh temporary sibling that is
    atomically renamed into place, so a crash or refusal never leaves a partial
    destination; a previous (job-owned) destination is replaced after a full pass.
    Every filesystem error is reported as ArtifactError.
    """
    import shutil
    import tempfile
    if len(blob) > limits['zip_bytes']:
        raise ArtifactError('artifact_too_large')
    destination = Path(destination)
    try:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.extract-', dir=destination.parent))
    except OSError:
        raise ArtifactError('artifact_extract_failed') from None
    try:
        written = _extract_into(blob, staging, limits)
        if destination.exists():
            shutil.rmtree(destination)
        os.rename(staging, destination)
        return written
    except OSError:
        raise ArtifactError('artifact_extract_failed') from None
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _extract_into(blob, destination, limits):
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise ArtifactError('artifact_not_zip') from None
    infos = archive.infolist()
    if len(infos) > limits['files']:
        raise ArtifactError('artifact_too_many_files')
    names, total, written = set(), 0, {}
    for info in infos:
        name = info.filename
        if (not name or name.startswith(('/', '\\')) or '\\' in name or '\x00' in name or ':' in name
                or any(part in ('', '.', '..') for part in name.rstrip('/').split('/'))):
            raise ArtifactError('artifact_unsafe_path')
        mode = (info.external_attr >> 16) & 0o170000
        if mode and mode not in (stat.S_IFREG, stat.S_IFDIR):
            raise ArtifactError('artifact_unsafe_type')
        if name in names or name.rstrip('/') in {n.rstrip('/') for n in names}:
            raise ArtifactError('artifact_duplicate_name')
        names.add(name)
        if info.is_dir():
            continue
        if info.file_size > limits['file_bytes']:
            raise ArtifactError('artifact_member_too_large')
        total += info.file_size
        if total > limits['total_bytes']:
            raise ArtifactError('artifact_too_large')
    files = {n for n in names if not n.endswith('/')}
    for n in names:
        parts = n.rstrip('/').split('/')
        if any('/'.join(parts[:i]) in files for i in range(1, len(parts))):
            raise ArtifactError('artifact_path_conflict')
    root = destination.resolve()
    for info in infos:
        target = (destination / info.filename).resolve()
        if root != target and root not in target.parents:
            raise ArtifactError('artifact_unsafe_path')
        if info.is_dir():
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            continue
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        try:
            src = archive.open(info)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError):
            raise ArtifactError('artifact_corrupt') from None
        with src:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as out:
                while True:
                    try:
                        chunk = src.read(65536)
                    except (zipfile.BadZipFile, EOFError, OSError, zlib.error):
                        raise ArtifactError('artifact_corrupt') from None
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > info.file_size or size > limits['file_bytes']:
                        raise ArtifactError('artifact_size_mismatch')
                    digest.update(chunk)
                    out.write(chunk)
        if size != info.file_size:
            raise ArtifactError('artifact_size_mismatch')
        written[info.filename] = dict(sha256=digest.hexdigest(), size=size)
    return written


def verify_provenance(files, root, job):
    """The manifest must bind this request, source SHA, run and attempt, and hash every file."""
    if 'provenance.json' not in files:
        raise ArtifactError('provenance_missing')
    try:
        manifest = json.loads((Path(root) / 'provenance.json').read_text(encoding='utf-8'))
    except (ValueError, UnicodeDecodeError, OSError):
        raise ArtifactError('provenance_invalid') from None
    if not isinstance(manifest, dict):
        raise ArtifactError('provenance_invalid')
    expected = dict(request_id=job['request_id'], source_sha=job['source_sha'],
                    run_id=job['provider_run_id'], run_attempt=str(job['attempt']))
    for key, value in expected.items():
        if str(manifest.get(key)) != str(value):
            raise ArtifactError('provenance_mismatch')
    if manifest.get('workflow_sha') not in (None, job['workflow_ref_sha']):
        raise ArtifactError('provenance_mismatch')
    listed = manifest.get('files')
    if not isinstance(listed, dict) or not listed:
        raise ArtifactError('provenance_invalid')
    if set(listed) != set(files) - {'provenance.json'}:
        raise ArtifactError('artifact_unlisted_or_missing_file')
    for name, digest in listed.items():
        if name not in files or files[name]['sha256'] != digest:
            raise ArtifactError('artifact_checksum_mismatch')
    exit_status = manifest.get('exit_status')
    if type(exit_status) is not int:
        raise ArtifactError('provenance_invalid')
    return dict(exit_status=exit_status, runner_name=str(manifest.get('runner_name', ''))[:120],
                toolchain=str(manifest.get('toolchain', ''))[:400], files=sorted(listed))


# ------------------------------------------------------------------ claim broker client

class ClaimsClient:
    """Owner side of the claim broker: Unix socket (SO_PEERCRED) + HTTP health."""

    def __init__(self, socket_path, health_url, *, timeout=5):
        self.socket_path, self.health_url, self.timeout = socket_path, health_url, timeout

    def _call(self, payload):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(self.timeout)
            s.connect(self.socket_path)
            s.sendall(json.dumps(dict(payload, api='claims_owner')).encode())
            s.shutdown(socket.SHUT_WR)
            data = b''
            while chunk := s.recv(65536):
                data += chunk
                if len(data) > 1024 * 1024:
                    raise OSError('response_too_large')
        result = json.loads(data)
        if not isinstance(result, dict):
            raise OSError('invalid_response')
        return result

    def health(self):
        import urllib.request
        with urllib.request.urlopen(self.health_url, timeout=self.timeout) as response:  # noqa: S310 - validated URL
            body = json.loads(response.read(4096))
        owner = self._call(dict(op='health'))
        return bool(body.get('ok') and owner.get('ok') and body.get('audience') == owner.get('audience'))

    def register(self, **intent):
        return self._call(dict(intent, op='register'))

    def status(self, request_id):
        return self._call(dict(op='status', request_id=request_id))

    def reachability(self):
        return self._call(dict(op='reachability'))


# ------------------------------------------------------------------ domain

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs_domain_requests (
  id TEXT PRIMARY KEY, idempotency TEXT NOT NULL UNIQUE, digest TEXT NOT NULL, principal TEXT NOT NULL,
  state TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS jobs_domain_principal ON jobs_domain_requests(principal, created_at);
CREATE TABLE IF NOT EXISTS jobs_domain_dispatch_calls (
  request_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, at REAL NOT NULL);
'''


class JobDomain:
    def __init__(self, *, db_path, config, runtime, github, claims, inventory, artifact_root,
                 clock=time.time, meta=None):
        self.db_path, self.config, self.runtime = str(db_path), config, runtime
        self.github, self.claims, self.inventory = github, claims, inventory
        self.artifact_root, self.clock = Path(artifact_root), clock
        self.meta = meta or (lambda: dict(contract_version=1, request_id=_ident('request'), owner=self.owner(),
                                          generated_at=_iso(self.clock()), observed_at=None, expires_at=None,
                                          freshness='unknown', provenance=[], coverage=[], source_errors=[]))
        self._lock = threading.RLock()
        self._reconciling = set()
        with self._db() as db:
            db.executescript(_SCHEMA)

    # ---- plumbing
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.execute('PRAGMA journal_mode=WAL')
        return _Closing(db)

    def owner(self):
        c = self.config
        return dict(gateway_id=c['owner_gateway_id'], profile=c['owner_profile'], owner_epoch=c['owner_epoch'],
                    config_revision=c['config_revision'])

    def _workflow(self, workflow_id):
        for w in self.config['allowed_workflows']:
            if w['id'] == workflow_id:
                private = self.runtime['workflows'].get(workflow_id)
                if private is None or private['workflow_ref'] != w['workflow_ref']:
                    raise JobError('configuration_error', 'workflow_unresolved')
                if not set(w['trusted_ref_ids']) <= set(private['trusted_refs']):
                    raise JobError('configuration_error', 'workflow_unresolved')
                return w, private
        raise JobError('not_found', 'workflow_not_allowlisted')

    def _scope(self, scope_id):
        for scope in self.config['scopes']:
            if scope['id'] == scope_id:
                return scope
        raise JobError('configuration_error', 'scope_unresolved')

    def _provider_id(self):
        for provider in self.config['providers']:
            if provider['kind'] == 'github_actions':
                return provider['id']
        raise JobError('configuration_error', 'provider_unresolved')

    def _load(self, job_id):
        with self._db() as db:
            row = db.execute('SELECT payload, state, updated_at FROM jobs_domain_requests WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise JobError('not_found', 'job_not_found')
        record = json.loads(row[0])
        record['state'] = row[1]
        record['_updated_at'] = row[2]
        return record

    def _save(self, record, *, expect_state=None):
        record.pop('_updated_at', None)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if expect_state is not None:
                    current = db.execute('SELECT state FROM jobs_domain_requests WHERE id=?', (record['job']['id'],)).fetchone()
                    if current is None or current[0] != expect_state:
                        raise JobError('owner_conflict', 'job_state_changed')
                db.execute('UPDATE jobs_domain_requests SET payload=?, state=?, updated_at=? WHERE id=?',
                           (json.dumps(record), record['state'], self.clock(), record['job']['id']))
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise

    # ---- workflows
    def workflows(self):
        out = []
        for w in self.config['allowed_workflows']:
            if w['id'] in self.runtime['workflows']:
                out.append(validate('Workflow', dict(w, dispatch_enabled=bool(
                    w['dispatch_enabled'] and self.config['features']['dispatch']))))
        return dict(meta=self.meta(), data=out)

    # ---- validation
    def _inputs(self, workflow, private, supplied):
        definitions = {d['input_id']: d for d in workflow['input_definitions']}
        ids = [i['input_id'] for i in supplied]
        if len(ids) != len(set(ids)):
            raise JobError('invalid_request', 'duplicate_input')
        values = {}
        for item in supplied:
            d = definitions.get(item['input_id'])
            if d is None or item['input_id'] not in private['inputs']:
                raise JobError('invalid_request', 'unknown_input')
            value = item['value']
            kind = {'string': str, 'boolean': bool, 'number': (int, float)}[d['value_type']]
            if type(value) is bool and d['value_type'] == 'number' or not isinstance(value, kind):
                raise JobError('invalid_request', 'input_type_mismatch')
            if d['allowed_values'] and value not in d['allowed_values']:
                raise JobError('invalid_request', 'input_not_allowed')
            if isinstance(value, str) and not SAFE_STRING.fullmatch(value):
                raise JobError('invalid_request', 'input_unsafe')
            values[private['inputs'][item['input_id']]] = value if isinstance(value, str) else json.dumps(value)
        for d in workflow['input_definitions']:
            if d['required'] and d['input_id'] not in ids:
                raise JobError('invalid_request', 'input_required')
        return values

    def _eligible(self, workflow, private):
        snapshot = self.inventory()
        if snapshot is None or snapshot.get('freshness') != 'fresh':
            raise JobError('stale_evidence', 'runner_inventory_stale', retryable=True)
        labels = {label.lower() for label in private['runner_labels']}
        online = []
        for runner in snapshot.get('runners', ()):
            if runner.get('scope_id') != workflow['scope_id']:
                continue
            if not labels <= {label.lower() for label in runner.get('labels', ())}:
                continue
            if runner.get('registration') == 'online' and runner.get('freshness') == 'fresh':
                online.append(runner['id'])
        return online

    def _rate(self, principal):
        window = self.clock() - 3600
        with self._db() as db:
            rows = db.execute('SELECT created_at FROM jobs_domain_requests WHERE principal=? AND created_at>? '
                              "AND state NOT IN ('rejected_pre_dispatch') ORDER BY created_at",
                              (principal, window)).fetchall()
        if len(rows) >= self.runtime['per_principal_per_hour']:
            raise JobError('rate_limited', 'dispatch_rate_limited', retryable=True,
                           retry_after=max(1, int(rows[0][0] + 3600 - self.clock())))

    # ---- submit
    def submit(self, request, *, principal, canary=False):
        request = validate('JobRequest', request)
        if type(principal) is not str or not principal:
            raise JobError('forbidden', 'authenticated_principal_required')
        if request['expected_owner'] != self.owner():
            raise JobError('owner_conflict', 'owner_conflict')
        workflow, private = self._workflow(request['workflow_id'])
        if canary:
            if not private['canary']:
                raise JobError('forbidden', 'canary_mode_requires_canary_workflow')
        elif not self.config['features']['dispatch']:
            raise JobError('feature_disabled', 'dispatch_disabled')
        if not workflow['dispatch_enabled'] and not (canary and private['canary']):
            raise JobError('feature_disabled', 'workflow_dispatch_disabled')
        inputs = self._inputs(workflow, private, request['inputs'])
        canonical = dict(workflow_id=request['workflow_id'], source_sha=request['source_sha'],
                         inputs=sorted([i['input_id'], i['value']] for i in request['inputs']),
                         owner=request['expected_owner'], canary=bool(canary))
        digest = _digest(canonical)
        idem = _digest(dict(owner=self.owner()['gateway_id'], profile=self.owner()['profile'], principal=principal,
                            operation='jobs.submit', key=request['idempotency_key']))
        with self._db() as db:
            row = db.execute('SELECT id, digest FROM jobs_domain_requests WHERE idempotency=?', (idem,)).fetchone()
        if row is not None:
            if row[1] != digest:
                raise JobError('idempotency_conflict', 'idempotency_key_reused')
            return self._replay(row[0])
        self._rate(principal)
        # Claim authority must be verifiable before anything is persisted or dispatched.
        try:
            healthy = self.claims.health()
        except (OSError, ValueError):
            healthy = False
        if not healthy:
            raise JobError('dependency_unavailable', 'claim_broker_unreachable', retryable=True)
        if not canary:
            try:
                proof = self.claims.reachability()
            except (OSError, ValueError):
                proof = None
            proved = [w for w in (proof or {}).get('workflows', ())
                      if w.get('repository') == private['repository'] and w.get('workflow_path') == private['workflow_path']
                      and isinstance(w.get('last_claim_at'), (int, float))
                      and self.clock() - w['last_claim_at'] <= self.runtime['reachability_max_age_seconds']]
            if not proved:
                raise JobError('dependency_unavailable', 'runner_claim_reachability_unverified')
        online = self._eligible(workflow, private)
        queued_reason = None
        if not online:
            if private['offline_policy'] == 'reject':
                raise JobError('dependency_unavailable', 'no_eligible_runner_online', retryable=True)
            queued_reason = 'no_eligible_runner_online'
        repository = self._scope(workflow['scope_id'])['name']
        if repository != private['repository']:
            raise JobError('configuration_error', 'workflow_scope_mismatch')
        try:
            trusted = self.github.source_reachable(private['credential_ref'], repository, private['ref'], request['source_sha'])
        except DispatchUnknown:
            raise JobError('dependency_unavailable', 'source_verification_unavailable', retryable=True) from None
        if trusted != 'reachable':
            raise JobError('unsafe_target', trusted if trusted in ('ref_not_protected', 'source_not_reachable_from_trusted_ref') else 'source_not_trusted')
        now = self.clock()
        request_id = 'request_' + uuid.uuid4().hex
        job = dict(id=_ident('job'), owner=self.owner(), provider_id=self._provider_id(), request_id=request_id,
                   target_id='target_' + hashlib.sha256(workflow['id'].encode()).hexdigest()[:32],
                   workflow_id=workflow['id'], provider_run_id=None, provider_job_id=None, attempt=1,
                   workflow_ref_sha=None, source_sha=request['source_sha'], source_digest=None,
                   assigned_runner_id=None, status='queued', conclusion=None, observation_ids=[], artifacts=[])
        record = dict(state='intent', job=job, canary=bool(canary), queued_reason=queued_reason,
                      repository=repository, workflow_path=private['workflow_path'], ref=private['ref'],
                      inputs=inputs, request_inputs=[[i['input_id'], i['value']] for i in request['inputs']],
                      eligible_runner_ids=online, retry_of=None, history=[dict(at=now, state='intent')],
                      duplicate_run_ids=[], artifact_details=[], claim=None, dispatch_error=None)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if db.execute('SELECT 1 FROM jobs_domain_requests WHERE idempotency=?', (idem,)).fetchone():
                    db.execute('ROLLBACK')
                    return self.submit(request, principal=principal, canary=canary)
                db.execute('INSERT INTO jobs_domain_requests VALUES (?,?,?,?,?,?,?,?)',
                           (job['id'], idem, digest, principal, 'intent', json.dumps(record), now, now))
                db.execute('COMMIT')
            except sqlite3.IntegrityError:
                db.execute('ROLLBACK')
                return self.submit(request, principal=principal, canary=canary)
        try:
            registered = self.claims.register(request_id=request_id, repository=repository,
                                              workflow_path=private['workflow_path'], ref=private['ref'],
                                              source_sha=request['source_sha'])
        except (OSError, ValueError):
            registered = None
        if not registered or not registered.get('ok'):
            self._transition(record, 'rejected_pre_dispatch', dispatch_error='claim_intent_unregistered')
            raise JobError('dependency_unavailable', 'claim_intent_unregistered', retryable=True)
        return self._dispatch(record)

    def _transition(self, record, state, **fields):
        record.update(fields)
        record['history'].append(dict(at=self.clock(), state=state))
        previous, record['state'] = record['state'], state
        self._save(record, expect_state=previous)
        return record

    def _dispatch(self, record):
        job = record['job']
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if db.execute('SELECT 1 FROM jobs_domain_dispatch_calls WHERE request_id=?', (job['request_id'],)).fetchone():
                    db.execute('ROLLBACK')
                    raise JobError('unknown_outcome', 'dispatch_already_attempted')
                db.execute('INSERT INTO jobs_domain_dispatch_calls VALUES (?,?,?)', (job['request_id'], job['id'], self.clock()))
                db.execute('COMMIT')
            except sqlite3.Error:
                db.execute('ROLLBACK')
                raise
        self._transition(record, 'dispatching')
        inputs = dict(record['inputs'], request_id=job['request_id'], source_sha=job['source_sha'])
        private = self.runtime['workflows'][job['workflow_id']]
        try:
            result = self.github.dispatch(private['credential_ref'], record['repository'], record['workflow_path'],
                                          record['ref'], inputs)
        except DispatchRejected as exc:
            self._transition(record, 'rejected', dispatch_error=exc.reason_code)
            raise JobError('rate_limited' if exc.reason_code == 'provider_rate_limited' else 'dependency_unavailable',
                           exc.reason_code, retryable=exc.reason_code == 'provider_rate_limited',
                           retry_after=exc.retry_after) from None
        except (DispatchUnknown, OSError, TimeoutError):
            job['status'] = 'unknown_outcome'
            self._transition(record, 'unknown_outcome', dispatch_error='dispatch_outcome_unknown')
            return self._response(record)
        run_id = (result or {}).get('run_id')
        if run_id is not None:
            job['provider_run_id'] = str(run_id)
        self._transition(record, 'dispatched')
        return self._response(record)

    def _replay(self, job_id):
        record = self._load(job_id)
        if record['state'] in ('rejected', 'rejected_pre_dispatch'):
            raise JobError('dependency_unavailable', record.get('dispatch_error') or 'dispatch_rejected')
        return self._response(record)

    def _response(self, record):
        meta = self.meta()
        if record.get('queued_reason') and record['job']['status'] == 'queued':
            meta['source_errors'] = list(meta.get('source_errors', [])) + [dict(
                source_id=record['job']['workflow_id'], code=record['queued_reason'], retry_after_seconds=None,
                observed_at=_iso(self.clock()))]
        return dict(meta=meta, data=validate('Job', record['job']))

    # ---- lookup / reconcile
    def get(self, job_id, *, background=None):
        validate('JobLookupRequest', dict(job_id=job_id))
        record = self._load(job_id)
        if record['state'] in ('rejected', 'rejected_pre_dispatch'):
            raise JobError('not_found', 'job_not_found')
        settled = record['job']['status'] in ('concluded', 'cancelled') and record.get('artifact_details') is not None \
            and record['state'] == 'tracked' and (record['job']['artifacts'] or record.get('artifacts_checked'))
        if background is not None and not settled:
            self._kick(job_id, background)
        return self._response(record)

    def _kick(self, job_id, starter):
        with self._lock:
            if job_id in self._reconciling:
                return
            self._reconciling.add(job_id)

        def run():
            try:
                self.reconcile(job_id)
            except Exception:
                pass
            finally:
                with self._lock:
                    self._reconciling.discard(job_id)
        starter(run)

    def _bind_run(self, record):
        job, private = record['job'], self.runtime['workflows'][record['job']['workflow_id']]
        if job['provider_run_id'] is not None:
            return
        runs = self.github.find_runs(private['credential_ref'], record['repository'], record['workflow_path'],
                                     job['request_id'])
        matches = [r for r in runs if ('[%s]' % job['request_id']) in str(r.get('display_title', ''))]
        if not matches:
            return
        chosen = matches[0]
        if len(matches) > 1:
            claim = (self.claims.status(job['request_id']) or {}).get('claim')
            holders = [r for r in matches if claim and str(r['id']) == str(claim['run_id'])]
            if len(holders) != 1:
                return  # ambiguous: stay unknown until the broker names the winner
            chosen = holders[0]
            record['duplicate_run_ids'] = sorted(str(r['id']) for r in matches if r is not chosen)
        job['provider_run_id'] = str(chosen['id'])

    def reconcile(self, job_id):
        record = self._load(job_id)
        loaded_at = record.pop('_updated_at', None)
        if record['state'] in ('rejected', 'rejected_pre_dispatch', 'intent'):
            return self._response(record) if record['state'] != 'rejected' else None
        if record['state'] == 'dispatching' and self.clock() - (loaded_at or 0) < DISPATCH_GRACE_SECONDS:
            return self._response(record)  # the submitter still owns this transition
        job, private = record['job'], self.runtime['workflows'][record['job']['workflow_id']]
        before = json.dumps(record, sort_keys=True)
        self._bind_run(record)
        if job['provider_run_id'] is not None:
            cred, repo = private['credential_ref'], record['repository']
            run = self.github.run(cred, repo, job['provider_run_id'])
            if ('[%s]' % job['request_id']) not in str(run.get('display_title', '')):
                raise JobError('owner_conflict', 'run_request_mismatch')
            latest = int(run['run_attempt'])
            claim = (self.claims.status(job['request_id']) or {}).get('claim')
            record['claim'] = claim
            if claim and str(claim['run_id']) != job['provider_run_id']:
                record['claim_run_mismatch'] = str(claim['run_id'])
                claim = None
            # Track the attempt that holds the claim, never "latest". Later attempts are refused
            # by the broker and are reported separately.
            if claim:
                attempt = int(claim['run_attempt'])
                record['refused_attempts'] = list(range(attempt + 1, latest + 1))
                if attempt != latest:
                    run = self.github.run_attempt(cred, repo, job['provider_run_id'], attempt)
            else:
                attempt = latest
            job['attempt'] = attempt
            job['workflow_ref_sha'] = run['head_sha'] if SHA.fullmatch(str(run.get('head_sha', ''))) else None
            status = run.get('status')
            if status == 'completed':
                job['status'] = 'concluded'
                conclusion = run.get('conclusion')
                job['conclusion'] = conclusion if conclusion in ('success', 'failure', 'cancelled', 'timed_out') else 'unknown'
            elif status in ('in_progress',):
                job['status'], job['conclusion'] = 'running', None
            else:
                job['status'], job['conclusion'] = 'queued', None
            jobs = self.github.run_jobs(cred, repo, job['provider_run_id'], attempt)
            if jobs:
                gh_job = jobs[0]
                job['provider_job_id'] = str(gh_job['id'])
                if gh_job.get('runner_id'):
                    job['assigned_runner_id'] = provider_runner_key('github_actions', 'https://api.github.com',
                                                                    self._workflow(job['workflow_id'])[0]['scope_id'],
                                                                    str(gh_job['runner_id']))
                record['runner_name'] = str(gh_job.get('runner_name') or '')[:120]
            if job['status'] == 'concluded' and claim and not record.get('artifacts_checked'):
                self._collect_artifacts(record)
            elif job['status'] == 'concluded' and not claim and not record.get('artifacts_checked'):
                # No attempt ever held the claim, so nothing was built: there is no artifact to trust.
                record['artifact_details'] = [dict(state='none', reason='no_execution_claim')]
                record['artifacts_checked'] = True
            if record['state'] in ('unknown_outcome', 'dispatched', 'dispatching'):
                record['state'] = 'tracked'
                record['history'].append(dict(at=self.clock(), state='tracked'))
        if json.dumps(record, sort_keys=True) != before:
            with self._db() as db:
                # Optimistic: discard this pass if anyone else wrote the record meanwhile.
                db.execute('UPDATE jobs_domain_requests SET payload=?, state=?, updated_at=? WHERE id=? AND updated_at=?',
                           (json.dumps(record), record['state'], self.clock(), job['id'], loaded_at))
        return self._response(record)

    def _collect_artifacts(self, record):
        job, private = record['job'], self.runtime['workflows'][record['job']['workflow_id']]
        expected = '%s%s' % (private['artifact_name_prefix'], job['request_id'])
        listed = self.github.artifacts(private['credential_ref'], record['repository'], job['provider_run_id'])
        details, artifacts, transient = [], [], False
        for item in listed:
            if item.get('name') != expected:
                continue
            artifact_id = 'artifact_' + hashlib.sha256(('%s:%s' % (job['id'], item['id'])).encode()).hexdigest()[:32]
            base = dict(id=artifact_id, job_id=job['id'], size_bytes=int(item.get('size_in_bytes') or 0),
                        source_sha=None, source_digest=None, provider_run_id=job['provider_run_id'],
                        attempt=job['attempt'], toolchain_observation_ids=[])
            provider_digest = item.get('digest')
            placeholder = provider_digest if _is_digest(provider_digest) else 'sha256:' + '0' * 64
            if item.get('expired'):
                artifacts.append(dict(base, digest=placeholder, state='expired'))
                details.append(dict(id=artifact_id, state='expired', reason='artifact_expired_not_rebuilt'))
                continue
            try:
                blob = self.github.download_artifact(private['credential_ref'], record['repository'], item['id'],
                                                     ARTIFACT_LIMITS['zip_bytes'])
            except ArtifactError as exc:
                if str(exc) == 'artifact_expired':
                    artifacts.append(dict(base, digest=placeholder, state='expired'))
                    details.append(dict(id=artifact_id, state='expired', reason='artifact_expired_not_rebuilt'))
                    continue
                transient = True  # download failures are retried by a later reconcile
                details.append(dict(id=artifact_id, state='pending', reason=str(exc)))
                continue
            try:
                digest = 'sha256:' + hashlib.sha256(blob).hexdigest()
                if not _is_digest(provider_digest) or provider_digest != digest:
                    raise ArtifactError('artifact_digest_mismatch')
                root = self.artifact_root / job['id'] / artifact_id
                files = safe_extract(blob, root)
                provenance = verify_provenance(files, root, job)
                artifacts.append(dict(base, digest=digest, state='available', source_sha=job['source_sha'],
                                      size_bytes=len(blob)))
                details.append(dict(id=artifact_id, state='verified', path=str(root), **provenance))
            except ArtifactError as exc:
                artifacts.append(dict(base, digest=placeholder, state='unknown'))
                details.append(dict(id=artifact_id, state='rejected', reason=str(exc)))
        record['artifact_details'] = details
        if not transient:
            job['artifacts'] = artifacts
            record['artifacts_checked'] = True

    def details(self, job_id):
        """Owner-side evidence view (not a contract response)."""
        record = self._load(job_id)
        return dict(state=record['state'], job=record['job'], claim=record.get('claim'),
                    runner_name=record.get('runner_name'), artifact_details=record.get('artifact_details'),
                    duplicate_run_ids=record.get('duplicate_run_ids'), history=record['history'],
                    dispatch_error=record.get('dispatch_error'), queued_reason=record.get('queued_reason'))

    def retry(self, job_id, *, principal, idempotency_key, canary=False):
        """Explicit retry: a NEW authorized request; the old claim is never reused."""
        record = self._load(job_id)
        if record['job']['status'] not in ('concluded', 'unknown_outcome', 'cancelled'):
            raise JobError('owner_conflict', 'job_still_active')
        if record['job']['status'] == 'unknown_outcome':
            self.reconcile(job_id)
            record = self._load(job_id)
            with self._db() as db:
                row = db.execute('SELECT at FROM jobs_domain_dispatch_calls WHERE request_id=?',
                                 (record['job']['request_id'],)).fetchone()
            claim = (self.claims.status(record['job']['request_id']) or {}).get('claim')
            if (record['job']['provider_run_id'] is not None or claim is not None or row is None
                    or self.clock() - row[0] < UNKNOWN_RETRY_WINDOW_SECONDS):
                raise JobError('unknown_outcome', 'outcome_still_unknown_reconcile_first', retryable=True)
        workflow, _private = self._workflow(record['job']['workflow_id'])
        inputs = [dict(input_id=k, value=v) for k, v in record.get('request_inputs', [])]
        response = self.submit(dict(workflow_id=workflow['id'], source_sha=record['job']['source_sha'], inputs=inputs,
                                    idempotency_key=idempotency_key, expected_owner=self.owner()),
                               principal=principal, canary=canary)
        new = self._load(response['data']['id'])
        if new['job']['id'] != job_id and new.get('retry_of') is None:
            new['retry_of'] = job_id
            self._save(new)
        return response


def _is_digest(value):
    return isinstance(value, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', value) is not None


class _Closing:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *exc):
        self.db.close()


# ------------------------------------------------------------------ production GitHub port

class GitHubDispatchPort:
    """Direct HTTPS to api.github.com with a broker-vended token; no proxies.

    Artifact downloads follow exactly one redirect to an allowlisted HTTPS storage
    host, without the GitHub Authorization header.
    """
    API = 'api.github.com'
    STORAGE_SUFFIXES = ('.actions.githubusercontent.com', '.blob.core.windows.net')

    def __init__(self, credential_resolver, *, timeout=20):
        self.resolve, self.timeout = credential_resolver, timeout

    def _request(self, ref, method, path, body=None, *, host=API, auth=True, max_bytes=4 * 1024 * 1024):
        import http.client
        import ssl
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'hermes-runners-jobs',
                   'X-GitHub-Api-Version': '2022-11-28'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.resolve(ref).token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        conn = http.client.HTTPSConnection(host, timeout=self.timeout, context=ssl.create_default_context())
        try:
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ArtifactError('response_too_large')
            return response.status, {k.lower(): v for k, v in response.getheaders()}, raw
        finally:
            conn.close()

    def _json(self, ref, path):
        try:
            status, _headers, raw = self._request(ref, 'GET', path)
            if status != 200:
                raise DispatchUnknown()
            return json.loads(raw)
        except (OSError, TimeoutError, ValueError, ArtifactError):
            raise DispatchUnknown() from None

    def source_reachable(self, ref, repository, trusted_ref, sha):
        branch = trusted_ref[len('refs/heads/'):]
        info = self._json(ref, '/repos/%s/branches/%s' % (repository, branch))
        if info.get('protected') is not True:
            return 'ref_not_protected'
        head = (info.get('commit') or {}).get('sha')
        if not isinstance(head, str) or not SHA.fullmatch(head) or not SHA.fullmatch(sha):
            raise DispatchUnknown()
        try:
            status, _h, raw = self._request(ref, 'GET', '/repos/%s/compare/%s...%s' % (repository, sha, head))
            if status == 404:
                return 'source_not_reachable_from_trusted_ref'
            if status != 200:
                raise DispatchUnknown()
            compare = json.loads(raw)
        except (OSError, TimeoutError, ValueError, ArtifactError):
            raise DispatchUnknown() from None
        if compare.get('status') in ('ahead', 'identical') and (compare.get('merge_base_commit') or {}).get('sha') == sha:
            return 'reachable'
        return 'source_not_reachable_from_trusted_ref'

    def dispatch(self, ref, repository, workflow_path, git_ref, inputs):
        name = workflow_path.rsplit('/', 1)[-1]
        try:
            self.resolve(ref)  # nothing has been sent yet: a credential failure is a definite rejection
        except Exception:
            raise DispatchRejected('credential_unavailable') from None
        try:
            status, headers, raw = self._request(ref, 'POST', '/repos/%s/actions/workflows/%s/dispatches' % (repository, name),
                                                 dict(ref=git_ref, inputs=inputs, return_run_details=True))
        except (OSError, TimeoutError):
            raise DispatchUnknown() from None
        if status in (200, 204):
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = {}
            return dict(run_id=body.get('workflow_run_id'))
        if status in (403, 429) and (headers.get('x-ratelimit-remaining') == '0' or 'retry-after' in headers):
            retry = headers.get('retry-after')
            raise DispatchRejected('provider_rate_limited', int(retry) if retry and retry.isdigit() else None)
        if 400 <= status < 500:
            raise DispatchRejected('provider_rejected_%d' % status)
        raise DispatchUnknown()

    def find_runs(self, ref, repository, workflow_path, request_id):
        name = workflow_path.rsplit('/', 1)[-1]
        body = self._json(ref, '/repos/%s/actions/workflows/%s/runs?event=workflow_dispatch&per_page=50' % (repository, name))
        return [dict(id=r['id'], display_title=r.get('display_title', '')) for r in body.get('workflow_runs', [])]

    def run(self, ref, repository, run_id):
        return self._json(ref, '/repos/%s/actions/runs/%s' % (repository, run_id))

    def run_attempt(self, ref, repository, run_id, attempt):
        return self._json(ref, '/repos/%s/actions/runs/%s/attempts/%d' % (repository, run_id, int(attempt)))

    def run_jobs(self, ref, repository, run_id, attempt):
        body = self._json(ref, '/repos/%s/actions/runs/%s/attempts/%s/jobs' % (repository, run_id, attempt))
        return body.get('jobs', [])

    def artifacts(self, ref, repository, run_id):
        body = self._json(ref, '/repos/%s/actions/runs/%s/artifacts?per_page=100' % (repository, run_id))
        return body.get('artifacts', [])

    def download_artifact(self, ref, repository, artifact_id, max_bytes):
        from urllib.parse import urlsplit
        try:
            status, headers, _raw = self._request(ref, 'GET', '/repos/%s/actions/artifacts/%s/zip' % (repository, artifact_id))
            if status == 410:
                raise ArtifactError('artifact_expired')
            if status != 302 or 'location' not in headers:
                raise ArtifactError('artifact_download_failed')
            target = urlsplit(headers['location'])
            if target.scheme != 'https' or not target.hostname or not target.hostname.endswith(self.STORAGE_SUFFIXES):
                raise ArtifactError('artifact_redirect_refused')
            path = target.path + ('?' + target.query if target.query else '')
            status, _headers, raw = self._request(ref, 'GET', path, host=target.hostname, auth=False, max_bytes=max_bytes)
        except (OSError, TimeoutError):
            raise ArtifactError('artifact_download_failed') from None
        if status != 200:
            raise ArtifactError('artifact_download_failed')
        return raw


# ------------------------------------------------------------------ production wiring

_DOMAINS = {}
_DOMAINS_LOCK = threading.Lock()


def runtime_path(home):
    return Path(home) / 'runners-runtime.json'


def dispatch_path(home):
    # Separate private file: the collector's runtime file has a closed key set.
    return Path(home) / 'runners-dispatch.json'


def domain_for(service, home):
    """One JobDomain per owner state DB/config; private runtime read from the owner home."""
    key = str(Path(service.path).resolve())
    raw = json.loads(runtime_path(home).read_text(encoding='utf-8'))
    try:
        dispatch_raw = json.loads(dispatch_path(home).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise JobError('feature_disabled', 'dispatch_not_configured') from None
    runtime = load_dispatch_runtime(dispatch_raw)
    signature = _digest(dict(config=service.config, runtime=runtime))
    with _DOMAINS_LOCK:
        cached = _DOMAINS.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        from .providers.runtime import BrokerCredential, BrokerCredentialResolver
        references = {ref: BrokerCredential(**spec) for ref, spec in raw.get('credentials', {}).items()}
        resolver = BrokerCredentialResolver(references, broker_command=tuple(raw.get('broker_executable') and [raw['broker_executable']] or ['token-broker']))

        def inventory():
            snapshot = service.inventory()
            runners = [r for r in snapshot.get('data', {}).get('runners', [])] if isinstance(snapshot.get('data'), dict) else []
            return dict(freshness=snapshot['meta']['freshness'], runners=runners)

        domain = JobDomain(db_path=service.path, config=service.config, runtime=runtime,
                           github=GitHubDispatchPort(resolver), claims=ClaimsClient(runtime['claims_socket'], runtime['claims_health_url']),
                           inventory=inventory, artifact_root=Path(home) / 'runners' / 'artifacts',
                           meta=lambda: deepcopy(service.inventory()['meta']))
        _DOMAINS[key] = (signature, domain)
        return domain
