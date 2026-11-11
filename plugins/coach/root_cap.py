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
import datetime
import glob
import hashlib
import importlib.util
import json
import os
import re
import shlex
import sqlite3
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
    """Atomic write, then read back and compare: a record is not admitted until it reads back exactly."""
    p = Path(path)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, p)
        if _read_list(p) != json.loads(json.dumps(data)):
            raise RootCapError(f"{p.name} read-back differs")
    except OSError as exc:
        raise RootCapError(f"{p.name} not writable ({type(exc).__name__})")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


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
    try:
        raw = artifact.read_bytes()
    except OSError as exc:
        raise RootCapError(f"contract artifact unreadable ({type(exc).__name__})")
    if len(raw) > 1_000_000 or not re.search(rf"{re.escape(_leaf(key))}[^\n]{{0,80}}\b{value}\b",
                                             raw.decode("utf-8", "replace")):
        raise RootCapError(f"contract artifact does not state {key} = {value}")
    acc = _acceptance()
    db = Path(state_db) if state_db else home / "state.db"
    ok, why = acc.verify_citation(int(approval_message_id), approval_session, db)
    if not ok:
        raise RootCapError(f"approval is not native human authority: {why}")
    conn = acc._ro_connect(db)
    try:
        content, kind = conn.execute("select content, display_kind from messages where id=?",
                                     (int(approval_message_id),)).fetchone()
    finally:
        conn.close()
    typed = (acc.typed_text(content, kind) or "").lower()
    if _leaf(key) not in typed or not re.search(rf"\b{value}\b", typed):
        raise RootCapError(f"approval message does not name {key} and the frozen value {value}")
    status = owner_status(owner_card, home)
    if status not in OPEN_STATES:
        raise RootCapError(f"owner card {owner_card} is not an open card (status {status!r})")
    rec = {"target_home": str(home), "key": key, "frozen_value": value, "contract_ref": str(artifact.resolve()),
           "contract_revision_digest": _sha(raw), "approval_ref": {"session": approval_session,
                                                                    "message_id": int(approval_message_id)},
           "owner_ref": {"card": owner_card}, "active": True, "registered_at": time.time()}
    path = Path(store_root) / CONTRACTS_FILE
    Path(store_root).mkdir(parents=True, exist_ok=True)
    data = _read_list(path)
    out = []
    for old in data:
        same_target = (old.get("target_home"), old.get("key")) == (rec["target_home"], key)
        if same_target and old.get("active") and old.get("contract_revision_digest") != rec["contract_revision_digest"]:
            old = {**old, "active": False, "superseded_by_revision": rec["contract_revision_digest"]}
        if same_target and old.get("contract_revision_digest") == rec["contract_revision_digest"]:
            continue
        out.append(old)
    out.append(rec)
    _write_list(path, out)
    return rec


def _contract_state(rec):
    """(state, why): active | lapsed | ambiguous. Unprovable means ambiguous, never active-and-unguarded."""
    if not rec.get("active"):
        return "lapsed", "inactive"
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


def _split(command):
    """Words per simple command (separators ; & | newline ( ) ), plus redirect targets. Heredoc bodies are data."""
    lines, delimiter = [], None
    for line in command.replace("\\\n", " ").splitlines():
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        lines.append(line)
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        if m:
            delimiter = m.group(1)
    text = "\n".join(lines).replace("`", ";")
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
_AUTH = re.compile(r"\b(authori[sz]e|authori[sz]ed|approve|approved|permit|allow)\b")
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


def _decision(texts, rec, new_value):
    """Latest surviving native decision for `new_value` against this contract revision (None / dict)."""
    leaf = _leaf(rec["key"])
    digest = rec["contract_revision_digest"]
    current = None
    for raw in texts:
        if not isinstance(raw, str):
            continue
        text = raw.lower()
        for s in _sentences(text):
            if "?" in s or re.match(r"^(if|what if|should i|can i|could i|may i|would you|shall i)\b", s):
                continue
            names_key = leaf in s
            if _NEG.search(s) and (names_key or re.search(r"\b(revoke|rescind|withdraw|retract|never mind)\b", s)):
                current = None
                continue
            if not names_key or not _AUTH.search(s):
                continue
            m = re.search(r"\bfrom\s+(\d+)\s+to\s+(\d+)\b", s)
            if m and int(m.group(1)) == rec["frozen_value"]:
                target = int(m.group(2))
            elif not m and len(digest) >= 12 and digest[:12] in text:
                m2 = re.search(r"(?:=|\bto\b|->)\s*(\d+)\b", s)
                target = int(m2.group(1)) if m2 else None
            else:
                target = None
            if target is None or target != new_value:
                continue
            current = {"text": raw, "temporary": "temporar" in text, "sha": _sha(raw)}
    return current


