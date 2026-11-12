"""Frozen root-cap guard: a queue notice is not permission to change a rollout-frozen config key.

Narrow `pre_tool_call` guard for ONE key (`kanban.max_in_progress`) in ONE registered root home, and only while a
verified, active, approved rollout contract freezes it. Nothing else in config is restricted, and no value is
hardcoded: the frozen value, home, contract revision and owner come from a registered contract record.

* Contract projection (`register_contract`): a real approved artifact (digest-pinned), a native human approval
  message (same reader as kanban-honest-done: typed row, human source, same chat lineage) and an open owner card.
  Caller JSON, assistant prose and queue notices cannot create or amend authority.
* A mutation that contradicts the freeze is refused unless the user's own native turn authorizes exactly that
  key/value against the frozen revision ("from 7 to 8", or the revision digest). A temporary exception also needs
  a validated open owner card, the original value as restore target and a restore trigger (owner terminal state
  and/or a future UTC expiry); its provenance is persisted and read back before the call is admitted.
* After the call, `after()` reads the real config back before marking an exception active/applied.

This is an accidental-mutation guard at the tool boundary, not an OS sandbox: dynamic shell, scripts run from
files and non-literal Python are out of scope except where they visibly name the key (then ambiguity is refused).
"""
import ast
import contextlib
import datetime
import fcntl
import glob
import hashlib
import importlib.util
import json
import os
import re
import shlex
import sqlite3
import tempfile
import time
from pathlib import Path, PurePosixPath

GUARDED_KEYS = ("kanban.max_in_progress",)
CONTRACTS_FILE = "root_cap_contracts.json"
EXCEPTIONS_FILE = "root_cap_exceptions.json"
OPEN_STATES = frozenset({"todo", "ready", "running", "blocked", "scheduled", "triage"})
TERMINAL_STATES = frozenset({"done", "archived"})
ALTERNATIVES = ("Allowed instead: raise the card's priority, re-place it on a gateway with capacity, "
                "or do the work locally.")


class RootCapError(Exception):
    pass


# ----------------------------------------------------------------------------- small helpers
def _leaf(key):
    return key.rsplit(".", 1)[-1]


def _real(path):
    return os.path.realpath(os.path.expanduser(str(path)))


def _sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else str(data).encode()).hexdigest()