def _temporary_terms(decision, rec, session_id, now):
    """(terms, missing[]): owner/restore provenance parsed from the user's own decision text."""
    text = decision["text"].lower()
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
            status = owner_status(owner["card"], rec["target_home"])
        except RootCapError:
            status = "unreadable"
        if status is None:
            missing.append(f"owner card {owner['card']} does not exist")
        elif status in TERMINAL_STATES:
            missing.append(f"owner card {owner['card']} is already finished")
        elif status not in OPEN_STATES:
            missing.append(f"owner card {owner['card']} state could not be verified")
    restore = next((s for s in _sentences(text) if "restore" in s), "")
    restore_to = re.findall(r"\bto\s+(\d+)\b", restore)
    restore_value = int(restore_to[-1]) if restore_to else None
    expiry = expiry_raw = _ISO.search(decision["text"][max(decision["text"].lower().find("restore"), 0):]) \
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
    """(records for this exact home+key, ambiguity reason | None)."""
    recs = [r for r in contracts if r.get("key") == key and _real(r.get("target_home", "")) == _real(home)]
    live = []
    for r in recs:
        state, why = _contract_state(r)
        if state != "lapsed":
            live.append((r, state, why))
    return live


def check(tool_name, args, session_id, store_root, user_texts=(), now=None):
    """None to allow, or {"message", "event"} to block. Never raises for ordinary input."""
    name = str(tool_name).rsplit("__", 1)[-1]
    if name not in ("terminal", "execute_code") or not isinstance(args, dict):
        return None
    path = Path(store_root) / CONTRACTS_FILE
    if not path.exists():
        return None
    now = now if now is not None else time.time()
    parsed = parse(name, args)
    key = GUARDED_KEYS[0]
    wants = [s for s in parsed.sets if s["key"] in GUARDED_KEYS]
    if not (wants or parsed.raw or parsed.dynamic or parsed.cfg_api or parsed.broken):
        return None
    try:
        contracts = _read_list(path)
    except RootCapError as exc:
        return _refusal(None, f"the contract registry is unreadable ({exc})", key, None, session_id)
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
        decision = _decision([t for t in user_texts if isinstance(t, str)], rec, value)
        if decision is None:
            return _refusal(rec, "no native user decision authorizes this exact change", key, value, session_id)
        terms = {"owner_ref": None, "restore_value": None, "restore_trigger": None}
        if decision["temporary"]:
            terms, missing = _temporary_terms(decision, rec, session_id, now)
            if missing:
                return _refusal(rec, "temporary exception lacks " + "; ".join(missing), key, value, session_id)
        try:
            _admit(store_root, rec, decision, terms, value, session_id, args)
        except RootCapError as exc:
            return _refusal(rec, f"exception provenance could not be persisted and read back ({exc})",
                            key, value, session_id)
        return None

    for w in wants:
        if w["home"] is None:
            for home in homes:
                out = _refusal(_group(contracts, home, key)[0][0] if _group(contracts, home, key) else None,
                               "the target home could not be resolved (ambiguous)", key, None, session_id)
                return out
            continue
        if _real(w["home"]) not in homes:
            continue
        out = one(w["home"], _intval(w["value"]) if w["value"] is not None else None, True)
        if out:
            return out
    if parsed.raw or parsed.dynamic or parsed.cfg_api or parsed.broken:
        raw_homes = {Path(p).parent.as_posix() for r in parsed.raw for p in r["paths"] if p.endswith("config.yaml")}
        ambiguous = parsed.dynamic or parsed.cfg_api or parsed.broken
        for home in homes:
            if ambiguous or _real(home) in {_real(h) for h in raw_homes}:
                live = _group(contracts, home, key)
                if live:
                    return _refusal(live[0][0], "a direct or non-literal config write names this key and its "
                                    "value cannot be verified; use `hermes config set` with an authorized value",
                                    key, None, session_id)
    return None


def _restore_conflict(rec, store_root, key, session_id):
    try:
        excs = [e for e in _read_list(Path(store_root) / EXCEPTIONS_FILE)
                if e.get("contract_revision_digest") == rec["contract_revision_digest"] and e.get("state") == "active"]
    except RootCapError as exc:
        return _refusal(rec, f"exception registry unreadable ({exc})", key, rec["frozen_value"], session_id)
    if not excs:
        return None
    last = excs[-1]
    try:
        current = config_value(rec["target_home"], key)
    except Exception as exc:  # unreadable config: do not overwrite blindly
        return _refusal(rec, f"current value unreadable ({type(exc).__name__})", key, rec["frozen_value"], session_id)
    if current != last["override_value"]:
        return _refusal(rec, f"restore refused: the current value {current} differs from the temporary override "
                        f"{last['override_value']}; a newer decision may exist, so it is not overwritten",
                        key, rec["frozen_value"], session_id)
    return None


def _admit(store_root, rec, decision, terms, value, session_id, args):
    path = Path(store_root) / EXCEPTIONS_FILE
    call = _sha(json.dumps(args, sort_keys=True, default=str))
    data = _read_list(path)
    entry = {"kind": "temporary" if decision["temporary"] else "permanent", "key": rec["key"],
             "target_home": rec["target_home"], "contract_ref": rec["contract_ref"],
             "contract_revision_digest": rec["contract_revision_digest"], "approval_ref": rec["approval_ref"],
             "original_value": rec["frozen_value"], "override_value": value,
             "restore_value": terms["restore_value"], "restore_trigger": terms["restore_trigger"],
             "owner_ref": terms["owner_ref"], "decision_sha256": decision["sha"], "session": session_id,
             "call_sha256": call, "admitted_at": time.time(), "state": "admitted"}
    for old in data:
        if (old.get("decision_sha256"), old.get("session"), old.get("override_value"), old.get("call_sha256"),
                old.get("state")) == (entry["decision_sha256"], session_id, value, call, "admitted"):
            return old
    Path(store_root).mkdir(parents=True, exist_ok=True)
    _write_list(path, [*data, entry])
    return entry


def after(tool_name, args, session_id, store_root, now=None):
    """Post-call readback: admitted exceptions become active/applied only if the real config now has the value."""
    path = Path(store_root) / EXCEPTIONS_FILE
    if not path.exists():
        return
    now = now if now is not None else time.time()
    data = _read_list(path)
    call = _sha(json.dumps(args, sort_keys=True, default=str)) if isinstance(args, dict) else None
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
    if changed:
        _write_list(path, data)
    if applied:
        cpath = Path(store_root) / CONTRACTS_FILE
        contracts = _read_list(cpath)
        for c in contracts:
            for e in applied:
                if c.get("contract_revision_digest") == e["contract_revision_digest"] and c.get("active"):
                    c["active"] = False
                    c["superseded_by"] = {"decision_sha256": e["decision_sha256"], "value": e["override_value"],
                                          "session": e["session"], "at": now}
        _write_list(cpath, contracts)


def due_restores(store_root, now=None):
    """Active temporary exceptions whose expiry passed or whose owner card finished and that still hold the
    override value (compare-and-set: a value a later decision changed is reported as a conflict, not restored).
    Pure read; the existing owner performs the recovery action."""
    now = now if now is not None else time.time()
    out = []
    for e in _read_list(Path(store_root) / EXCEPTIONS_FILE):
        if e.get("state") != "active":
            continue
        trig = e.get("restore_trigger") or {}
        due = bool(trig.get("expires_at")) and (_parse_iso(trig["expires_at"]) or 0) <= now
        if trig.get("owner_terminal") and not due:
            try:
                due = owner_status(e["owner_ref"]["card"], e["target_home"]) in TERMINAL_STATES
            except RootCapError:
                due = False
        if due:
            try:
                current = config_value(e["target_home"], e["key"])
            except Exception:
                current = "unreadable"
            out.append({"key": e["key"], "target_home": e["target_home"], "restore_value": e["restore_value"],
                        "action": "restore" if current == e["override_value"] else "conflict", "current": current})
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
    lst = sub.add_parser("list")
    lst.add_argument("--store", required=True)
    ns = ap.parse_args(argv)
    if ns.cmd == "list":
        for r in _read_list(Path(ns.store) / CONTRACTS_FILE):
            print(json.dumps({**r, "state": _contract_state(r)[0]}))
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