def _intval(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        pass
    try:
        number = float(str(text).strip())
        return int(number) if number == int(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _env_home():
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def _root_of(home):
    p = Path(home)
    return p.parent.parent if p.parent.name == "profiles" else p


def _expand(word):
    """Literal path of a shell word, or None when it depends on an unknown variable/substitution."""
    if not isinstance(word, str):
        return None
    home = os.environ.get("HOME") or os.path.expanduser("~")
    word = word.replace("${HOME}", home).replace("$HOME", home)
    if "$" in word or "`" in word:
        return None
    return os.path.expanduser(word)


def _read_list(path):
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        raise RootCapError(f"{p.name} unreadable ({type(exc).__name__})")
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise RootCapError(f"{p.name} is not a record list")
    return data


def _write_list(path, data):
    """Unique temp file, fsync, atomic replace, then read back and compare: a record is not admitted until it
    reads back exactly. Read-modify-write callers hold `_locked` (see `_mutate`)."""
    p = Path(path)
    tmp = None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
        tmp = None
        if _read_list(p) != json.loads(json.dumps(data)):
            raise RootCapError(f"{p.name} read-back differs")
    except OSError as exc:
        raise RootCapError(f"{p.name} not writable ({type(exc).__name__})")
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


@contextlib.contextmanager
def _locked(path):
    """Exclusive advisory lock (threads and processes: one open file description per call) spanning a whole
    read/modify/write transition of the records in `path`'s directory."""
    directory = Path(path).parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(directory / ".root_cap.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise RootCapError(f"{directory.name} lock unavailable ({type(exc).__name__})")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _mutate(path, fn):
    """Locked read-modify-write: `fn(list)` returns (new_list | None, result). Every transition serializes here."""
    with _locked(path):
        data = _read_list(path)
        new, result = fn(data)
        if new is not None:
            _write_list(path, new)
        return result


def shared_dir(home):
    """The coach store of the ROOT home that owns `home` (a profile home resolves to its root): the one place every
    profile's guard consults, so a profile-to-root write meets the same projection as a root session."""
    return _root_of(Path(_real(home))) / "coach"


def _dirs(store_root, homes=()):
    out, seen = [], set()
    for d in [Path(store_root), *(shared_dir(h) for h in homes if h)]:
        key = _real(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


_CONTRACT_FIELDS = {"target_home": str, "key": str, "frozen_value": int, "contract_ref": str,
                    "contract_revision_digest": str, "approval_ref": dict, "owner_ref": dict, "active": bool}


def _validate_contract(rec):
    """Strict record schema: a malformed record is a known-but-unreadable contract, never a silent allow."""
    for field, typ in _CONTRACT_FIELDS.items():
        value = rec.get(field)
        if not isinstance(value, typ) or (typ is int and isinstance(value, bool)):
            raise RootCapError(f"contract record has an invalid {field}")
    if not isinstance(rec["owner_ref"].get("card"), str) or not rec["owner_ref"]["card"]:
        raise RootCapError("contract record has no owner card")
    return rec


def _load_contracts(dirs):
    """(records, known): `known` is True when any projection file exists, even an empty one. Raises RootCapError
    when a projection exists but cannot be read or validated (known-but-unreadable)."""
    out, known = [], False
    for d in dirs:
        path = Path(d) / CONTRACTS_FILE
        if not path.exists():
            continue
        known = True
        for rec in _read_list(path):
            out.append(_validate_contract(rec))
    return out, known


# ----------------------------------------------------------------------------- owner / config readers
def owner_status(card, target_home):
    """Kanban status of a card, None when no readable board has it. Raises when a board cannot be read
    and the card was not found elsewhere (ambiguity is refused, never read as 'open')."""
    paths = []
    pinned = os.environ.get("HERMES_KANBAN_DB")
    if pinned:
        paths.append(pinned)
    root = str(target_home)
    paths.append(os.path.join(root, "kanban.db"))
    paths.extend(sorted(glob.glob(os.path.join(root, "kanban", "boards", "*", "kanban.db"))))
    seen, error, existing = set(), None, 0
    for path in paths:
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        existing += 1
        try:
            conn = _acceptance()._ro_connect(Path(path))
            try:
                row = conn.execute("select status from tasks where id=?", (card,)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            error = exc
            continue
        if row:
            return str(row[0])
    if error is not None or not existing:
        raise RootCapError("kanban board unreadable" if error is not None else "no kanban board found")
    return None


def _simple_yaml_get(text, key):
    """Indentation-only reader for `a:\n  b: scalar` when PyYAML is unavailable (scalars only)."""
    path, indents = key.split("."), []
    depth = 0
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while indents and indent <= indents[-1]:
            indents.pop()
            depth -= 1
        name, _, rest = line.strip().partition(":")
        if depth < len(path) and name == path[depth] and (depth == len(indents)):
            if depth == len(path) - 1:
                value = rest.split(" #")[0].strip().strip("'\"")
                return _intval(value) if _intval(value) is not None else (value or None)
            indents.append(indent)
            depth += 1
        elif depth == len(indents):
            continue
    return None


def config_value(target_home, key):
    text = (Path(target_home) / "config.yaml").read_text()
    try:
        import yaml
    except ImportError:
        return _simple_yaml_get(text, key)
    data = yaml.safe_load(text) or {}
    for part in key.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _acceptance():
    path = Path(__file__).resolve().parent.parent / "kanban-honest-done" / "acceptance.py"
    spec = importlib.util.spec_from_file_location("_root_cap_acceptance", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------------------------- contract registration
def register_contract(store_root, *, target_home, key, frozen_value, contract_ref, approval_session,
                      approval_message_id, owner_card, state_db=None):
    """Project an approved rollout contract into the coach store. Raises RootCapError on any unproven part."""
    if key not in GUARDED_KEYS:
        raise RootCapError(f"unsupported key {key!r}; guarded: {', '.join(GUARDED_KEYS)}")
    value = _intval(frozen_value)
    if value is None:
        raise RootCapError("frozen value must be an integer")
    home = Path(_real(target_home))
    if not (home / "config.yaml").is_file():
        raise RootCapError(f"{home} is not a Hermes home (no config.yaml)")
    artifact = Path(contract_ref)
    digest = _artifact_digest(artifact, key, value)
    db = Path(state_db) if state_db else home / "state.db"
    _verify_native_approval(db, approval_session, approval_message_id, artifact, digest, key, value, owner_card)
    status = owner_status(owner_card, home)
    if status not in OPEN_STATES:
        raise RootCapError(f"owner card {owner_card} is not an open card (status {status!r})")
    rec = {"target_home": str(home), "key": key, "frozen_value": value, "contract_ref": str(artifact.resolve()),
           "contract_revision_digest": digest, "approval_ref": {"session": approval_session,
                                                                 "message_id": int(approval_message_id)},
           "owner_ref": {"card": owner_card}, "active": True, "registered_at": time.time()}
    path = shared_dir(home) / CONTRACTS_FILE

    def apply(data):
        out = []
        for old in data:
            same_target = (old.get("target_home"), old.get("key")) == (rec["target_home"], key)
            if same_target and old.get("active") and old.get("contract_revision_digest") != digest:
                old = {**old, "active": False, "superseded_by_revision": digest}
            if same_target and old.get("contract_revision_digest") == digest:
                continue
            out.append(old)
        out.append(rec)
        return out, rec

    return _mutate(path, apply)


def _artifact_digest(artifact, key, value):
    """Digest of the artifact bytes, which must state `key ... value` (a pinned revision, not a path)."""
    try:
        raw = Path(artifact).read_bytes()
    except OSError as exc:
        raise RootCapError(f"contract artifact unreadable ({type(exc).__name__})")
    if len(raw) > 1_000_000 or not re.search(rf"{re.escape(_leaf(key))}[^\n]{{0,80}}\b{value}\b",
                                             raw.decode("utf-8", "replace")):
        raise RootCapError(f"contract artifact does not state {key} = {value}")
    return _sha(raw)


def _verify_native_approval(db, session, message_id, artifact, digest, key, value, owner_card):
    """The approving row must be native human authority: a typed row of a human-opened chat, newer than the artifact
    revision, in the approval chat's lineage (same reader as kanban-honest-done), and its text a positive, first-person
    approval of THIS rollout (not reported or quoted speech, not negated, naming the artifact revision or owner)."""
    acc = _acceptance()
    try:
        after = Path(artifact).stat().st_mtime  # approval of THIS revision cannot predate the revision's bytes
    except OSError as exc:
        raise RootCapError(f"contract artifact unreadable ({type(exc).__name__})")
    ok, why = acc.verify_citation(int(message_id), session, db, after=after)
    if not ok:
        raise RootCapError(f"approval is not native human authority for this revision: {why}")
    human, why = human_origin(session, db)
    if not human:
        raise RootCapError(f"approval is not native human authority: {why}")
    conn = acc._ro_connect(db)
    try:
        content, kind = conn.execute("select content, display_kind from messages where id=?",
                                     (int(message_id),)).fetchone()
    finally:
        conn.close()
    _approval_binds(acc.typed_text(content, kind) or "", key, value, Path(artifact), digest, owner_card)


_QUOTED_SQ = re.compile(r"(?<![\w])['‘][^'’\n]{2,}['’](?![\w])")
_QUESTION = re.compile(r"\?|^\s*(did|do|does|have|has|had|is|are|was|were|if|what if|should|can|could|may|would|"
                       r"shall|will|why|how|when|whether)\b")


def _approval_binds(text, key, value, artifact, digest, owner_card):
    """The human's own message must be a positive, non-negated, first-person approval of THIS rollout: key, frozen
    value and the artifact revision (digest prefix, artifact file name or the owner card). Naming the key and a number
    is not approval ('Do not freeze X at 7. This is not approval.'); neither are questions ('Did I approve ...?'),
    reported speech, or text inside quotation marks of any kind."""
    low = text.lower().replace("`", "")
    leaf = _leaf(key)
    sentences = _sentences(low)
    named = [s for s in sentences if leaf in s and re.search(rf"\b{value}\b", s)]
    if not named:
        raise RootCapError(f"approval message does not name {key} and the frozen value {value}")
    if any(_NEG.search(s) or "not approv" in s for s in sentences):
        raise RootCapError("approval message is negative or withdraws the freeze")

    def bare(s):  # the sentence without any quoted span (double, curly or single quotation marks)
        return _QUOTED_SQ.sub(" ", _QUOTED_DQ.sub(" ", s))

    # A reported-speech cue anywhere in the sentence, a question, or the key/value/approval appearing only inside
    # quotation marks disqualifies it. Only a plain statement can be the user's own decision.
    own = [s for s in named if not _REPORTED.search(s) and not _QUESTION.search(s) and leaf in bare(s)
           and re.search(rf"\b{value}\b", bare(s))]
    if not own:
        raise RootCapError("approval message is a question, reported or quoted speech, not the user's own approval")
    named = own
    if not any(re.search(r"\b(approv\w*|authori[sz]\w*|freez\w*|frozen|confirm\w*)\b", bare(s)) for s in named):
        raise RootCapError("approval message does not approve the freeze")
    refs = (digest[:12], artifact.name.lower(), owner_card.lower())
    if not any(ref and ref in low for ref in refs):
        raise RootCapError("approval message does not name the artifact revision (digest prefix, file name) "
                           "or the owner card, so it is not tied to this contract")


def _contract_state(rec):
    """(state, why): active | lapsed | ambiguous. Unprovable means ambiguous, never active-and-unguarded."""
    if not rec.get("active"):
        return "lapsed", "inactive"
    if rec.get("_unverified"):
        return "ambiguous", rec["_unverified"]
    try:
        status = owner_status((rec.get("owner_ref") or {}).get("card", ""), rec.get("target_home", ""))
    except RootCapError as exc:
        return "ambiguous", f"rollout owner state unreadable ({exc})"
    if status is None:
        return "ambiguous", "rollout owner card not found"
    if status in TERMINAL_STATES:
        return "lapsed", "owner finished"
    try:
        digest = _sha(Path(rec["contract_ref"]).read_bytes())
    except (OSError, KeyError):
        return "ambiguous", "approved contract artifact unreadable"
    if digest != rec.get("contract_revision_digest"):
        return "ambiguous", "approved contract artifact changed since approval (revision digest mismatch)"
    return "active", ""


# ----------------------------------------------------------------------------- approved-source activation
# A rollout freeze is declared ONCE as a task-local descriptor of explicit references (artifact, approval authority,
# owner card, reviewed capacity->key mapping). The guard consults every descriptor of the target root at each
# targeted call and re-verifies its authority then, so there is no separate registration step to forget, a missing
# projection never erases a known protection, and later steering (withdrawn/superseded approval, finished owner)
# is re-read at admission. Nothing here scans prose: an unreferenced approval is not a contract.
SOURCES_FILE = "root_cap_sources.json"
_AUTH_TYPES = ("native_message", "action_operation")


def _validate_source(rec):
    """Strict descriptor schema; a malformed descriptor is a known-but-unreadable contract, never a silent allow."""
    for field in ("source_id", "target_home", "key", "contract_ref"):
        if not isinstance(rec.get(field), str) or not rec[field]:
            raise RootCapError(f"source descriptor has an invalid {field}")
    value = rec.get("frozen_value")
    if not isinstance(value, int) or isinstance(value, bool):
        raise RootCapError("source descriptor has an invalid frozen_value")
    if not isinstance(rec.get("owner_card"), str) or not rec["owner_card"]:
        raise RootCapError("source descriptor has no owner card")
    auth = rec.get("authority")
    if not isinstance(auth, dict) or auth.get("type") not in _AUTH_TYPES:
        raise RootCapError("source descriptor has no recognised authority reference")
    if auth["type"] == "native_message":
        if not isinstance(auth.get("session"), str) or not auth["session"] \
                or not isinstance(auth.get("message_id"), int) or isinstance(auth.get("message_id"), bool):
            raise RootCapError("native_message authority needs session and message_id")
    else:
        for field in ("db", "operation_id", "request_id"):
            if not isinstance(auth.get(field), str) or not auth[field]:
                raise RootCapError(f"action_operation authority needs {field}")
        if not isinstance(auth.get("revision"), int) or isinstance(auth.get("revision"), bool):
            raise RootCapError("action_operation authority needs an integer revision")
        if not isinstance(auth.get("native_message_id"), int) or isinstance(auth.get("native_message_id"), bool):
            raise RootCapError("action_operation authority needs native_message_id (the state.db row that carried the "
                               "canonical user decision into the owner's chat)")
        owner = auth.get("owner")
        if not isinstance(owner, dict) or not all(isinstance(owner.get(k), str) and owner[k]
                                                  for k in ("gatewayId", "profile", "storedSessionId")):
            raise RootCapError("action_operation authority needs owner gatewayId/profile/storedSessionId")
        mapping = rec.get("mapping")
        if not isinstance(mapping, dict) or not all(isinstance(mapping.get(k), str) and mapping[k].strip()
                                                    for k in ("phrase", "rationale", "reviewed_by")):
            raise RootCapError("action_operation authority needs a reviewed mapping (phrase, rationale, reviewed_by) "
                               "from the approved capacity wording to the guarded key")
    return rec


def _load_sources(dirs):
    out, known = [], False
    for d in dirs:
        path = Path(d) / SOURCES_FILE
        if not path.exists():
            continue
        known = True
        for rec in _read_list(path):
            out.append(_validate_source(rec))
    return out, known


def _user_steps_core():
    path = Path(__file__).resolve().parent.parent / "user-steps" / "core.py"
    spec = importlib.util.spec_from_file_location("_root_cap_user_steps_core", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _strip_state(body):
    return {k: v for k, v in body.items() if k not in ("state", "resolution")}


def _verify_operation(auth, mapping, owner_card, home):
    """Read-only check of ONE explicitly referenced ActionStore operation (never a search): a positive approval that
    authorizes execution, recorded for the CURRENT, unwithdrawn revision of the request it names, whose frozen intent
    equals the stored revision, whose text carries the reviewed capacity wording, and whose stored session is the
    owner card's. Legacy rows without a question binding are accepted on these equalities; a bound row must also
    carry the correct scope digest. Raises RootCapError with the exact conflict."""
    owner = auth["owner"]
    ns = json.dumps([owner["gatewayId"], owner["profile"]], sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False)
    path = Path(auth["db"])
    if not path.is_file():
        raise RootCapError("approval operation store not found")
    try:
        conn = _acceptance()._ro_connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            op_row = conn.execute("select body, request, revision from operations where id=? and namespace=? "
                                  "and session=?", (auth["operation_id"], ns, owner["storedSessionId"])).fetchone()
            req = conn.execute("select revision, state, resolution from requests where namespace=? and id=? "
                               "and session=?", (ns, auth["request_id"], owner["storedSessionId"])).fetchone()
            rev = conn.execute("select body from revisions where namespace=? and id=? and revision=?",
                               (ns, auth["request_id"], auth["revision"])).fetchone()
        finally:
            conn.close()
        op = json.loads(op_row["body"]) if op_row else None
        stored = json.loads(rev["body"]) if rev else None
        resolution = json.loads(req["resolution"]) if req and req["resolution"] else None
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        raise RootCapError(f"approval operation unreadable ({type(exc).__name__})")
    if op is None or req is None or stored is None:
        raise RootCapError("approval operation, request or revision not found for the referenced owner")
    intent = op.get("intent") or {}
    if (op.get("requestId"), op.get("revision"), op.get("owner")) != (auth["request_id"], auth["revision"], owner) \
            or op_row["request"] != auth["request_id"] or op_row["revision"] != auth["revision"]:
        raise RootCapError("approval operation does not match the referenced request, revision and owner")
    if intent.get("effect") != "approve" or intent.get("executionAuthorized") is not True:
        raise RootCapError("referenced operation is not a positive execution-authorizing approval "
                           f"(effect {intent.get('effect')!r})")
    if op.get("dispatchState") == "cancelled":
        raise RootCapError("referenced approval operation was cancelled")
    if req["state"] == "withdrawn":
        raise RootCapError("referenced approval request was withdrawn (conflict: the freeze is no longer approved)")
    if req["revision"] != auth["revision"]:
        raise RootCapError(f"referenced approval revision {auth['revision']} is superseded by revision "
                           f"{req['revision']} (conflict: the current revision must be re-approved)")
    if not resolution or resolution.get("operationId") != auth["operation_id"]:
        raise RootCapError("referenced operation is not the recorded resolution of the current revision")
    request = intent.get("request")
    if not isinstance(request, dict) or _strip_state(request) != _strip_state(stored):
        raise RootCapError("approved intent differs from the stored revision (not the approved text)")
    scope = stored.get("scope") or {}
    if scope.get("complete") is not True:
        raise RootCapError("approved request scope is incomplete")
    if not any(isinstance(ref, dict) and ref.get("type") == "kanban" and ref.get("id") == owner_card
               and ref.get("gatewayId") == owner["gatewayId"] and ref.get("profile") == owner["profile"]
               for ref in scope.get("sourceRefs") or []):
        raise RootCapError(f"approved request scope.sourceRefs does not name the owner card {owner_card} "
                           "(the approval is not tied to this rollout)")
    text = f"{stored.get('title', '')}\n{scope.get('markdown', '')}".lower()
    if mapping["phrase"].lower() not in text:
        raise RootCapError("approved request does not carry the reviewed capacity wording "
                           f"{mapping['phrase']!r}")
    if "binding" in op:
        try:
            expected = _user_steps_core().scope_digest(request)
        except Exception as exc:
            raise RootCapError(f"approval scope digest could not be computed ({type(exc).__name__})")
        if (op["binding"] or {}).get("scopeDigest") != expected:
            raise RootCapError("approval scope digest does not match the approved request")
    _verify_native_operation_row(auth, op, owner, home)
    try:
        task = owner_task(owner_card, home)
    except RootCapError as exc:
        raise RootCapError(f"owner card unreadable ({exc})")
    if task is None:
        raise RootCapError("owner card not found")
    if "session_id" not in task:
        raise RootCapError("owner card has no session_id to bind the approval to")
    if task["session_id"] != owner["storedSessionId"]:
        raise RootCapError("approval session is not the owner card's session")


def _verify_native_operation_row(auth, op, owner, home):
    """The ActionStore record alone is not authority (any process can call ActionStore.decide). The canonical decision
    must also exist as the native user row the owning runtime admitted into the owner's chat: a user-role row of the
    owner's session lineage, in a human-opened chat, whose embedded frozen decision equals this operation's intent
    (so native request == intent.request == stored revision)."""
    db = Path(auth["state_db"]) if auth.get("state_db") else Path(home) / "state.db"
    acc = _acceptance()
    try:
        conn = acc._ro_connect(db)
    except sqlite3.Error as exc:
        raise RootCapError(f"native decision row unreadable ({type(exc).__name__})")
    try:
        try:
            row = conn.execute("select session_id, role, content from messages where id=?",
                               (auth.get("native_message_id"),)).fetchone()
            lineage = acc.lineage(conn, owner["storedSessionId"])
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RootCapError(f"native decision row unreadable ({type(exc).__name__})")
    if not row:
        raise RootCapError(f"native decision message {auth.get('native_message_id')} does not exist")
    sid, role, content = row
    if role != "user":
        raise RootCapError(f"native decision message {auth['native_message_id']} is {role}-authored, not a user row")
    if sid not in lineage:
        raise RootCapError(f"native decision message {auth['native_message_id']} is in another chat ({sid}), not the "
                           "approval's owner session")
    human, why = human_origin(sid, db)
    if not human:
        raise RootCapError(f"native decision message is not human authority: {why}")
    text = (content or "").lstrip()
    if not text.startswith("Canonical user decision."):
        raise RootCapError("native decision message is not a canonical user decision row")
    embedded = None
    for line in text.splitlines():
        if line.startswith("{"):
            try:
                embedded = json.loads(line)
            except ValueError:
                embedded = None
            break
    expected = {"operationId": auth["operation_id"], **(op.get("intent") or {})}
    if embedded != expected:
        raise RootCapError("native decision row does not carry this operation's frozen request "
                           "(native request differs from the stored intent)")


def _verify_source_authority(src, digest, home):
    auth = src["authority"]
    if auth["type"] == "native_message":
        db = Path(auth["state_db"]) if auth.get("state_db") else Path(home) / "state.db"
        _verify_native_approval(db, auth["session"], auth["message_id"], Path(src["contract_ref"]), digest,
                                src["key"], src["frozen_value"], src["owner_card"])
    else:
        _verify_operation(auth, src["mapping"], src["owner_card"], home)


def _source_rec(src, home, digest, why=None):
    rec = {"target_home": str(home), "key": src["key"], "frozen_value": src["frozen_value"],
           "contract_ref": str(Path(src["contract_ref"]).resolve()), "contract_revision_digest": digest,
           "approval_ref": {"source_id": src["source_id"], "authority": src["authority"]["type"]},
           "owner_ref": {"card": src["owner_card"]}, "active": True, "source_id": src["source_id"]}
    if why:
        rec["_unverified"] = why
    return rec


def _source_records(src, contracts):
    """Records one descriptor contributes: [] (lapsed or already projected), [verified record] (also projected into the
    root store so a later permanent supersession has a record to flip) or [unverified record] (known contract whose
    authority cannot be proven now: the targeted write is refused with the exact reason, never allowed)."""
    home = Path(_real(src["target_home"]))
    key, value = src["key"], src["frozen_value"]
    try:
        if key not in GUARDED_KEYS:
            raise RootCapError(f"unsupported key {key!r}")
        digest = _artifact_digest(src["contract_ref"], key, value)
    except RootCapError as exc:
        return [_source_rec(src, home, "unverified", f"approved contract artifact: {exc}")]
    same = [c for c in contracts if c.get("key") == key and _real(c.get("target_home", "")) == _real(home)
            and c.get("contract_revision_digest") == digest]
    if same and not any(c.get("active") for c in same):
        return []  # superseded by a permanent authorized change or a newer revision: no longer frozen
    try:
        status = owner_status(src["owner_card"], home)
        if status is None:
            raise RootCapError("rollout owner card not found")
        if status in TERMINAL_STATES:
            return []  # the rollout is over: nothing is frozen
        if status not in OPEN_STATES:
            raise RootCapError(f"rollout owner card state {status!r} is not open")
        _verify_source_authority(src, digest, home)
    except RootCapError as exc:
        return [_source_rec(src, home, digest, f"declared rollout authority cannot be verified now ({exc})")]
    if same:
        return []  # the active projection already carries this exact revision
    rec = {**_source_rec(src, home, digest), "registered_at": time.time()}

    def apply(data):
        if any(c.get("key") == key and c.get("contract_revision_digest") == digest
               and _real(c.get("target_home", "")) == _real(home) for c in data):
            return None, None
        return [*data, rec], rec

    try:
        _mutate(shared_dir(home) / CONTRACTS_FILE, apply)
    except RootCapError as exc:
        return [_source_rec(src, home, digest, f"verified freeze could not be projected ({exc})")]
    return [rec]


def _apply_sources(sources, contracts):
    out = list(contracts)
    for src in sources:
        try:
            out.extend(_source_records(src, contracts))
        except Exception as exc:  # any fault on a declared freeze keeps it known and refuses only the targeted write
            home = Path(_real(src["target_home"]))
            out.append(_source_rec(src, home, "unverified", f"source resolution failed ({type(exc).__name__}: {exc})"))
    return out


def declare_source(store_root, *, target_home, key, frozen_value, contract_ref, owner_card, authority, mapping=None,
                   source_id=None):
    """Declare a rollout freeze by explicit reference. The descriptor is verified now (a freeze that cannot be proven
    is not declared) and again at every guarded call. Written to the ROOT home's shared store."""
    home = Path(_real(target_home))
    if not (home / "config.yaml").is_file():
        raise RootCapError(f"{home} is not a Hermes home (no config.yaml)")
    value = _intval(frozen_value)
    if value is None:
        raise RootCapError("frozen value must be an integer")
    rec = {"source_id": source_id or _sha(f"{home}|{key}|{Path(contract_ref).resolve()}")[:16],
           "target_home": str(home), "key": key, "frozen_value": value,
           "contract_ref": str(Path(contract_ref).resolve()), "owner_card": owner_card, "authority": authority,
           **({"mapping": mapping} if mapping else {}), "declared_at": time.time()}
    _validate_source(rec)
    out = _source_records(rec, [])
    if not out:
        raise RootCapError("cannot declare: the freeze is not active (owner finished or revision superseded)")
    if out[0].get("_unverified"):
        raise RootCapError(f"cannot declare: {out[0]['_unverified']}")
    path = shared_dir(home) / SOURCES_FILE

    def apply(data):
        return [*[d for d in data if d.get("source_id") != rec["source_id"]], rec], rec

    return _mutate(path, apply)


# ----------------------------------------------------------------------------- command parsing
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
_KEYWORDS = {"{", "}", "!", "then", "do", "else", "if", "while", "until", "time", "fi", "done"}
_WRAPPER_ARGS = {"sudo": {"-u", "-g", "-h", "-p", "-C", "-r", "-t", "-U", "-D"}, "env": {"-u", "-C", "-S"},
                 "nice": {"-n"}, "ionice": {"-c", "-n", "-p"}, "timeout": {"-s", "-k"}, "stdbuf": set(),
                 "command": set(), "exec": set(), "nohup": set(), "setsid": set(), "time": set()}
_EXEC_CALLS = {"system", "popen", "run", "call", "check_call", "check_output", "Popen", "getoutput",
               "getstatusoutput"}
_CFG_WRITE = re.compile(r"safe_dump|\bdump\(|\.write\(|write_text|save_config|set_config_value|set_nested")
_WRITERS = {"tee", "cp", "mv", "install", "dd", "ed", "ex", "vi", "vim", "nano"}
_INPLACE = {"sed", "perl", "yq", "awk"}


_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(\\?)(['\"]?)(\w+)\3")


def _heredocs(command):
    """(command text without heredoc bodies, [{line, delimiter, quoted, body}]). A body is data unless its consumer
    executes stdin (see `_scan_heredoc`); an unquoted delimiter still lets the shell expand `$(...)` in the body."""
    lines, docs, cur = [], [], None
    for line in command.replace("\\\n", " ").splitlines():
        if cur is not None:
            if (line.lstrip("\t") if cur["strip_tabs"] else line).strip() == cur["delimiter"]:
                docs.append(cur)
                cur = None
            else:
                cur["body"].append(line)
            continue
        lines.append(line)
        m = _HEREDOC.search(line)
        if m:
            cur = {"line": line, "delimiter": m.group(4), "quoted": bool(m.group(2) or m.group(3)),
                   "strip_tabs": bool(m.group(1)), "body": []}
    if cur is not None:
        docs.append(cur)
    for d in docs:
        d["body"] = "\n".join(d["body"])
    return "\n".join(lines), docs


def _substitutions(text, quotes=True):
    """Bodies of `$(...)` / backtick substitutions the shell would EXECUTE: unquoted and double-quoted words, never
    single-quoted data. `quotes=False` reads an unquoted-delimiter heredoc body, where quotes are literal."""
    out, i, n, sq, dq = [], 0, len(text), False, False
    while i < n:
        c = text[i]
        if c == "\\" and not sq:
            i += 2
            continue
        if quotes and c == "'" and not dq:
            sq = not sq
        elif quotes and c == '"' and not sq:
            dq = not dq
        elif not sq and c == "$" and text[i + 1:i + 2] == "(" and text[i + 2:i + 3] != "(":
            depth, j, qs, qd = 1, i + 2, False, False
            while j < n and depth:
                d = text[j]
                if d == "\\" and not qs:
                    j += 1
                elif d == "'" and not qd:
                    qs = not qs
                elif d == '"' and not qs:
                    qd = not qd
                elif not qs and d == "(":
                    depth += 1
                elif not qs and d == ")":
                    depth -= 1
                j += 1
            out.append(text[i + 2:j - 1 if depth == 0 else j])
            i = j
            continue
        elif not sq and c == "`":
            j = i + 1
            while j < n and text[j] != "`":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i + 1:j])
            i = j
        i += 1
    return out


def _split(command):
    """Words per simple command (separators ; & | newline ( ) ), plus redirect targets. `command` has its heredoc
    bodies already removed by `_heredocs`."""
    text = command.replace("`", ";")
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|\n<>()")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    tokens = list(lex)  # ValueError (unbalanced quote) propagates: the caller decides ambiguity
    segments, words, redirects, skip, target = [], [], [], False, False
    for tok in tokens:
        if tok and all(c in ";&|\n()" for c in tok):
            segments.append((words, redirects))
            words, redirects, skip, target = [], [], False, False
        elif tok and all(c in "<>&|" for c in tok) and ("<" in tok or ">" in tok):
            skip = tok.endswith("&")
            target = ">" in tok and not skip
            if not skip and not target:
                skip = True  # input redirect: the filename is not an argument
        elif skip:
            skip = False
        elif target:
            redirects.append(tok)
            target = False
        else:
            words.append(tok)
    segments.append((words, redirects))
    return segments


def _strip(words, env):
    """Drop env assignments, shell keywords and transparent wrappers; returns the real command words."""
    words = list(words)
    while words:
        first = words[0]
        name = PurePosixPath(first).name
        if first in _KEYWORDS:
            words.pop(0)
        elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", first):
            k, v = first.split("=", 1)
            env[k] = v
            words.pop(0)
        elif name in _WRAPPER_ARGS:
            words.pop(0)
            takes = _WRAPPER_ARGS[name]
            while words and (words[0].startswith("-") or (name == "env" and "=" in words[0])
                             or (name == "timeout" and re.match(r"^\d", words[0]))):
                opt = words.pop(0)
                if "=" in opt and not opt.startswith("-"):
                    k, v = opt.split("=", 1)
                    env[k] = v
                elif opt in takes and words:
                    words.pop(0)
        else:
            break
    return words


def _is_hermes(words):
    name = PurePosixPath(words[0]).name
    if re.fullmatch(r"hermes(-[a-z0-9_-]+)?(\.py)?", name) and "python" not in name:
        return True
    if re.fullmatch(r"(hermes-core-)?python[\d.]*", name) and "-m" in words:
        i = words.index("-m")
        return i + 1 < len(words) and words[i + 1].split(".")[0] == "hermes_cli"
    return False


class _Parsed:
    def __init__(self):
        self.sets = []          # {"key","value","home","kind"}
        self.raw = []           # config-file writes naming the key: {"paths": [...]}
        self.dynamic = False    # executes a non-literal command while naming the key
        self.broken = False     # unparseable text naming a guarded set
        self.cfg_api = False


def _scan_command(command, parsed, env=None, depth=0):
    if depth > 6 or not isinstance(command, str):
        return
    env = dict(env or {})
    command, docs = _heredocs(command)
    # Substitutions execute inside double quotes and unquoted words (never single quotes) — also a `echo "$(...)"`.
    for body in _substitutions(command):
        _scan_command(body, parsed, env, depth + 1)
    for doc in docs:
        _scan_heredoc(doc, parsed, env, depth)
    try:
        segments = _split(command)
    except ValueError:
        if re.search(r"config\s+(set|unset)\s+\S*max_in_progress", command) or \
                re.search(r"max_in_progress", command) and "config.yaml" in command:
            parsed.broken = True
        return
    lowered = command.lower()
    for words, redirects in segments:
        words = _strip(words, env)
        if not words or words[0].startswith("#"):
            continue
        exe = PurePosixPath(words[0]).name
        # `export NAME=value` / bare assignments change the running env for later segments.
        if exe == "export":
            for w in words[1:]:
                if "=" in w:
                    k, v = w.split("=", 1)
                    env[k] = v
            continue
        if exe in _SHELLS:
            payload = _payload(words, "c")
            if payload is not None:
                _scan_command(payload, parsed, env, depth + 1)
            continue
        if exe == "eval":
            _scan_command(" ".join(words[1:]), parsed, env, depth + 1)
            continue
        if re.fullmatch(r"(hermes-core-)?python[\d.]*", exe) and "-c" in words[:4]:
            payload = _payload(words, "c")
            if payload is not None:
                _scan_code(payload, parsed, depth + 1)
            continue
        if _is_hermes(words):
            _scan_hermes(words, env, parsed)
        _scan_raw(exe, words, redirects, lowered, parsed)
    # a redirect-only write such as `echo 'max_in_progress: 8' >> config.yaml` still reaches _scan_raw above


def _scan_heredoc(doc, parsed, env, depth):
    """A heredoc body is executed when its line feeds stdin to a shell (`bash <<EOF`, `cat <<EOF | sh`) or to
    `python -`; otherwise it is data (`cat <<'EOF'`, `tee file`), except that an unquoted delimiter lets the shell
    expand `$(...)` in the body regardless of the consumer."""
    body = doc["body"]
    if not doc["quoted"]:
        for sub in _substitutions(body, quotes=False):
            _scan_command(sub, parsed, env, depth + 1)
    try:
        segments = _split(_heredocs(doc["line"])[0])
    except ValueError:
        segments = []
    for words, _redirects in segments:
        words = _strip(words, dict(env))
        if not words:
            continue
        exe = PurePosixPath(words[0]).name
        operands = [w for w in words[1:] if not w.startswith("-")]
        if exe in _SHELLS and "-c" not in "".join(w for w in words[1:] if w.startswith("-") and not w.startswith("--")) \
                and (not operands or "-s" in words):
            _scan_command(body, parsed, env, depth + 1)
        elif re.fullmatch(r"(hermes-core-)?python[\d.]*", exe) and (not operands or operands == ["-"]) \
                and "-c" not in words and "-m" not in words:
            _scan_code(body, parsed, depth + 1)
        elif exe == "eval" or (exe in (".", "source") and operands[:1] == ["/dev/stdin"]):
            _scan_command(body, parsed, env, depth + 1)


def _payload(words, flag):
    for i, w in enumerate(words[1:], 1):
        if w.startswith("-") and not w.startswith("--") and flag in w[1:]:
            return words[i + 1] if i + 1 < len(words) else None
    return None


def _scan_hermes(words, env, parsed):
    profile = None
    for i, w in enumerate(words):
        if w in ("-p", "--profile") and i + 1 < len(words):
            profile = words[i + 1]
        elif w.startswith("--profile="):
            profile = w.split("=", 1)[1]
    idx = next((i for i in range(len(words) - 1) if words[i] == "config" and words[i + 1] in ("set", "unset")), None)
    if idx is None:
        return
    rest = [w for w in words[idx + 2:] if not w.startswith("-")]
    if not rest:
        return
    key, value = rest[0], rest[1] if len(rest) > 1 else None
    if "=" in key:
        key, value = key.split("=", 1)
    if words[idx + 1] == "unset":
        value = None
    explicit = env.get("HERMES_HOME")
    if explicit is not None:
        home = _expand(explicit)
    else:
        home = _env_home()
    if home is not None and profile is not None:
        home = str(_root_of(home)) if profile == "default" else str(_root_of(home) / "profiles" / profile)
    parsed.sets.append({"key": key, "value": value, "home": home})


def _scan_raw(exe, words, redirects, lowered, parsed):
    paths = [_real(p) for p in (_expand(w) for w in [*words[1:], *redirects]) if p]
    if not any(p.endswith("config.yaml") for p in paths):
        return
    writer = bool(redirects) and any(_real(_expand(r) or "") .endswith("config.yaml") for r in redirects if _expand(r))
    if exe in _WRITERS:
        writer = True
    if exe in _INPLACE and any(w.startswith("-") and not w.startswith("--") and "i" in w[1:] or w in (
            "--in-place", "--inplace") for w in words[1:]):
        writer = True
    if writer and any(_leaf(k) in lowered for k in GUARDED_KEYS):
        parsed.raw.append({"paths": paths})


def _fold(node, env):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            piece = _fold(v.value if isinstance(v, ast.FormattedValue) else v, env)
            if piece is None:
                return None
            parts.append(piece)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _fold(node.left, env), _fold(node.right, env)
        return None if a is None or b is None else a + b
    return None


def _scan_code(code, parsed, depth=0):
    if depth > 6:
        return
    try:
        tree = ast.parse(code)
    except (SyntaxError, TypeError, ValueError):
        return
    env, aliases, assigned = {}, {"terminal"}, set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "hermes_tools":
            aliases.update(a.asname or a.name for a in node.names if a.name == "terminal")
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value = _fold(node.value, env)
            if name in assigned or value is None:
                env.pop(name, None)
            else:
                env[name] = value
            assigned.add(name)
    leaf = _leaf(GUARDED_KEYS[0])
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name in aliases or name == "terminal":
            arg = next((k.value for k in node.keywords if k.arg == "command"), node.args[0] if node.args else None)
        elif name in _EXEC_CALLS:
            arg = next((k.value for k in node.keywords if k.arg in ("args", "cmd")), node.args[0] if node.args else None)
        else:
            continue
        command = None
        if isinstance(arg, (ast.List, ast.Tuple)):
            parts = [_fold(e, env) for e in arg.elts]
            command = None if any(p is None for p in parts) else " ".join(shlex.quote(p) for p in parts)
        elif arg is not None:
            command = _fold(arg, env)
        if command is None:
            if leaf in code:
                parsed.dynamic = True
        else:
            _scan_command(command, parsed, None, depth + 1)
    if leaf in code and _CFG_WRITE.search(code) and "config" in code:
        parsed.cfg_api = True


def parse(tool_name, args):
    parsed = _Parsed()
    name = str(tool_name).rsplit("__", 1)[-1]
    if not isinstance(args, dict):
        return parsed
    if name == "terminal" and isinstance(args.get("command"), str):
        _scan_command(args["command"], parsed)
    elif name == "execute_code" and isinstance(args.get("code"), str):
        _scan_code(args["code"], parsed)
    return parsed


# ----------------------------------------------------------------------------- native decision
_NEG = re.compile(r"\b(revoke|rescind|withdraw|retract|deny|denied|do not|don't|dont|never|no longer|"
                  r"not authori[sz]e|unauthori[sz]e|cancel that|never mind)\b")
# A cancellation needs no key: "Cancel that." withdraws the previous relevant decision.
_CANCEL = re.compile(r"\b(revoke|rescind|withdraw|retract|never ?mind|cancel (that|it|this|the (change|raise))|"
                     r"scratch that|disregard (that|it)|ignore (that|it|my (last|previous)))\b")
_AUTH = re.compile(r"\b(authori[sz]e|authori[sz]ed|approve|approved|permit|allow)\b")
# Reported speech is not a decision: someone else's words, quoted text, "not me".
_REPORTED = re.compile(r"\b(said|says|wrote|writes|claimed?|claims|asked|asks|told|quote[sd]?|suggested|proposed|"
                       r"according to|reads?|reported)\b|^\s*(>|the (assistant|agent|worker|notice|bot|card))")
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|`[^`]*`")
_QUOTED_DQ = re.compile(r"\"[^\"]*\"|“[^”]*”")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})", re.I)
_OWNER_TERMINAL = re.compile(r"\b(owner|card|run)\s+(completion|completes|is done|finishes|terminal)|"
                             r"\b(on|upon|at)\s+(owner |card )?(completion|done)\b|\bowner completion\b")


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", text) if s.strip()]


def _parse_iso(text):
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _items(user_texts):
    """Normalize authority input to [{text, ref}]: plain strings (legacy callers) or typed rows with the native
    message identity (`message_uid`) the coach capture recovered."""
    out = []
    for raw in user_texts or ():
        if isinstance(raw, str):
            out.append({"text": raw, "ref": None})
        elif isinstance(raw, dict) and isinstance(raw.get("content"), str):
            out.append({"text": raw["content"], "ref": raw.get("message_uid")})
    return out


def _state_db_home():
    try:
        from hermes_constants import get_hermes_home
        return str(get_hermes_home())
    except Exception:
        return _env_home()


def human_origin(session_id, db=None):
    """(ok, why): the deciding chat must be a human-opened session (desktop/tui/cli/local). A Kanban/cron/subagent
    session's opening prompt can quote any text, so its user-role rows are machine content, not the user's decision.
    Unknown or unreadable means no authority (the safe direction: the change stays refused). `db` is the state.db
    that holds the session (default: the running home's)."""
    try:
        acc = _acceptance()
        conn = acc._ro_connect(Path(db) if db else Path(_state_db_home()) / "state.db")
        try:
            row = conn.execute("select source from sessions where id=?", (session_id,)).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return False, f"session origin unreadable ({type(exc).__name__})"
    source = str((row or [""])[0] or "")
    if source not in acc.HUMAN_SOURCES:
        return False, f"session {session_id or '?'} is a {source or 'unknown'} session, not a human chat"
    return True, ""


# A question is not a decision ("Did I authorize ...", "Can I raise ..."), but a negated imperative that merely starts
# with the same word is ("Do not raise ...", "Can't raise ...").
_QUESTION_LEAD = re.compile(r"\?|^\s*(?:did|do|does|have|has|had|is|are|was|were|if|what if|should|can|could|may|would|"
                            r"shall|will|why|how|when|whether)\b(?!['\u2019]|\s+not\b)")
# Someone else's words withdrawing the decision are not the user's cancellation. Narrower than _REPORTED on purpose:
# a missed cancellation admits the write, so only a clear third-party attribution disqualifies it ("I said cancel
# that" and "you told me to cancel it" stay the user's own).
_REPORTED_CANCEL = re.compile(r"^\s*(>|the (assistant|agent|worker|notice|bot|card)\b)|\baccording to\b|"
                              r"\b(?!(?:i|we|you)\b)\w+\s+(said|says|wrote|writes|claimed?|claims|asked|asks|told|"
                              r"suggested|proposed|quote[sd]?|reported|reads?)\b")


def _own_speech(text):
    """The human's own words only: every quoted span (double, curly, single, curly single) is removed; backticks
    around an identifier are not quotation (`kanban.max_in_progress` is still the user naming the key) and
    apostrophes inside words ("I'm", "owner's") are not quotation marks."""
    return _QUOTED_SQ.sub(" ", _QUOTED_DQ.sub(" ", text)).replace("`", "")


def _own_terms(own_text):
    """The human's own affirmative sentences of one message, original case kept: quoted spans are already removed;
    questions, someone else's words, cancellations and negations are dropped, so none of them can supply an owner,
    run, session, restore value, expiry or trigger."""
    keep = []
    for s in _sentences(own_text):
        low = s.lower()
        if _QUESTION_LEAD.search(low) or _CANCEL.search(low) or _NEG.search(low) or _REPORTED.search(low):
            continue
        keep.append(s)
    return " ".join(keep)


# "not temporary", "don't make this temporary", "isn't a temporary exception" negate the temporary claim itself.
_NOT_TEMP = re.compile(r"(?:\bnot\b|n['\u2019]t\b|\bnever\b|\bnon[- ]|\bno\b)(?:\s+\w+){0,3}?\s+temporar")
_PERM_NEG = re.compile(r"(?:\bnot\b|n['\u2019]t\b|\bnever\b|\bno longer\b|\bnon[- ]?)(?:\s+\w+){0,3}?\s+permanen|"
                       r"\bnon[- ]?permanen")
# Bounded-ness signals other than the word "temporary": a restoration, a time bound, or an owner-bound scope. Any of
# them in the human's own affirmative words means the change is NOT an open-ended permanent one, whatever wording
# was used, so the owner/restore provenance is demanded and (when valid) persisted as a temporary exception.
_BOUND_RESTORE = re.compile(r"\b(?:restor\w*|revert\w*|roll(?:ed|ing)?[- ]?back|roll (?:it|this|that) back|put (?:it|this|that) back|"
                            r"set (?:it|this|that) back|change (?:it|this|that) back|go(?:ing)? back to)\b")
_UNIT = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|nights?|weeks?)"
_QTY = (r"(?:\d+(?:\.\d+)?|(?:a\s+)?(?:few|couple(?:\s+of)?|several)|half\s+an?|half|a|an|one|two|three|four|five|"
        r"six|seven|eight|nine|ten|twenty|thirty)")
_BOUND_TIME = re.compile(
    r"\b(?:only|just|solely|merely)\s+(?:for|until|till|while|during|to)\b|"
    # a duration with any run of qualifiers in front of the number: "for only 10 minutes", "for just a couple of
    # hours", "for the next 5 min", "for up to an hour", "within 10 minutes", "10 minutes only"
    r"\b(?:for|within|in)\s+(?:(?:only|just|merely|solely|the\s+next|the\s+first|next|another|a\s+further|about|"
    r"roughly|approximately|around|up\s+to|at\s+most|at\s+least)\s+)*" + _QTY + r"[\s-]*" + _UNIT + r"\b|"
    r"\b" + _QTY + r"[\s-]*" + _UNIT + r"\s+(?:only|max|maximum|at\s+most|tops)\b|"
    r"\b(?:until|till|expires?|expiry|expiring|time[- ]?boxed|time[- ]?limited|one[- ]?off|one[- ]?time|short[- ]?term|"
    r"for now|for today|for tonight|for the (?:moment|day|night|time being|meantime|duration)|for a (?:bit|while)|"
    r"just this once|this once|briefly|while (?:it|this|that) (?:runs|finishes|completes|drains))\b")
_BOUND_OWNER = re.compile(r"\bowner\s+(?:card|run|session|task)\b|\bthis (?:session|run) only\b")
# A bound signal is cancelled only by a negator right in front of it (within two words): "Don't restore it",
# "not only for an hour", "this is not time-boxed". A negation elsewhere in the sentence ("Don't forget to restore
# it afterwards") leaves the bound standing, so the unsafe direction (a missed bound = untracked permanent) is closed.
_NEGATOR_BEFORE = re.compile(r"(?:\bnot|n['\u2019]t|\bnever|\bno|\bwithout|\bnon)[\s-]+(?:\w+[\s-]+){0,1}$")


def _negated_before(low, pos):
    return bool(_NEGATOR_BEFORE.search(low[max(0, pos - 40):pos]))


def _temporary_intent(own_text):
    """True when the human's own words (quoted spans already removed; questions and someone else's words skipped)
    make the change bounded: they say it is temporary / non-permanent, restrict it from being permanent, or name a
    restoration, a time bound or an owner-bound scope. One coherent rule, not a phrase list per wording: an
    authorization is permanent only when NONE of those bounded-ness signals appears in the user's own speech.
    Unlike _own_terms this KEEPS negated sentences ("Do not make this permanent, it is temporary." is a
    restriction; discarding it as non-affirmative would upgrade the authorization to an untracked permanent one),
    except that a negated restoration/time/owner clause ("Don't restore it", "not only for an hour") is not a bound.
    The classification only ever tightens the decision: it demands the owner/restore provenance from the human's
    own affirmative terms (`_own_terms`) and never supplies any."""
    for s in _sentences(own_text):
        low = s.lower()
        if _QUESTION_LEAD.search(low) or _REPORTED.search(low):
            continue
        if _PERM_NEG.search(low):
            return True
        # Negation is scoped to the one signal it governs: "not temporary" blanks only that span (same length, so
        # positions stay valid) and every other signal of the sentence still counts ("This is not temporary, but
        # restore it on owner completion." / "..., but it expires tomorrow." remain bounded).
        low = _NOT_TEMP.sub(lambda m: " " * len(m.group(0)), low)
        if "temporar" in low:
            return True
        for pat in (_BOUND_RESTORE, _BOUND_TIME, _BOUND_OWNER):
            if any(not _negated_before(low, m.start()) for m in pat.finditer(low)):
                return True
    return False


def _decision(items, rec, new_value):
    """Latest surviving direct human decision for this contract revision, whatever its value; it is returned only when
    it authorizes `new_value`. A later explicit decision for another value (or a cancellation) therefore supersedes an
    earlier one: the old value is refused, the latest is admitted (None / dict). Every sentence is classified as the
    user's own speech FIRST (quoted/reported/interrogative text is neither an authorization nor a cancellation),
    then as cancellation/negation, then as an authorization."""
    leaf = _leaf(rec["key"])
    digest = rec["contract_revision_digest"]
    current = None
    for item in items:
        raw = item["text"]
        text = raw.lower()
        text_own = _own_speech(raw)
        for s in _sentences(_own_speech(text)):
            if _QUESTION_LEAD.search(s):
                continue
            if _CANCEL.search(s):
                if not _REPORTED_CANCEL.search(s):
                    current = None
                continue
            if _NEG.search(s) and leaf in s:
                if not _REPORTED.search(s):
                    current = None
                continue
            bare = s
            if _REPORTED.search(s) or leaf not in bare or not _AUTH.search(bare):
                continue
            m = re.search(r"\bfrom\s+(\d+)\s+to\s+(\d+)\b", bare)
            if m and int(m.group(1)) == rec["frozen_value"]:
                target = int(m.group(2))
            elif not m and len(digest) >= 12 and digest[:12] in text:
                m2 = re.search(r"(?:=|\bto\b|->)\s*(\d+)\b", bare)
                target = int(m2.group(1)) if m2 else None
            else:
                target = None
            if target is None:
                continue
            # Temporary provenance (owner/run/session/restore/expiry/trigger) comes only from this message's own
            # affirmative sentences: quoted, reported, interrogative or withdrawing text is never a term.
            own = _own_terms(text_own)
            current = {"text": raw, "terms_text": own, "temporary": _temporary_intent(text_own),
                       "sha": _sha(raw), "ref": item["ref"], "target": target}
    if current is None or current["target"] != new_value:
        return None
    return current


def owner_task(card, target_home):
    """The owner card's row (status plus the optional columns present on this board), None when not found."""
    paths = []
    pinned = os.environ.get("HERMES_KANBAN_DB")
    if pinned:
        paths.append(pinned)
    root = str(target_home)
    paths.append(os.path.join(root, "kanban.db"))
    paths.extend(sorted(glob.glob(os.path.join(root, "kanban", "boards", "*", "kanban.db"))))
    seen, error = set(), None
    for path in paths:
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        try:
            conn = _acceptance()._ro_connect(Path(path))
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute("select * from tasks where id=?", (card,)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            error = exc
            continue
        if row:
            return dict(row)
    if error is not None:
        raise RootCapError("kanban board unreadable")
    return None


def _temporary_terms(decision, rec, session_id, now):
    """(terms, missing[]): owner/restore provenance parsed from the user's own decision text and validated against
    the actual owner card and run. A supplied field that cannot be verified is rejected, never stored as verified."""
    own_text = decision.get("terms_text", "")
    text = own_text.lower()
    missing = []
    card = re.search(r"\bt_[0-9a-z]{4,}\b", text)
    owner = {"card": card.group(0) if card else None, "session": session_id if "this session" in text else None}
    run = re.search(r"\brun\s+(\d+)\b", text)
    if run:
        owner["run"] = int(run.group(1))
    for label in ("gateway", "profile"):
        m = re.search(rf"\b{label}\s+([\w-]+)", text)
        if m:
            owner[label] = m.group(1)
    if not owner["card"]:
        missing.append("owner reference (an existing card id)")
    else:
        try:
            task = owner_task(owner["card"], rec["target_home"])
        except RootCapError:
            task = {"status": "unreadable"}
        status = None if task is None else str(task.get("status"))
        if task is None:
            missing.append(f"owner card {owner['card']} does not exist")
        elif status in TERMINAL_STATES:
            missing.append(f"owner card {owner['card']} is already finished")
        elif status not in OPEN_STATES:
            missing.append(f"owner card {owner['card']} state could not be verified")
        else:
            if "run" in owner and task.get("current_run_id") != owner["run"]:
                missing.append(f"owner run {owner['run']} is not the card's current run")
            if "profile" in owner and task.get("assignee") != owner["profile"]:
                missing.append(f"owner profile {owner['profile']} is not the card's assignee")
            if "gateway" in owner:
                missing.append("owner gateway cannot be verified here (omit it)")
    restore = next((s for s in _sentences(text) if "restore" in s), "")
    restore_to = re.findall(r"\bto\s+(\d+)\b", restore)
    restore_value = int(restore_to[-1]) if restore_to else None
    expiry = expiry_raw = _ISO.search(own_text[max(text.find("restore"), 0):]) \
        if restore else None
    owner_terminal = bool(_OWNER_TERMINAL.search(restore))
    if restore_value is None:
        missing.append("restore value")
    elif restore_value != rec["frozen_value"]:
        missing.append(f"restore target must be the original value {rec['frozen_value']}")
    if not owner_terminal and not expiry:
        missing.append("restore trigger (owner completion and/or a UTC expiry)")
    if expiry:
        when = _parse_iso(expiry.group(0).upper())
        if when is None or when <= now:
            missing.append("restore expiry is not a future UTC time")
    terms = {"owner_ref": owner, "restore_value": restore_value,
             "restore_trigger": {"owner_terminal": owner_terminal,
                                 "expires_at": expiry_raw.group(0) if expiry_raw else None}}
    return terms, missing


# ----------------------------------------------------------------------------- the guard
def _refusal(rec, why, key, value, session_id=""):
    name = Path(rec["contract_ref"]).name if rec else "unknown contract"
    digest = (rec or {}).get("contract_revision_digest", "")[:12]
    owner = ((rec or {}).get("owner_ref") or {}).get("card", "?")
    frozen = (rec or {}).get("frozen_value", "?")
    msg = (f"Root {key} is frozen at {frozen} by the approved rollout contract {name} (revision {digest}, owner "
           f"{owner}): {why}. A queue notice, headroom or a generic go-ahead is not permission to change it. "
           f"{ALTERNATIVES} To change it, the user must say so in their own words: the key, 'from {frozen} to "
           f"<new value>' (or the revision digest); a temporary change also needs an existing owner card, the "
           f"restore value {frozen} and a restore trigger (owner completion and/or a UTC expiry).")
    event = {"event": "agent_block", "rule": "root_cap", "key": key, "value": value, "frozen": frozen,
             "contract": (rec or {}).get("contract_ref"), "revision": digest, "session": session_id}
    return {"message": msg, "event": event}


def _group(contracts, home, key):
    """(records for this exact home+key, state, why) for every contract that is not lapsed."""
    recs = [r for r in contracts if r.get("key") == key and _real(r.get("target_home", "")) == _real(home)]
    live = []
    for r in recs:
        state, why = _contract_state(r)
        if state != "lapsed":
            live.append((r, state, why))
    return live


def mentions_target(args):
    """True when a tool call's own arguments name the guarded key: the narrow condition under which a guard fault
    must refuse instead of allow (unrelated calls are never refused by a guard fault)."""
    try:
        blob = json.dumps(args, default=str)
    except Exception:
        blob = str(args)
    return any(_leaf(k) in blob for k in GUARDED_KEYS)


def _call_sha(tool_name, args, tool_call_id):
    return _sha(json.dumps({"tool": str(tool_name), "args": args, "call": tool_call_id or ""},
                           sort_keys=True, default=str))


def check(tool_name, args, session_id, store_root, user_texts=(), now=None, tool_call_id=None):
    """None to allow, or {"message", "event"} to block. Never raises: a guard fault refuses only a call that names
    the guarded key (targeted uncertainty); unrelated calls are not affected."""
    name = str(tool_name).rsplit("__", 1)[-1]
    if name not in ("terminal", "execute_code") or not isinstance(args, dict):
        return None
    key = GUARDED_KEYS[0]
    try:
        return _check(name, args, session_id, store_root, user_texts, now, tool_call_id)
    except Exception as exc:
        if not mentions_target(args):
            return None
        return _refusal(None, f"the guard could not verify this change ({type(exc).__name__}: {exc})", key, None,
                        session_id)


def _check(name, args, session_id, store_root, user_texts, now, tool_call_id):
    now = now if now is not None else time.time()
    parsed = parse(name, args)
    key = GUARDED_KEYS[0]
    wants = [s for s in parsed.sets if s["key"] in GUARDED_KEYS]
    if not (wants or parsed.raw or parsed.dynamic or parsed.cfg_api or parsed.broken):
        return None
    raw_homes = {Path(p).parent.as_posix() for r in parsed.raw for p in r["paths"] if p.endswith("config.yaml")}
    candidates = [w["home"] for w in wants if w["home"]] + [_env_home(), *raw_homes]
    try:
        dirs = _dirs(store_root, candidates)
        contracts, known = _load_contracts(dirs)
        sources, sources_known = _load_sources(dirs)
    except RootCapError as exc:
        return _refusal(None, f"the contract registry is unreadable ({exc})", key, None, session_id)
    if sources:
        contracts = _apply_sources(sources, contracts)
    known = known or sources_known
    if not known:
        return None  # no projection anywhere: no contract, nothing is frozen (no hardcoded value)
    homes = sorted({_real(r.get("target_home", "")) for r in contracts if r.get("key") == key and r.get("active")})
    if not homes:
        return None

    def one(home, value, allow_authority):
        live = _group(contracts, home, key)
        if not live:
            return None
        values = {r["frozen_value"] for r, _s, _w in live}
        for r, state, why in live:
            if state == "ambiguous":
                return _refusal(r, why, key, value, session_id)
        if len(live) > 1 and len(values) > 1 or len({r["contract_revision_digest"] for r, _s, _w in live}) > 1:
            return _refusal(live[0][0], "more than one active contract revision freezes this key (ambiguous)",
                            key, value, session_id)
        rec = live[0][0]
        if value is not None and value == rec["frozen_value"]:
            return _restore_conflict(rec, store_root, key, session_id)
        if not allow_authority or value is None:
            return _refusal(rec, "the change cannot be tied to an exact new value", key, value, session_id)
        human, why = human_origin(session_id)
        if not human:
            return _refusal(rec, f"no native user decision authorizes this change ({why})", key, value, session_id)
        decision = _decision(_items(user_texts), rec, value)
        if decision is None:
            return _refusal(rec, "no native user decision authorizes this exact change", key, value, session_id)
        terms = {"owner_ref": None, "restore_value": None, "restore_trigger": None}
        if decision["temporary"]:
            terms, missing = _temporary_terms(decision, rec, session_id, now)
            if missing:
                return _refusal(rec, "temporary exception lacks " + "; ".join(missing), key, value, session_id)
        try:
            _admit(rec, decision, terms, value, session_id, name, args, tool_call_id)
        except RootCapError as exc:
            return _refusal(rec, f"exception provenance could not be persisted and read back ({exc})",
                            key, value, session_id)
        return None

    for w in wants:
        if w["home"] is None:
            home = homes[0]
            live = _group(contracts, home, key)
            return _refusal(live[0][0] if live else None, "the target home could not be resolved (ambiguous)",
                            key, None, session_id)
        if _real(w["home"]) not in homes:
            continue
        out = one(w["home"], _intval(w["value"]) if w["value"] is not None else None, True)
        if out:
            return out
    if parsed.raw or parsed.dynamic or parsed.cfg_api or parsed.broken:
        ambiguous = parsed.dynamic or parsed.cfg_api or parsed.broken
        for home in homes:
            if ambiguous or _real(home) in {_real(h) for h in raw_homes}:
                live = _group(contracts, home, key)
                if live:
                    return _refusal(live[0][0], "a direct or non-literal config write names this key and its "
                                    "value cannot be verified; use `hermes config set` with an authorized value",
                                    key, None, session_id)
    return None


def _exception_files(rec, store_root):
    return [Path(d) / EXCEPTIONS_FILE for d in _dirs(store_root, [rec["target_home"]])]


def _restore_conflict(rec, store_root, key, session_id):
    try:
        excs = []
        for path in _exception_files(rec, store_root):
            excs += [e for e in _read_list(path)
                     if e.get("contract_revision_digest") == rec["contract_revision_digest"]
                     and e.get("state") == "active"]
    except RootCapError as exc:
        return _refusal(rec, f"exception registry unreadable ({exc})", key, rec["frozen_value"], session_id)
    if not excs:
        return None
    last = sorted(excs, key=lambda e: e.get("admitted_at", 0))[-1]
    try:
        current = config_value(rec["target_home"], key)
    except Exception as exc:  # unreadable config: do not overwrite blindly
        return _refusal(rec, f"current value unreadable ({type(exc).__name__})", key, rec["frozen_value"], session_id)
    if current != last["override_value"]:
        return _refusal(rec, f"restore refused: the current value {current} differs from the temporary override "
                        f"{last['override_value']}; a newer decision may exist, so it is not overwritten",
                        key, rec["frozen_value"], session_id)
    return None


def _admit(rec, decision, terms, value, session_id, tool_name, args, tool_call_id):
    """Persist the admitted exception under the root-shared lock; concurrent admissions keep every distinct call."""
    path = shared_dir(rec["target_home"]) / EXCEPTIONS_FILE
    call = _call_sha(tool_name, args, tool_call_id)
    entry = {"kind": "temporary" if decision["temporary"] else "permanent", "key": rec["key"],
             "target_home": rec["target_home"], "contract_ref": rec["contract_ref"],
             "contract_revision_digest": rec["contract_revision_digest"], "approval_ref": rec["approval_ref"],
             "original_value": rec["frozen_value"], "override_value": value,
             "restore_value": terms["restore_value"], "restore_trigger": terms["restore_trigger"],
             "owner_ref": terms["owner_ref"], "decision_sha256": decision["sha"],
             "decision_ref": {"session": session_id, "message_uid": decision.get("ref")},
             "session": session_id, "tool": str(tool_name), "tool_call_id": tool_call_id, "call_sha256": call,
             "admitted_at": time.time(), "state": "admitted"}

    def apply(data):
        for old in data:
            if (old.get("decision_sha256"), old.get("session"), old.get("override_value"), old.get("call_sha256"),
                    old.get("state")) == (entry["decision_sha256"], session_id, value, call, "admitted"):
                return None, old
        return [*data, entry], entry

    return _mutate(path, apply)


def after(tool_name, args, session_id, store_root, now=None, tool_call_id=None):
    """Post-call readback: admitted exceptions become active/applied only if the real config now has the value.
    One transaction per root store, so a permanent supersession and its exception record move together."""
    name = str(tool_name).rsplit("__", 1)[-1]
    if name not in ("terminal", "execute_code") or not isinstance(args, dict):
        return
    now = now if now is not None else time.time()
    try:
        homes = [s["home"] for s in parse(name, args).sets if s.get("home")]
    except Exception:
        homes = []
    call = _call_sha(name, args, tool_call_id)
    for directory in _dirs(store_root, [_env_home(), *homes]):
        epath = Path(directory) / EXCEPTIONS_FILE
        if not epath.exists():
            continue
        with _locked(epath):
            data = _read_list(epath)
            changed, applied = False, []
            for e in data:
                try:
                    current = config_value(e["target_home"], e["key"])
                except Exception:
                    current = "unreadable"
                if e.get("state") == "admitted" and e.get("session") == session_id and e.get("call_sha256") == call:
                    if current == e["override_value"]:
                        e["state"] = "applied" if e["kind"] == "permanent" else "active"
                        e["applied_at"] = now
                        if e["kind"] == "permanent":
                            applied.append(e)
                    else:
                        e["state"], e["readback_value"] = "not_applied", current
                    changed = True
                elif e.get("state") == "active" and current == e.get("restore_value"):
                    e["state"], e["restored_at"] = "restored", now
                    changed = True
            cpath = Path(directory) / CONTRACTS_FILE
            contracts = None
            if applied:
                contracts = _read_list(cpath)
                for c in contracts:
                    for e in applied:
                        if c.get("contract_revision_digest") == e["contract_revision_digest"] and c.get("active"):
                            c["active"] = False
                            c["superseded_by"] = {"decision_sha256": e["decision_sha256"], "value": e["override_value"],
                                                  "session": e["session"], "at": now}
            if changed:
                _write_list(epath, data)
            if contracts is not None:
                _write_list(cpath, contracts)


def due_restores(store_root, now=None, homes=()):
    """Active temporary exceptions whose expiry passed or whose owner card finished. `restore` when the config still
    holds the override value AND the contract revision it was granted against is still the active one; anything else
    (value changed by a later decision, revision superseded) is a `conflict`, never an overwrite. Pure read: the
    existing owner/recovery path performs the action (`report_due`)."""
    now = now if now is not None else time.time()
    out = []
    for directory in _dirs(store_root, [*homes, _env_home()]):
        epath = Path(directory) / EXCEPTIONS_FILE
        if not epath.exists():
            continue
        try:
            contracts = _read_list(Path(directory) / CONTRACTS_FILE)
        except RootCapError:
            contracts = []
        for e in _read_list(epath):
            if e.get("state") != "active":
                continue
            trig = e.get("restore_trigger") or {}
            due = bool(trig.get("expires_at")) and (_parse_iso(trig["expires_at"]) or 0) <= now
            if trig.get("owner_terminal") and not due:
                try:
                    due = owner_status(e["owner_ref"]["card"], e["target_home"]) in TERMINAL_STATES
                except (RootCapError, KeyError, TypeError):
                    due = False
            if due:
                try:
                    current = config_value(e["target_home"], e["key"])
                except Exception:
                    current = "unreadable"
                same_revision = any(c.get("contract_revision_digest") == e.get("contract_revision_digest")
                                    and c.get("active") for c in contracts)
                out.append({"key": e["key"], "target_home": e["target_home"], "restore_value": e["restore_value"],
                            "owner_card": (e.get("owner_ref") or {}).get("card"),
                            "owner_session": (e.get("owner_ref") or {}).get("session"),
                            "override_value": e.get("override_value"),
                            "call_sha256": e.get("call_sha256"),
                            "action": "restore" if current == e["override_value"] and same_revision else "conflict",
                            "current": current})
    return out


def report_due(store_root, log, seen, now=None, homes=()):
    """Existing-owner recovery path: log each due/conflict exception once (`seen` is the caller's dedupe set) so the
    coach's normal notice machinery carries it to the owner. Reports only; nothing restores automatically."""
    try:
        due = due_restores(store_root, now=now, homes=homes)
    except Exception as exc:
        due = [{"key": GUARDED_KEYS[0], "action": "conflict", "call_sha256": "unreadable", "current": str(exc)}]
    fresh = []
    for d in due:
        ident = (d.get("call_sha256"), d["action"])
        if ident in seen:
            continue
        seen.add(ident)
        log({"event": "root_cap_restore_due", "rule": "root_cap", **d})
        fresh.append(d)
    return fresh


NOTICES_FILE = "root_cap_notices.json"


def _notice_text(d):
    key, card = d["key"], d.get("owner_card") or "?"
    if d["action"] == "restore":
        return (f"[coach] root-cap recovery due: the temporary raise of {key} (owner card {card}) reached its restore "
                f"trigger and the config still holds the override ({d['current']}). Restore it now: "
                f"`hermes config set {key} {d['restore_value']}` (the guard admits a restore to the frozen value). "
                f"Nothing restores it automatically.")
    return (f"[coach] root-cap recovery CONFLICT: the temporary raise of {key} (owner card {card}) reached its restore "
            f"trigger but the current value is {d['current']} (override was {d.get('override_value')}) or its contract "
            f"revision is no longer active. Do NOT overwrite it: a newer user decision may exist. Ask the user and "
            f"resolve it explicitly.")


def due_notices(store_root, session_id, log=None, now=None, homes=()):
    """Notice texts owed to THIS session for due/conflicting temporary exceptions: the existing coach `pre_llm_call`
    context path, so the owner's own chat (`owner_ref.session`) is told, and a first fallback delivery reaches the next
    live chat when the owner's chat is gone. Delivery is persisted (`root_cap_notices.json`, locked, keyed by exception
    call + action + session), so a restart or a second chat never repeats it. Never raises: a fault returns []."""
    if not session_id:
        return []
    try:
        due = due_restores(store_root, now=now, homes=homes)
    except Exception:
        return []
    now = now if now is not None else time.time()
    out = []
    for d in due:
        path = shared_dir(d["target_home"]) / NOTICES_FILE
        ident = (d.get("call_sha256"), d["action"])

        def apply(data, d=d, ident=ident):
            mine = [n for n in data if (n.get("call_sha256"), n.get("action")) == ident]
            if any(n.get("session") == session_id for n in mine):
                return None, (False, False)
            first = not mine
            if not first and session_id != d.get("owner_session"):
                return None, (False, False)
            return [*data, {"call_sha256": ident[0], "action": ident[1], "session": session_id, "at": now,
                            "owner_card": d.get("owner_card")}], (True, first)
        try:
            delivered, first = _mutate(path, apply)
        except RootCapError:
            continue
        if delivered:
            out.append(_notice_text(d))
            if first and log:
                log({"event": "root_cap_restore_due", "rule": "root_cap", **d})
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Register a frozen root-cap contract with the coach guard.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("--store", required=True)
    reg.add_argument("--target-home", required=True)
    reg.add_argument("--key", default=GUARDED_KEYS[0])
    reg.add_argument("--value", type=int, required=True)
    reg.add_argument("--contract-ref", required=True)
    reg.add_argument("--approval-session", required=True)
    reg.add_argument("--approval-message", type=int, required=True)
    reg.add_argument("--owner-card", required=True)
    dec = sub.add_parser("declare", help="declare a freeze by explicit references (verified now and at every guarded call)")
    dec.add_argument("--target-home", required=True)
    dec.add_argument("--key", default=GUARDED_KEYS[0])
    dec.add_argument("--value", type=int, required=True)
    dec.add_argument("--contract-ref", required=True)
    dec.add_argument("--owner-card", required=True)
    dec.add_argument("--id", default=None)
    dec.add_argument("--approval-session")
    dec.add_argument("--approval-message", type=int)
    dec.add_argument("--action-db")
    dec.add_argument("--operation")
    dec.add_argument("--request")
    dec.add_argument("--revision", type=int)
    dec.add_argument("--gateway")
    dec.add_argument("--profile")
    dec.add_argument("--stored-session")
    dec.add_argument("--native-message", type=int, help="state.db id of the canonical user decision row in the owner chat")
    dec.add_argument("--state-db", help="state.db that holds the native decision row (default: <target-home>/state.db)")
    dec.add_argument("--mapping-phrase")
    dec.add_argument("--mapping-rationale")
    dec.add_argument("--mapping-reviewed-by")
    lst = sub.add_parser("list")
    lst.add_argument("--store", required=True)
    lst.add_argument("--target-home", default=None, help="root home whose shared projection is listed too")
    ns = ap.parse_args(argv)
    if ns.cmd == "list":
        for d in _dirs(ns.store, [ns.target_home or _env_home()]):
            for r in _read_list(Path(d) / CONTRACTS_FILE):
                print(json.dumps({**r, "state": _contract_state(r)[0], "projection": str(d)}))
            for r in _read_list(Path(d) / SOURCES_FILE):
                print(json.dumps({**r, "source": True, "projection": str(d)}))
        return 0
    if ns.cmd == "declare":
        if ns.action_db:
            authority = {"type": "action_operation", "db": ns.action_db, "operation_id": ns.operation,
                         "request_id": ns.request, "revision": ns.revision,
                         "owner": {"gatewayId": ns.gateway, "profile": ns.profile, "storedSessionId": ns.stored_session},
                         "native_message_id": ns.native_message, **({"state_db": ns.state_db} if ns.state_db else {})}
            mapping = {"phrase": ns.mapping_phrase, "rationale": ns.mapping_rationale,
                       "reviewed_by": ns.mapping_reviewed_by}
        else:
            authority = {"type": "native_message", "session": ns.approval_session, "message_id": ns.approval_message}
            mapping = None
        try:
            rec = declare_source(None, target_home=ns.target_home, key=ns.key, frozen_value=ns.value,
                                 contract_ref=ns.contract_ref, owner_card=ns.owner_card, authority=authority,
                                 mapping=mapping, source_id=ns.id)
        except RootCapError as exc:
            print(f"refused: {exc}")
            return 1
        print(json.dumps(rec, indent=2))
        return 0
    try:
        rec = register_contract(ns.store, target_home=ns.target_home, key=ns.key, frozen_value=ns.value,
                                contract_ref=ns.contract_ref, approval_session=ns.approval_session,
                                approval_message_id=ns.approval_message, owner_card=ns.owner_card)
    except RootCapError as exc:
        print(f"refused: {exc}")
        return 1
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
