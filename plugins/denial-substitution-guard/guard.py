"""Persistent denial mistake guard. Not a sandbox or authority verifier.

M1 containment: no time, identity, writable grant, or owner-terminal exemption.
Parser/tamper coverage remains bounded; see SPEC.md and the owner acceptance ledger.
"""
from __future__ import annotations

import ast
import fnmatch
import contextlib
from collections import deque
import fcntl
import hashlib
import json
import math
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any, Optional

MAX_LEDGER_BYTES = 8 * 1024 * 1024
MAX_LEDGER_ROWS = 20000
MAX_OVERFLOW_EVIDENCE = 128
_PROD = False
_ROOT = None
_UNHEALTHY = {}
_OVERFLOW = {}
SHELL_TOOLS = {"terminal", "process_manage", "process", "tailnet_exec", "run_in_user_terminal"}
PY_TOOLS = {"execute_code"}
NESTED_TOOLS = {"tool_call"}
FILE_TOOLS = {"write_file", "patch"}
PROTECTED = ("denial-ledger", "denial-grants")
# AWS CLI v2 global options, from the installed awscli/data/cli.json (2.40.1).
GLOBAL_VALUE_OPTS = {"--profile", "--region", "--endpoint-url", "--output", "--query", "--cli-read-timeout",
                     "--cli-connect-timeout", "--ca-bundle", "--color", "--cli-binary-format", "--cli-error-format"}
GLOBAL_FLAG_OPTS = {"--debug", "--no-verify-ssl", "--no-paginate", "--version", "--no-sign-request", "--no-cli-pager",
                    "--cli-auto-prompt", "--no-cli-auto-prompt", "--help"}
READERS = {"cat", "sed", "grep", "rg", "head", "tail", "less", "jq", "awk", "ls", "echo", "printf", "wc", "stat"}
WRITE_SHELL = re.compile(r"(>|\btee\b|\brm\b|\bmv\b|\bcp\b|\bsed\s+-i|\btruncate\b|\bdd\b|\binstall\b|\bpython3?\b|\bperl\b"
                         r"|\bunlink\b|\brmdir\b|(?<![\w-])touch\b|\bshred\b|(?<![\w-])ln\b|\bch(?:mod|own|grp)\b|\bsudoedit\b|\brsync\b|-delete\b)")
WRITE_PY = re.compile(r"write|open\s*\(|unlink|remove|rename|replace|truncate", re.I)
DENIAL_START = re.compile(r"User:\s*(arn:\S+)\s+is not authorized to perform")
POSITIVE_READS = {("ssm", "getcommandinvocation"), ("sts", "getcalleridentity"),
                  ("ec2", "describeinstances"), ("ssm", "listcommands")}


def _account_root():
    try:
        from hermes_constants import account_default_hermes_home
        return account_default_hermes_home().resolve()
    except ImportError:
        import pwd
        return (Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes").resolve()


def resolve_root():
    global _ROOT
    if _ROOT is None:
        _ROOT = _account_root()
    return _ROOT


def _assert_writable_root(root):
    root, account = Path(root).resolve(), _account_root()
    if not _PROD and (root == account or account in root.parents):
        # Explicit synthetic scratch roots are the only exception, never profiles.
        scratch = account / "cache" / "scratch"
        if scratch not in root.parents:
            raise PermissionError("unregistered guard cannot write account ledger or descendants")


def _root(root):
    return Path(root).resolve() if root is not None else resolve_root()


def ledger_path(root=None):
    return _root(root) / "denial-ledger.jsonl"


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _fingerprint(tool, args):
    texts = _call_texts(tool, args)
    return _digest(json.dumps(texts, ensure_ascii=True, separators=(",", ":")))


_RESULT_MAX_DEPTH = 12
_RESULT_ECHO_KEYS = {"command"}  # process_manage echoes its input command; not an observation


def iter_result_texts(value, depth=0, budget=None):
    """Every string leaf of a post-hook result, JSON-decoding embedded JSON.

    Handler results are JSON strings; the registry also admits multimodal
    dicts. All dict values (except an echoed input command) and list items
    are visited; beyond the depth bound the rest is serialized and scanned as
    raw text. Yielded leaf text is charged in characters against one bound;
    any single string over it, or exceeding it in total, raises ValueError."""
    budget = [MAX_LEDGER_BYTES] if budget is None else budget
    if isinstance(value, str):
        if len(value) > MAX_LEDGER_BYTES:
            raise ValueError("oversized denial result")
        if value.lstrip()[:1] in ("{", "[", '"') and depth < _RESULT_MAX_DEPTH:
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                decoded = None
            if isinstance(decoded, (dict, list, str)):
                yield from iter_result_texts(decoded, depth + 1, budget)
                return
        budget[0] -= len(value)
        if budget[0] < 0:
            raise ValueError("oversized denial result")
        yield value
    elif isinstance(value, (dict, list, tuple)):
        if depth >= _RESULT_MAX_DEPTH:
            try:
                yield from iter_result_texts(json.dumps(value, default=str), _RESULT_MAX_DEPTH, budget)
            except RecursionError:
                raise ValueError("result nesting bound")
            return
        items = (v for k, v in value.items() if k not in _RESULT_ECHO_KEYS) if isinstance(value, dict) else value
        for item in items:
            yield from iter_result_texts(item, depth + 1, budget)


# AWS API error codes that report an authorization refusal. Throttling,
# expired/invalid credentials and validation errors are not denials.
API_DENIAL = re.compile(r"An error occurred \((AccessDenied(?:Exception)?|UnauthorizedOperation|Unauthorized(?:Access|Exception)?"
                        r"|AuthorizationError(?:Exception)?|Forbidden(?:Exception)?|403|NotAuthorized(?:Exception)?)\)"
                        r" when calling the ([A-Za-z0-9]+) operation")


def _api_actions(operation, calls):
    """IAM-prefixed actions for an API operation named in an error.

    A call whose own operation, or one of whose listed IAM candidates, names
    the operation contributes that candidate's service (so `cloudformation
    deploy` failing PutObject gives s3:PutObject). Otherwise a single
    recognized service is assumed; several give UNKNOWN (replay only)."""
    calls = [(s.lower(), o) for s, o in calls if (s, o) != ("UNKNOWN", "UNKNOWN")]
    want = _norm(operation)
    named = set()
    for s, o in calls:
        if o == want:
            named.add(_IAM_PREFIX.get(s, s))
        for prefix in _iam_prefixes(s):
            named.update(p for p, a in _IAM_OPS.get((prefix, o), ()) if a == want)
    if named:
        return [f"{p}:{operation}" for p in sorted(named)]
    services = {_IAM_PREFIX.get(s, s) for s, _ in calls}
    return [f"{services.pop()}:{operation}"] if len(services) == 1 else ["UNKNOWN"]


def parse_denials(result, calls=()):
    for stream in iter_result_texts(result):
        for line in stream.splitlines():
            starts = list(DENIAL_START.finditer(line))
            for i, match in enumerate(starts):
                text = line[match.end():starts[i + 1].start() if i + 1 < len(starts) else len(line)]
                action = re.match(r":\s*([A-Za-z0-9-]+:[A-Za-z0-9*]+)", text)
                resource = re.search(r"\bon resource:\s*(\S+)", text)
                explicit = True if "explicit deny" in text else False if "no identity-based policy allows" in text else None
                yield {"principal": match.group(1).rstrip(".,\"'\\"),
                       "action": action.group(1) if action else "UNKNOWN",
                       "resource": resource.group(1).rstrip(".,\"'\\") if resource else "UNKNOWN",
                       "explicit": explicit, "truncated": bool(re.search(r"…|truncated", text)),
                       "evidence_sha256": _digest(match.group(0) + text)}
            if starts:
                continue
            for match in API_DENIAL.finditer(line):
                for action in _api_actions(match.group(2), calls):
                    row = {"principal": "UNKNOWN", "action": action, "resource": "UNKNOWN",
                           "explicit": True if "explicit deny" in line else None, "truncated": False,
                           "evidence_sha256": _digest(match.group(0))}
                    if action != "UNKNOWN":
                        row["action_kind"] = "api"
                    yield row


def parse_denial(text):
    return next(parse_denials(text), None)


def invocation_identity(text):
    m = re.search(r"token-broker\s+exec\b.*?--scope[ =]['\"]?([\w./:-]+)", text or "", re.S)
    if m:
        return f"broker:{m.group(1)}"
    m = re.search(r"--profile[ =]['\"]?([\w./:@-]+)", text or "") or re.search(r"AWS_PROFILE=['\"]?([\w./:@-]+)", text or "")
    return f"profile:{m.group(1)}" if m else "ambient"


def _norm(op):
    return re.sub(r"[-_]", "", op).lower()


def _ansi_c_end(text, i):
    """Index of the quote closing the $'..' word starting at i, or -1."""
    j = i + 2
    while j < len(text):
        if text[j] == '\\': j += 2; continue
        if text[j] == "'": return j
        j += 1
    return -1


def _substitutions(text):
    """Extract executable substitutions, never single-quoted documentation.

    Replace their output with a dynamic marker, not an invented resource value.
    This is a bounded shell subset, not evaluation or a complete shell parser.
    """
    outer, children = [], []
    i, quote, word_started = 0, None, False
    while i < len(text):
        c = text[i]
        # A comment starts only at an unquoted word boundary. Its quotes and
        # substitutions are data and cannot affect the following command.
        if c == "#" and quote is None and not word_started:
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        if c == "\\" and quote != "'" and i + 1 < len(text):
            if text[i + 1] != "\n": word_started = True
            outer.append(text[i:i + 2]); i += 2; continue
        if c == "$" and quote is None and text.startswith("$'", i) and not _legacy() and _ansi_c_end(text, i) >= 0 and _precise_used():
            end = _ansi_c_end(text, i)
            word_started = True
            outer.append(text[i:end + 1]); i = end + 1; continue
        if c in "'\"" and (quote is None or quote == c):
            word_started = True
            quote = None if quote else c
            outer.append(c); i += 1; continue
        if quote != "'" and (text.startswith("$(", i) or c == "`"):
            backtick = c == "`"
            start = i + (1 if backtick else 2)
            j, level, inner_quote, inner_word = start, 1, None, False
            while j < len(text):
                ch = text[j]
                if ch == "#" and inner_quote is None and not inner_word:
                    end = text.find("\n", j)
                    j = len(text) if end < 0 else end
                    continue
                if ch == "\\" and inner_quote != "'":
                    if j + 1 < len(text) and text[j + 1] != "\n": inner_word = True
                    j += 2; continue
                if ch == "$" and inner_quote is None and text.startswith("$'", j) and not _legacy() and _ansi_c_end(text, j) >= 0 and _precise_used():
                    inner_word = True
                    j = _ansi_c_end(text, j) + 1; continue
                if ch in "'\"" and (inner_quote is None or inner_quote == ch):
                    inner_word = True
                    inner_quote = None if inner_quote else ch
                    j += 1; continue
                elif not inner_quote:
                    if backtick and ch == "`":
                        break
                    if not backtick:
                        if ch == "(": level += 1
                        if ch == ")": level -= 1
                        if not level: break
                inner_word = bool(inner_quote) or ch not in " \t\r\n;&|()<>"
                j += 1
            children.append(text[start:j])
            # Output of a substitution whose code names a protected fragment
            # is an uncertain word, never an invented or safe value.
            outer.append("$__DSG_RISKY" if any(f in text[start:j] for f in _PROTECTED_FRAGMENTS) else "$DYNAMIC")
            word_started = True
            i = min(j + 1, len(text)); continue
        word_started = bool(quote) or c not in " \t\r\n;&|()<>"
        outer.append(c); i += 1
    return "".join(outer), children


def _shell_segments(text):
    # Keep lexical control syntax separate from decoded argument text. Quoted
    # ';' and newlines are data; unquoted newlines terminate commands.
    segment, word = [], []
    quote, started, i = None, False, 0
    while i < len(text):
        c = text[i]
        if c == "\\" and quote != "'":
            if i + 1 == len(text): raise ValueError("trailing escape")
            following = text[i + 1]
            if following != "\n":
                if quote == '"' and following not in '$`"\\': word.append(c)
                word.append(following); started = True
            i += 2; continue
        if quote:
            if c == quote: quote = None
            else: word.append(c)
            i += 1; continue
        if c in "'\"":
            quote, started = c, True
        elif c == "#" and not started:
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif c in " \t\r\n;&|()<>":
            if started:
                segment.append("".join(word)); word, started = [], False
            if c in "\n;&|()":
                if segment: yield segment
                segment = []
            elif c in "<>": segment.append(c)
        else:
            word.append(c); started = True
        i += 1
    if quote: raise ValueError("unclosed quote")
    if started: segment.append("".join(word))
    if segment: yield segment


def _abbreviates(word, options):
    # argparse may accept an unambiguous prefix of a long option; treat any
    # prefix of a target-selecting option as that option (conservative).
    return word.startswith("--") and len(word) > 2 and any(o.startswith(word) for o in options)


def _resource_values(svc, op, argv):
    if (svc, op) != ("ssm", "sendcommand"):
        return None
    if any(_abbreviates(t.split("=", 1)[0], ("--cli-input-json", "--cli-input-yaml", "--targets")) for t in argv):
        return None
    if any(t.split("=", 1)[0] != "--instance-ids" and _abbreviates(t.split("=", 1)[0], ("--instance-ids",)) for t in argv):
        return None
    values = []
    for i, token in enumerate(argv):
        if token.startswith("--instance-ids="):
            values.append(token.split("=", 1)[1])
        elif token == "--instance-ids":
            for value in argv[i + 1:]:
                if value.startswith("--"): break
                values.append(value)
    # Only explicit instance ids can prove a different SSM instance. All other
    # syntax (including output substitution, lists, files and variables) is unknown.
    # A literal alphanumeric id is passed verbatim to AWS; compared case-insensitively.
    return [v.lower() for v in values] if values and all(re.fullmatch(r"i-[0-9A-Za-z]+", v) for v in values) else None


_AWS_WRAPPERS = {"env", "sudo", "doas", "command", "exec", "nohup", "nice", "ionice", "timeout", "stdbuf", "setsid",
                 "time", "xargs", "flock", "unbuffer", "builtin", "chronic", "taskset", "chrt", "caffeinate",
                 "watch", "strace", "ltrace", "runuser", "nsenter", "unshare", "chroot", "systemd-run", "script",
                 "ssh", "uv", "uvx", "pipx", "poetry", "pdm", "conda", "mamba", "micromamba"}
# Wrappers whose arguments are joined and run by a (remote) shell.
_AWS_STRING_WRAPPERS = {"ssh", "watch"}
_AWS_RESERVED = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!", "{", "}", "time", "in"}
_AWS_HEADS = {"aws", "token-broker", "bash", "sh", "zsh", "eval"}


def _is_head(name):
    return name in _AWS_HEADS or re.fullmatch(r"python[0-9.]*", name) is not None


def _python_cli(args, depth):
    """`python [opts] -m awscli ARGS` is the AWS CLI; `python [opts] -c CODE`
    runs CODE (analyzed like execute_code). Other script runs are opaque."""
    skip = False
    for k, word in enumerate(args):
        if skip:
            skip = False; continue
        if word in ("-W", "-X", "--check-hash-based-pycs"):
            skip = True; continue
        if word == "-m" and k + 1 < len(args):
            if args[k + 1] in ("awscli", "awscli.__main__"):
                return _shell_invocations(shlex.join(["aws"] + args[k + 2:]), depth + 1)
            return []
        if word.startswith("-m") and len(word) > 2:
            return _shell_invocations(shlex.join(["aws"] + args[k + 1:]), depth + 1) if word[2:] == "awscli" else []
        if word == "-c" and k + 1 < len(args):
            return _py_invocations(args[k + 1], depth + 1)
        if not word.startswith("-"):
            return []
    return []


# Programs that run text they read from stdin, a here-string, process
# substitution or their arguments (review-cumulative-2 B1).
_STDIN_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "ash", "fish", "busybox", "eval", "source"}
_FED_TEXT = re.compile(r"\baws(?:cli)?\b|\bpython[0-9.]*\b|\btoken-broker\b")


def _fed_consumer(outer, segments):
    if "<<<" in outer or "<(" in outer or ">(" in outer:
        return True
    return any(os.path.basename(w) in _STDIN_SHELLS for seg in segments for w in seg) or \
        any(seg and seg[0] == "." for seg in segments)


def _fed_invocations(tokens, depth):
    """With a stdin/argument shell consumer present anywhere in the text, data
    words may become code: a word holding an AWS-shaped command line, or the
    words after an AWS/interpreter head in a data command (`echo aws ...`)."""
    out = []
    for k, w in enumerate(tokens):
        if re.search(r"\s", w) and _FED_TEXT.search(w):
            out.extend(_shell_invocations(w, depth + 1, fed=True))
    k = next((k for k in range(1, len(tokens)) if _is_head(os.path.basename(tokens[k]))), None)
    if k is not None and not _is_head(os.path.basename(tokens[0])):
        out.extend(_shell_invocations(shlex.join(tokens[k:]), depth + 1, fed=True))
    return out


def _shell_invocations(text, depth=0, fed=False):
    if depth > 6 or len(text) > 262144:
        raise ValueError("shell analysis bound")
    outer, children = _substitutions(text)
    segments = list(_shell_segments(outer))
    fed = fed or _fed_consumer(outer, segments)
    out = [call for child in children for call in _shell_invocations(child, depth + 1, fed=fed)]
    for tokens in segments:
        if fed and _FED_TEXT.search(" ".join(tokens)):
            out.extend(_fed_invocations(tokens, depth))
        i = 0
        while i < len(tokens):
            t = tokens[i]
            name = os.path.basename(t)
            if re.match(r"^[A-Za-z_][A-Za-z_0-9]*\+?=", t) or t in _AWS_RESERVED:
                i += 1; continue
            if t == "function" and i + 1 < len(tokens):
                i += 2; continue
            if name in _AWS_WRAPPERS:
                # Wrapper option grammar differs per tool; any later word that
                # is an AWS/interpreter head is taken as the wrapped command
                # (conservative: an option value spelled `aws` may also match).
                # A dynamic later word may be the wrapped command: unknown.
                rest = tokens[i + 1:]
                if any("$" in w or "`" in w for w in rest) and re.search(r"\baws(?:cli)?\b", text):
                    raise ValueError("dynamic wrapped command word")
                if name == "env":
                    for k, w in enumerate(rest):
                        split = w[2:] if w.startswith("-S") and len(w) > 2 else w.split("=", 1)[1] if w.startswith("--split-string=") else \
                            rest[k + 1] if w in ("-S", "--split-string") and k + 1 < len(rest) else None
                        if split is not None:
                            out.extend(_shell_invocations(" ".join([split] + rest[k + 2 if w in ("-S", "--split-string") else k + 1:]), depth + 1))
                if name in _AWS_STRING_WRAPPERS:
                    # The joined remaining words are shell text: any word that
                    # holds a space-separated aws command starts that text.
                    for k, w in enumerate(rest):
                        if re.search(r"\s", w) and re.search(r"\baws(?:cli)?\b|\bpython", w):
                            out.extend(_shell_invocations(" ".join(rest[k:]), depth + 1)); break
                # Interpreter words are analyzed in addition to (never instead
                # of) the first AWS-family head, so an option value spelled
                # `python` cannot hide a later aws (bounded: first 4).
                for k in [k for k in range(i + 1, len(tokens)) if re.fullmatch(r"python[0-9.]*", os.path.basename(tokens[k]))][:4]:
                    out.extend(_python_cli(tokens[k + 1:], depth))
                j = next((k for k in range(i + 1, len(tokens)) if os.path.basename(tokens[k]) in _AWS_HEADS), None)
                if j is None: break
                i = j; continue
            if "$" in t or "`" in t:
                # A dynamic command word may be aws: unknown, never 'no call'.
                if re.search(r"\baws(?:cli)?\b", text): raise ValueError("dynamic shell command word")
                break
            if name == "eval":
                out.extend(_shell_invocations(" ".join(tokens[i + 1:]), depth + 1)); break
            if name == "token-broker" and "--" in tokens[i + 1:]:
                i = tokens.index("--", i + 1) + 1; continue
            if name in {"bash", "sh", "zsh"}:
                for j in range(i + 1, len(tokens) - 1):
                    if tokens[j] in {"-c", "-lc", "-ec"}:
                        out.extend(_shell_invocations(tokens[j + 1], depth + 1)); break
            if re.fullmatch(r"python[0-9.]*", name):
                out.extend(_python_cli(tokens[i + 1:], depth))
            if name == "aws":
                for svc, op, rest, clean in _aws_positionals(tokens[i + 1:]):
                    # Only operation words can name a resource selector, and only
                    # when no pre-operation word had an uncertain reading.
                    out.append((svc, op, _resource_values(svc, op, rest) if clean else None))
            # Never interpret an ordinary command's literal argv as executable.
            break
    return out


_AWS_MAX_BRANCHES = 6


def _aws_option_arity(word):
    """Possible value counts for an option word before the operation.

    The CLI parses global optionals anywhere (argparse intersperses them).
    Unknown or ambiguous spellings (including argparse prefix abbreviations)
    yield both readings, so a misreading can only add a candidate call."""
    if "=" in word or not word.startswith("--"):
        return (0,) if word.split("=", 1)[0] in GLOBAL_VALUE_OPTS | GLOBAL_FLAG_OPTS else (0, None)
    if word in GLOBAL_VALUE_OPTS: return (1,)
    if word in GLOBAL_FLAG_OPTS: return (0,)
    matches = {1 if o in GLOBAL_VALUE_OPTS else 0 for o in GLOBAL_VALUE_OPTS | GLOBAL_FLAG_OPTS if o.startswith(word)}
    return tuple(matches) if len(matches) == 1 else (0, 1)


def _aws_positionals(argv):
    """(service, operation, operation words, clean) readings of an `aws` argv after its head.

    clean is False when any word before the operation was not a recognized
    global option (or its value) with a single reading."""
    readings, branches = [], 0
    stack = [(0, [], True)]
    while stack:
        j, positional, clean = stack.pop()
        while j < len(argv) and len(positional) < 2:
            word = argv[j]
            if word == "--":
                taken = argv[j + 1:j + 1 + 2 - len(positional)]
                positional.extend(taken)
                j += 1 + len(taken); clean = False; break
            if word.startswith("-") and word != "-":
                arity = _aws_option_arity(word)
                if arity == (1,) and j + 1 < len(argv) and argv[j + 1].startswith("-"):
                    arity = (0, 1)  # argparse refuses an option-like value: uncertain
                if None in arity:
                    clean = False  # unknown single word, e.g. -x or --unknown=v
                    arity = (0,)
                if len(arity) > 1:
                    branches += 1
                    if branches > _AWS_MAX_BRANCHES:
                        raise ValueError("aws option ambiguity bound")
                    stack.append((j + 2, list(positional), False))
                    clean = False
                j += 1 + arity[0]
                continue
            positional.append(word)
            j += 1
        if len(positional) == 2:
            readings.append((positional[0].lower(), _norm(positional[1]), argv[j:], clean))
    return readings


# IAM service prefix for AWS CLI command / boto3 client names that differ.
_IAM_PREFIX = {
    "s3api": "s3", "s3control": "s3", "stepfunctions": "states", "elbv2": "elasticloadbalancing",
    "elb": "elasticloadbalancing", "sesv2": "ses", "pinpoint-email": "ses", "apigatewayv2": "apigateway",
    "apigatewaymanagementapi": "execute-api", "configservice": "config", "opensearch": "es",
    "emr": "elasticmapreduce", "iot-data": "iot", "iot-jobs-data": "iot", "lex-models": "lex",
    "lex-runtime": "lex", "lexv2-models": "lex", "lexv2-runtime": "lex", "sagemaker-runtime": "sagemaker",
    "dynamodbstreams": "dynamodb", "mediastore-data": "mediastore", "bedrock-runtime": "bedrock",
    "bedrock-agent": "bedrock", "bedrock-agent-runtime": "bedrock", "marketplace-catalog": "aws-marketplace",
    "resourcegroupstaggingapi": "tag", "servicecatalog-appregistry": "servicecatalog",
    "timestream-query": "timestream", "timestream-write": "timestream", "sso-admin": "sso",
    "sso-oidc": "sso-oauth", "connect-contact-lens": "connect", "cloudhsmv2": "cloudhsm", "mwaa": "airflow",
    "personalize-runtime": "personalize", "personalize-events": "personalize", "forecastquery": "forecast",
    "pinpoint": "mobiletargeting", "pinpoint-sms-voice": "sms-voice", "appconfigdata": "appconfig",
    "greengrassv2": "greengrass", "amp": "aps", "deploy": "codedeploy", "kinesis-video-media": "kinesisvideo",
    "kinesis-video-archived-media": "kinesisvideo", "kinesis-video-signaling": "kinesisvideo",
    "dms": "dms", "acm-pca": "acm-pca", "meteringmarketplace": "aws-marketplace",
    "codestar-connections": "codestar-connections", "s3tables": "s3tables", "logs": "logs",
}

# (IAM prefix, normalized operation) -> IAM actions it also needs/means, where
# the API or CLI command name differs from the IAM action name. Unlisted
# operations keep their own name, which is the common AWS convention.
_S3_PUT, _S3_GET, _S3_DEL, _S3_LIST = ("s3", "putobject"), ("s3", "getobject"), ("s3", "deleteobject"), ("s3", "listbucket")
_IAM_OPS = {
    # High-level `aws s3` commands (closed set) and boto3 managed transfers.
    ("s3", "cp"): {_S3_PUT, _S3_GET, _S3_LIST}, ("s3", "mv"): {_S3_PUT, _S3_GET, _S3_DEL, _S3_LIST},
    ("s3", "rm"): {_S3_DEL, _S3_LIST}, ("s3", "sync"): {_S3_PUT, _S3_GET, _S3_DEL, _S3_LIST},
    ("s3", "mb"): {("s3", "createbucket")}, ("s3", "rb"): {("s3", "deletebucket"), _S3_DEL, _S3_LIST},
    ("s3", "ls"): {_S3_LIST, ("s3", "listallmybuckets")}, ("s3", "presign"): {_S3_GET},
    ("s3", "website"): {("s3", "putbucketwebsite")},
    ("s3", "uploadfile"): {_S3_PUT}, ("s3", "uploadfileobj"): {_S3_PUT}, ("s3", "downloadfile"): {_S3_GET},
    ("s3", "downloadfileobj"): {_S3_GET}, ("s3", "copy"): {_S3_PUT, _S3_GET},
    # s3api operations whose IAM action differs.
    ("s3", "headobject"): {_S3_GET}, ("s3", "headbucket"): {_S3_LIST}, ("s3", "listobjects"): {_S3_LIST},
    ("s3", "listobjectsv2"): {_S3_LIST}, ("s3", "listobjectversions"): {("s3", "listbucketversions")},
    ("s3", "listbuckets"): {("s3", "listallmybuckets")}, ("s3", "deleteobjects"): {_S3_DEL, ("s3", "deleteobjectversion")},
    ("s3", "copyobject"): {_S3_PUT, _S3_GET}, ("s3", "uploadpart"): {_S3_PUT}, ("s3", "uploadpartcopy"): {_S3_PUT, _S3_GET},
    ("s3", "createmultipartupload"): {_S3_PUT}, ("s3", "completemultipartupload"): {_S3_PUT},
    ("s3", "listparts"): {("s3", "listmultipartuploadparts")},
    ("s3", "listmultipartuploads"): {("s3", "listbucketmultipartuploads")},
    ("s3", "selectobjectcontent"): {_S3_GET}, ("s3", "getobjectattributes"): {_S3_GET, ("s3", "getobjectattributes")},
    ("s3", "deleteobject"): {_S3_DEL, ("s3", "deleteobjectversion")},
    ("s3", "putbucketlifecycleconfiguration"): {("s3", "putlifecycleconfiguration")},
    ("s3", "deletebucketlifecycle"): {("s3", "putlifecycleconfiguration")},
    ("s3", "putbucketnotificationconfiguration"): {("s3", "putbucketnotification")},
    ("s3", "putbucketreplication"): {("s3", "putreplicationconfiguration")},
    ("s3", "deletebucketreplication"): {("s3", "putreplicationconfiguration")},
    ("s3", "putbucketencryption"): {("s3", "putencryptionconfiguration")},
    ("s3", "deletebucketencryption"): {("s3", "putencryptionconfiguration")},
    ("s3", "deletebucketcors"): {("s3", "putbucketcors")}, ("s3", "deletebuckettagging"): {("s3", "putbuckettagging")},
    ("s3", "putpublicaccessblock"): {("s3", "putbucketpublicaccessblock")},
    ("s3", "deletepublicaccessblock"): {("s3", "putbucketpublicaccessblock")},
    ("s3", "deletebucketownershipcontrols"): {("s3", "putbucketownershipcontrols")},
    ("s3", "putobjectlockconfiguration"): {("s3", "putbucketobjectlockconfiguration")},
    ("s3", "putbucketaccelerateconfiguration"): {("s3", "putaccelerateconfiguration")},
    ("s3", "putbucketintelligenttieringconfiguration"): {("s3", "putintelligenttieringconfiguration")},
    ("s3", "deletebucketintelligenttieringconfiguration"): {("s3", "putintelligenttieringconfiguration")},
    ("s3", "putbucketinventoryconfiguration"): {("s3", "putinventoryconfiguration")},
    ("s3", "deletebucketinventoryconfiguration"): {("s3", "putinventoryconfiguration")},
    ("s3", "putbucketanalyticsconfiguration"): {("s3", "putanalyticsconfiguration")},
    ("s3", "deletebucketanalyticsconfiguration"): {("s3", "putanalyticsconfiguration")},
    ("s3", "putbucketmetricsconfiguration"): {("s3", "putmetricsconfiguration")},
    ("s3", "deletebucketmetricsconfiguration"): {("s3", "putmetricsconfiguration")},
    ("lambda", "invoke"): {("lambda", "invokefunction")},
    ("lambda", "invokewithresponsestream"): {("lambda", "invokefunction")},
    ("elasticmapreduce", "createcluster"): {("elasticmapreduce", "runjobflow")},
    ("elasticmapreduce", "terminateclusters"): {("elasticmapreduce", "terminatejobflows")},
    ("ecr", "getloginpassword"): {("ecr", "getauthorizationtoken")},
    ("ecr", "getlogin"): {("ecr", "getauthorizationtoken")},
    ("eks", "updatekubeconfig"): {("eks", "describecluster")},
    ("codeartifact", "login"): {("codeartifact", "getauthorizationtoken"), ("sts", "getservicebearertoken")},
    ("cloudformation", "deploy"): {("cloudformation", a) for a in (
        "createchangeset", "executechangeset", "createstack", "updatestack", "describestacks")} | {_S3_PUT},
    ("cloudformation", "package"): {_S3_PUT},
    ("codedeploy", "push"): {_S3_PUT, ("codedeploy", "registerapplicationrevision")},
    ("codedeploy", "deploy"): {("codedeploy", "createdeployment")},
}


# Operation sentinel for calls that may perform any action of their service.
_ANY_OP = "\x00any"


def _iam_prefixes(svc):
    svc = svc.lower()
    out = {svc, _IAM_PREFIX.get(svc, svc), re.sub(r"v\d+$", "", svc)}
    return out


def _iam_actions(svc, op):
    """IAM (prefix, normalized action) candidates one CLI/SDK call may need."""
    out = set()
    for prefix in _iam_prefixes(svc):
        out.add((prefix, op))
        out.update(_IAM_OPS.get((prefix, op), ()))
    return out


def _action_matches(row_action, calls, kind=None):
    """Whether a call may need the denied action.

    IAM rows name an IAM action. API rows (kind "api", from an `An error
    occurred (AccessDenied) when calling the X operation` line) name an API
    operation, so they match any call sharing one of that operation's IAM
    actions."""
    svc, _, op = row_action.partition(":")
    want_svc, want_op = svc.lower(), _norm(op)
    if kind == "api":
        wanted = _iam_actions(want_svc, want_op)
        prefixes = {p for p, _ in wanted}
        return any((o == _ANY_OP and not prefixes.isdisjoint(_iam_prefixes(s))) or
                   not wanted.isdisjoint(_iam_actions(s, o)) for s, o in calls)
    wild = "*" in want_op or "?" in want_op
    for s, o in calls:
        if o == _ANY_OP:
            if want_svc in _iam_prefixes(s): return True
            continue
        for prefix, action in _iam_actions(s, o):
            if prefix == want_svc and (fnmatch.fnmatchcase(action, want_op) if wild else action == want_op):
                return True
    return False


def _literal(node, depth=0):
    if depth > 12: return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str): return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left, depth + 1), _literal(node.right, depth + 1)
        if left is not None and right is not None and len(left) + len(right) <= 262144:
            return left + right
    return None


def _client_service(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "client":
        value = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "service_name"), None)
        return _literal(value)
    return None


_PY_LAUNCHERS = {
    "subprocess": {"run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"},
    "os": {"system", "popen", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe",
           "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
           "posix_spawn", "posix_spawnp"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    "asyncio.subprocess": {"create_subprocess_exec", "create_subprocess_shell"},
    "pty": {"spawn"}, "commands": {"getoutput", "getstatusoutput"},
}
_PY_LAUNCHERS["posix"] = _PY_LAUNCHERS["nt"] = _PY_LAUNCHERS["os"]
# Event-loop methods (receiver is any loop object): protocol factory first.
_PY_LOOP_LAUNCHERS = {"subprocess_exec", "subprocess_shell"}
# Generic names that need a resolved launcher binding; every other launcher
# name is matched by its final attribute whatever the receiver chain is
# (re-exports such as subprocess.os.system, asyncio.subprocess.*).
_PY_GENERIC_LAUNCHERS = {"run", "call", "spawn", "popen"}
_PY_ALL_LAUNCHERS = set().union(*_PY_LAUNCHERS.values()) | _PY_LOOP_LAUNCHERS


def _py_dotted(expr):
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr); expr = expr.value
    if isinstance(expr, ast.Name): parts.append(expr.id)
    return list(reversed(parts))


def _py_launcher_bindings(tree):
    """May-alias table: every name any import statement (any scope) binds to a
    launcher module/function. Imports are not scope-resolved, so bindings are
    unioned, never replaced: an import elsewhere cannot erase a launcher."""
    modules = {"sp": {"subprocess"}}
    functions = {}
    for module in _PY_LAUNCHERS: modules.setdefault(module, set()).add(module)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _PY_LAUNCHERS: modules.setdefault(alias.asname or alias.name, set()).add(alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            for alias in node.names:
                # `from M import os as o` / `from asyncio import subprocess as s`: a launcher
                # module re-exported by any module binds the alias to that module.
                for m in {alias.name, f"{node.module}.{alias.name}"} & set(_PY_LAUNCHERS):
                    modules.setdefault(alias.asname or alias.name, set()).add(m)
                if node.module not in _PY_LAUNCHERS: continue
                if alias.name == "*":  # wildcard import binds every launcher the module exports
                    for fn in _PY_LAUNCHERS[node.module]: functions.setdefault(fn, set()).add(fn)
                elif alias.name in _PY_LAUNCHERS[node.module]:
                    functions.setdefault(alias.asname or alias.name, set()).add(alias.name)
    return modules, functions


def _py_process_call(node, bindings):
    """Return the launcher function name, or None (ValueError if the possible bindings disagree)."""
    modules, functions = bindings
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr not in _PY_ALL_LAUNCHERS: return None
        if func.attr not in _PY_GENERIC_LAUNCHERS: return func.attr
        # Generic name: some component of the receiver chain must be a launcher module.
        chain = _py_dotted(func.value)
        for k, part in enumerate(chain):
            for m in modules.get(part, set()) | ({".".join(chain[:k + 1])} & set(_PY_LAUNCHERS)) | ({part} & set(_PY_LAUNCHERS)):
                if func.attr in _PY_LAUNCHERS.get(m, ()): return func.attr
        return None
    if isinstance(func, ast.Name) and func.id in functions:
        names = functions[func.id]
        if len(names) > 1: raise ValueError("ambiguous launcher binding")
        return next(iter(names))
    return None


def _py_launcher_argv(launcher, node):
    """Argument nodes that form the command (argv lists, variadic argv words or shell strings)."""
    args = list(node.args) + [k.value for k in node.keywords if k.arg in ("args", "command", "cmd", "argv")]
    if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
        raise ValueError("starred process arguments")  # _calls: UNKNOWN only if text names aws
    # A starred element inside an argv container has unknown cardinality.
    if any(isinstance(e, ast.Starred) for a in args if isinstance(a, (ast.List, ast.Tuple)) for e in a.elts):
        raise ValueError("starred argv element")
    executable = next((k.value for k in node.keywords if k.arg == "executable"), None)
    if executable is not None:
        # executable= replaces the program while argv[0] stays a label:
        # an aws (or unknown) program cannot be matched to an operation.
        program = _literal(executable)
        if program is None or os.path.basename(program) == "aws":
            raise ValueError("process executable override")  # _calls: UNKNOWN only if text names aws
    if launcher in ("run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput", "system",
                    "popen", "create_subprocess_shell", "spawn"):
        args = list(node.args[:1]) + [k.value for k in node.keywords if k.arg in ("args", "cmd", "command", "argv")]
        return args, False
    if launcher == "create_subprocess_exec":
        if not node.args: raise ValueError("empty process argv")
        return list(node.args), True
    if launcher == "subprocess_exec":  # loop.subprocess_exec(protocol_factory, program, *args)
        if len(node.args) < 2: raise ValueError("incomplete process argv")
        return list(node.args[1:]), True
    if launcher == "subprocess_shell":  # loop.subprocess_shell(protocol_factory, cmd)
        if len(node.args) < 2: raise ValueError("incomplete process argv")
        return [node.args[1]], False
    # exec*/spawn*/posix_spawn*: the path (file) argument is the program that
    # runs; argv[0] is only a label, so it is replaced by the path.
    args = list(node.args)
    if launcher.startswith("spawn"): args = args[1:]  # mode
    if launcher.startswith(("posix_spawn", "execv", "spawnv")):
        if len(args) < 2: raise ValueError("incomplete process argv")
        argv = args[1]
        if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
            raise ValueError("dynamic process argv")
        words = [args[0], *argv.elts[1:]]
    else:  # execl*/spawnl*: path, arg0, args..., [env]
        if launcher.endswith("e"): args = args[:-1]
        if len(args) < 2: raise ValueError("incomplete process argv")
        words = [args[0], *args[2:]]
    return words, True


def _py_invocations(code, depth=0):
    if depth > 6 or len(code) > 262144: raise ValueError("python analysis bound")
    tree = ast.parse(code)
    bindings = _py_launcher_bindings(tree)
    if re.search(r"(?:^|[\s;])(?:import\s+awscli|from\s+awscli\b)|run_module\s*\(\s*['\"]awscli|run_path\s*\([^)]*awscli", code):
        # The CLI driver run in-process: arguments are not statically mapped.
        raise ValueError("in-process awscli driver")
    if re.search(r"\baws(?:cli)?\b", code) and (any(isinstance(n, ast.ImportFrom) and n.level for n in ast.walk(tree)) or re.search(r"__import__|import_module|sys\.modules|\bgetattr\s*\(|\b(?:globals|locals|vars)\s*\(|\b__builtins__\b|\bmethodcaller\b|\battrgetter\b", code)):
        # Dynamic module/attribute access, or a relative import whose package is
        # unknown, can reach a launcher the import table cannot see.
        raise ValueError("dynamic launcher access")
    out = []
    scopes, node_scopes = {tree: {}}, {}
    scope_parent = {tree: None}
    declarations, assignments = {}, []

    def index(node, scope):
        node_scopes[node] = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            parent = scope
            while isinstance(parent, ast.ClassDef):
                parent = scope_parent[parent]
            scope_parent[node] = parent
            scopes[node] = {}
            arguments = getattr(node, "args", None)
            if arguments:
                for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
                    scopes[node].setdefault(arg.arg, set())
                for arg in (arguments.vararg, arguments.kwarg):
                    if arg: scopes[node].setdefault(arg.arg, set())
            # Only bodies execute in the new scope. Defaults, decorators,
            # annotations and bases retain their definition-time environment.
            for field, value in ast.iter_fields(node):
                target_scope = node if field == "body" else scope
                if isinstance(value, ast.AST): index(value, target_scope)
                elif isinstance(value, list):
                    for child in value:
                        if isinstance(child, ast.AST): index(child, target_scope)
            return
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                declarations[scope, name] = "global" if isinstance(node, ast.Global) else "nonlocal"
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            service = _client_service(node.value)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    scopes[scope].setdefault(target.id, set())
                    assignments.append((scope, target.id, service))
        for child in ast.iter_child_nodes(node): index(child, scope)

    index(tree, tree)
    # Declarations apply throughout a block, independent of traversal order.
    for scope, name in declarations:
        scopes[scope].pop(name, None)

    def owner(name, scope):
        declaration = declarations.get((scope, name))
        if declaration == "global": return tree
        if declaration == "nonlocal":
            parent = scope_parent[scope]
            while parent is not None and parent is not tree:
                if name in scopes[parent]: return parent
                parent = scope_parent[parent]
            return None
        return scope

    for scope, name, service in assignments:
        target = owner(name, scope)
        if target is not None:
            values = scopes[target].setdefault(name, set())
            if service: values.add(service)

    def services_for(name, scope):
        while scope is not None:
            target = owner(name, scope)
            if target is not scope:
                return scopes[target].get(name, set()) if target is not None else set()
            if name in scopes[scope]: return scopes[scope][name]
            scope = scope_parent[scope]
        return set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call): continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        launcher = _py_process_call(node, bindings)
        if launcher:
            values, variadic = _py_launcher_argv(launcher, node)
            if variadic:
                words = [_literal(v) for v in values]
                if words and all(w is not None for w in words):
                    out.extend(_shell_invocations(shlex.join(words), depth + 1))
                elif re.search(r"\baws(?:cli)?\b", code):
                    raise ValueError("dynamic process command")
                values = []
            for value in values:
                literal = _literal(value)
                if literal is None and isinstance(value, (ast.List, ast.Tuple)):
                    parts = [_literal(e) for e in value.elts]
                    literal = shlex.join(parts) if parts and all(p is not None for p in parts) else None
                if literal is not None:
                    out.extend(_shell_invocations(literal, depth + 1))
                elif re.search(r"\baws(?:cli)?\b", code):
                    # A process argv/command not statically known may run aws.
                    raise ValueError("dynamic process command")
        if name in SHELL_TOOLS:
            value = next((k.value for k in node.keywords if k.arg == "command"), node.args[0] if node.args else None)
            literal = _literal(value)
            if literal is not None: out.extend(_shell_invocations(literal, depth + 1))
            elif value is not None and re.search(r"\baws(?:cli)?\b", code):
                # A tool command not statically known may run aws (like the
                # subprocess path); the inner call's own hook may also see it.
                raise ValueError("dynamic tool command")
        if isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            services = services_for(receiver.id, node_scopes[node]) if isinstance(receiver, ast.Name) else {_client_service(receiver)} - {None}
            # Ambiguous bindings retain all possible services, never let a later
            # assignment erase a prior denied service. This may conservatively hold.
            for service in services:
                resources = None
                value = next((k.value for k in node.keywords if k.arg == "InstanceIds"), None)
                if service == "ssm" and name == "send_command" and isinstance(value, (ast.List, ast.Tuple)) \
                        and not any(k.arg is None or k.arg == "Targets" for k in node.keywords):
                    values = [_literal(v) for v in value.elts]
                    if values and all(v and re.fullmatch(r"i-[0-9A-Za-z]+", v) for v in values): resources = [v.lower() for v in values]
                out.append((service.lower(), _norm(name), resources))
        if isinstance(node.func, ast.Attribute) and node.func.attr == "resource" and re.search(r"\bboto3\b|\bbotocore\b", code):
            # boto3 resource objects call operations through generated
            # attributes (Instance(..).terminate()): any action of the service.
            arg = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "service_name"), None)
            service = _literal(arg)
            if not service: raise ValueError("dynamic resource service")
            out.append((service.lower(), _ANY_OP, None))
    return out


def _shell_calls(text, depth=0):
    return [(svc, op) for svc, op, _ in _shell_invocations(text, depth)]


def _py_calls(code):
    return [(svc, op) for svc, op, _ in _py_invocations(code)]


def _base_tool(name):
    """Wire/MCP spellings name the same tool (core reverses them before hooks;
    this is defence in depth for any path that does not)."""
    name = str(name or "")
    return name[5:] if name.startswith("mcp__") else name


def _opaque(value):
    """An envelope the guard cannot unwrap (nesting beyond the bound, JSON too
    deep to decode): no text is analyzed and the call is held unconditionally
    as static analysis unavailable (fail closed)."""
    return ("overflow", "")


def _nested_entries(args):
    """tool_call entries: the `calls` list and the legacy single {name, arguments}
    shape; string-JSON arguments are decoded. Undecodable JSON is passed on
    as the raw string (a tool would not run it); unparseable depth is opaque."""
    entries = list(args.get("calls") or []) if isinstance(args.get("calls"), list) else []
    if "name" in args:
        entries.append({"name": args.get("name"), "arguments": args.get("arguments")})
    for call in entries:
        if not isinstance(call, dict): continue
        inner = call.get("arguments")
        if isinstance(inner, str):
            try: inner = json.loads(inner)
            except ValueError: inner = {}
            except RecursionError:
                yield None, call.get("arguments"); continue
        yield str(call.get("name") or call.get("tool") or ""), inner


_NESTED_MAX = 16


def _call_texts(tool_name, args, depth=0):
    args = args if isinstance(args, dict) else {}
    tool_name = _base_tool(tool_name)
    if tool_name in SHELL_TOOLS:
        return [("shell", " ; ".join(str(args.get(k) or "") for k in ("command", "input", "data")))]
    if tool_name in PY_TOOLS:
        return [("python", str(args.get("code") or ""))]
    if tool_name in FILE_TOOLS:
        paths = [str(args.get("path") or "")]
        if tool_name == "patch":
            paths.extend(re.findall(r"^\*\*\* (?:Update File|Add File|Delete File|Move to): (.+)$", str(args.get("patch") or ""), re.M))
        return [("file", path) for path in paths]
    if tool_name in NESTED_TOOLS:
        if depth >= _NESTED_MAX:
            return [_opaque(args)]
        return [t for name, inner in _nested_entries(args)
                for t in ([_opaque(inner)] if name is None else _call_texts(name, inner, depth + 1))]
    return []


def _plausible_aws_call(kind, text):
    try:
        if _calls(kind, text): return True
    except (ValueError, SyntaxError, RecursionError):
        return bool(re.search(r"\baws(?:cli)?\b|token-broker\s+exec|boto3|botocore", text))
    if kind == "shell" and os.path.basename((text.split() or [""])[0]) in READERS:
        return False
    return bool(re.search(r"\baws(?:cli)?\b|token-broker\s+exec|boto3|botocore", text))


def _calls(kind, text):
    if kind == "overflow":
        return [("UNKNOWN", "UNKNOWN")]
    try:
        return _shell_calls(text) if kind == "shell" else _py_calls(text) if kind == "python" else []
    except (ValueError, SyntaxError, RecursionError):
        # Independent, nonthrowing failure classification; never retry a failed
        # parser in the hook fallback. No resource/action precision is asserted.
        return [("UNKNOWN", "UNKNOWN")] if re.search(r"\baws(?:cli)?\b|token-broker\s+exec|boto3|botocore", text) else []


def is_mutating(tool, args):
    return any(call not in POSITIVE_READS for kind, text in _call_texts(tool, args) for call in _calls(kind, text))


class LedgerState:
    def __init__(self, status, rows=None, reason=""):
        self.status, self.rows, self.reason = status, rows or [], reason


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate(row):
    if not isinstance(row, dict) or type(row.get("v", 0)) is not int or row.get("v", 0) not in (0, 1):
        raise ValueError("unsupported row schema")
    if row.get("action_kind") not in (None, "api"):
        raise ValueError("invalid action_kind")
    for name in ("action", "resource", "principal"):
        if not isinstance(row.get(name), str) or not row[name]:
            raise ValueError("invalid " + name)
    if not _finite(row.get("at")) or row.get("explicit") is not None and type(row["explicit"]) is not bool:
        raise ValueError("invalid timestamp or qualifier")
    if row["action"] == row["resource"] == "UNKNOWN" and not row.get("fingerprint"):
        raise ValueError("unknown legacy scope lacks invocation fingerprint")
    if row.get("v") == 1:
        if not _finite(row.get("observed_at")) or row["observed_at"] != row["at"]:
            raise ValueError("timestamp aliases disagree")
        for name in ("event_id", "execution_host", "fingerprint", "evidence_sha256"):
            if not isinstance(row.get(name), str) or not row[name]:
                raise ValueError("invalid " + name)
    return row


def _durable_rows(root):
    """Every valid row readable within the byte bound, plus any problem.

    Unhealthy states still return these rows: a cap, latch, oversized file or
    invalid line may add holds but never hides an already persisted denial."""
    path = root / "denial-ledger.jsonl"
    if not os.path.lexists(path):
        return None, ""
    with path.open("rb") as fh:
        data = fh.read(MAX_LEDGER_BYTES)
        over = bool(fh.read(1)) or len(data) >= MAX_LEDGER_BYTES
    lines = data.split(b"\n")
    if over and lines:
        lines.pop()  # possibly partial last line within the bound
    rows, problem = [], "byte cap" if over else ""
    for line in lines:
        if not line.strip():
            continue
        try:
            rows.append(_validate(json.loads(line.decode())))
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            problem = problem or type(exc).__name__
    return rows, problem


def _read_unlocked(root):
    try:
        rows, problem = _durable_rows(root)
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        return LedgerState("UNHEALTHY", reason=type(exc).__name__)
    try:
        capped = os.path.lexists(root / "denial-ledger.cap")
    except (OSError, ValueError):
        capped = True
    if capped:
        return LedgerState("UNHEALTHY", rows, "persistent cap")
    if str(root) in _UNHEALTHY:
        return LedgerState("UNHEALTHY", rows, _UNHEALTHY[str(root)])
    if problem:
        return LedgerState("UNHEALTHY", rows, problem)
    if rows is None:
        return LedgerState("MISSING")
    if len(rows) >= MAX_LEDGER_ROWS:
        return LedgerState("UNHEALTHY", rows, "row cap")
    return LedgerState("VALID" if rows else "EMPTY", rows)


@contextlib.contextmanager
def _locked(root, exclusive=False):
    _assert_writable_root(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "denial-ledger.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_ledger(root=None):
    root = _root(root)
    try:
        with _locked(root):
            return _read_unlocked(root)
    except (OSError, ValueError) as exc:
        return LedgerState("UNHEALTHY", reason=type(exc).__name__)


def _dedupe(row):
    key = tuple(row.get(k) for k in ("action", "resource", "principal", "explicit", "execution_host"))
    return key + (row.get("fingerprint"),)


def _cap(root):
    with open(root / "denial-ledger.cap", "a") as fh:
        fh.flush()
        os.fsync(fh.fileno())
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _retain_overflow(root, rows):
    # A bounded diagnostic sample, not authorization state. The persistent cap
    # or unhealthy latch holds mutations independently of retained evidence.
    _OVERFLOW.setdefault(str(root), deque(maxlen=MAX_OVERFLOW_EVIDENCE)).extend(rows)


def append_denials(rows, root=None):
    root = _root(root)
    _assert_writable_root(root)
    try:
        with _locked(root, True):
            state = _read_unlocked(root)
            if state.status == "UNHEALTHY":
                _retain_overflow(root, rows)
                if "cap" in state.reason:
                    _cap(root)
                return
            keys = {_dedupe(row) for row in state.rows}
            count = len(state.rows)
            size = (root / "denial-ledger.jsonl").stat().st_size if state.rows else 0
            for row in rows:
                _validate(row)
                key = _dedupe(row)
                if key in keys:
                    continue
                payload = (json.dumps(row, sort_keys=True) + "\n").encode()
                if count >= MAX_LEDGER_ROWS or size + len(payload) >= MAX_LEDGER_BYTES:
                    _retain_overflow(root, [row])
                    _cap(root)
                    continue
                new_file = not os.path.lexists(root / "denial-ledger.jsonl")
                fd = os.open(root / "denial-ledger.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    view = memoryview(payload)
                    while view:
                        n = os.write(fd, view)
                        if n <= 0:
                            raise OSError("short append")
                        view = view[n:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if new_file:
                    directory_fd = os.open(root, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                keys.add(key)
                count += 1
                size += len(payload)
    except Exception as exc:
        _UNHEALTHY[str(root)] = type(exc).__name__
        raise


PROCESS_TOOLS = {"process_manage", "process"}


def _process_command(result):
    """The background command a process_manage result reports output for."""
    try:
        decoded = json.loads(result) if isinstance(result, str) else result
    except (ValueError, RecursionError):
        return None
    command = decoded.get("command") if isinstance(decoded, dict) else None
    return command if isinstance(command, str) and command else None


def record_from_result(tool_name, args, result, now=None, *, root=None, **native_ids):
    if _base_tool(tool_name) in PROCESS_TOOLS:
        # poll/log/wait/kill output belongs to the launched terminal command,
        # which the result echoes; record it under that command's identity.
        command = _process_command(result)
        if command is None:
            return
        tool_name, args = "terminal", {"command": command}
    texts = [(kind, text) for kind, text in _call_texts(tool_name, args) if _plausible_aws_call(kind, text)]
    if not texts:
        return
    when = time.time() if now is None else now
    fingerprint = _fingerprint(tool_name, args)
    identity = invocation_identity(texts[0][1]) if len(texts) == 1 else "unknown"
    host = str(native_ids.get("execution_host") or (args or {}).get("host") or "local")
    calls = [c for kind, text in texts for c in _calls(kind, text)]
    rows = []
    denials = parse_denials(result, calls)
    incomplete = None
    while True:
        try:
            denial = next(denials)
        except StopIteration:
            break
        except (ValueError, RecursionError) as exc:
            # The result could not be fully observed (size/nesting bound).
            # Keep what was parsed and hold exact replay of this invocation.
            incomplete = type(exc).__name__
            break
        rows.append(denial)
    if incomplete:
        rows.append({"principal": "UNKNOWN", "action": "UNKNOWN", "resource": "UNKNOWN", "explicit": None,
                     "truncated": True, "observation": "incomplete:" + incomplete,
                     "evidence_sha256": _digest("incomplete:" + fingerprint)})
    for index, denial in enumerate(list(rows)):
        rows[index] = row = dict(denial, v=1, observed_at=when, at=when, identity=identity, identity_hint=identity,
                   execution_host=host, fingerprint=fingerprint, tool_name=tool_name,
                   session_id=str(native_ids.get("session_id") or ""), tool_call_id=str(native_ids.get("tool_call_id") or ""),
                   denial_index=index)
        row["event_id"] = "sha256:" + _digest(json.dumps(row, sort_keys=True))
    if rows:
        append_denials(rows, root=root)


def verify_receipt(denial_row, invocation):
    return "UNAVAILABLE"


def _rid(resource):
    return re.split(r"[/:]", resource.rstrip("/"))[-1]


def _mentions(text, rid):
    return bool(rid) and rid != "*" and re.search(r"(?<![\w-])" + re.escape(rid) + r"(?![\w-])", text) is not None


class _UncertainScalar:
    """Top of the bounded scalar domain; never input text or a glob."""
    __slots__ = ()


_UNCERTAIN = _UncertainScalar()


def _scalar_cost(value):
    return 1 if value is _UNCERTAIN else len(value)


def _join_environments(states):
    """Keep correlation below the cap; widen disagreements, not string bytes."""
    result, seen = [], set()
    for state in states:
        key = tuple(sorted(state.items()))
        if key not in seen:
            result.append(state); seen.add(key)
    if len(result) <= 8:
        return result
    keys = set().union(*(state.keys() for state in result))
    widened = {}
    for key in keys:
        first = result[0].get(key, _UNCERTAIN)
        widened[key] = first if all(key in state and state[key] == first for state in result) else _UNCERTAIN
    return [widened]


def _environment_set(states):
    return {tuple(sorted(state.items())) for state in states}


def _widen_environments(states):
    """One environment: agreeing values kept, disagreeing or missing -> top."""
    keys = set().union(*(state.keys() for state in states))
    first = states[0]
    return {key: first[key] if all(key in s and s[key] == first.get(key) for s in states) and key in first
            else _UNCERTAIN for key in keys}


def _slice_word(word, start):
    return (False, word[1][start:], word[2][start:], '', len(word) > 4 and word[4])


def _tamper_words(text, scalars, *, structure=False, budget=None):
    """Yield command tokens (operator, value, glob mask), without evaluation.

    The generator expands each command before its caller updates scalar state.
    Quotes/escapes retain per-character glob eligibility. Unknown parameters are
    holes, never wildcards. This is deliberately not a general shell parser.
    """
    segment, word, active = [], [], []
    uncertain = False
    quote, started, i, start, segment_start = None, False, 0, 0, 0
    def finish():
        nonlocal word, active, started, uncertain
        if started:
            segment.append((False, ''.join(word), tuple(active), text[start:i], uncertain))
        word, active, started = [], [], False
        uncertain = False
    while i < len(text):
        c = text[i]
        if not started: start = i
        if c == '\\' and quote != "'":
            if i + 1 == len(text):
                raise _TamperUnsupported('shell trailing escape')
            following = text[i + 1]
            if following != '\n':
                if quote == '"' and following not in '$`"\\':
                    word.append(c); active.append(False)
                word.append(following); active.append(False); started = True
            i += 2; continue
        if c in "'\"" and (quote is None or quote == c):
            quote = None if quote else c
            started = True; i += 1; continue
        if c == '$' and quote is None and text.startswith("$'", i) and not _legacy() and _precise_used():
            # ANSI-C quoting: backslash escapes the closing quote. Escapes are
            # not decoded; a word containing one is uncertain, never a glob.
            j = _ansi_c_end(text, i)
            if j < 0: raise _TamperUnsupported('shell unclosed quote')
            content = text[i + 2:j]
            if '\\' in content: uncertain = True
            word.extend(content); active.extend([False] * len(content))
            started = True; i = j + 1; continue
        if c == '$' and quote != "'":
            m = re.match(r'\$(?:\{([A-Za-z_][A-Za-z_0-9]*|[0-9]+|[@*])\}|([A-Za-z_][A-Za-z_0-9]*|[0-9@*]))', text[i:])
            if not m and text.startswith('${', i) and not _legacy() and _precise_used():
                # Complex expansion (${F%x}, ${F:-x}, ${!G}, ${F[0]}, ...):
                # never decoded. A word built from one is uncertain when its
                # name/operand can carry a protected (fragment) or top value,
                # otherwise an opaque non-protected value.
                end = _brace_expansion_end(text, i)
                if end < 0: raise _TamperUnsupported('shell unclosed parameter expansion')
                inner = text[i + 2:end]
                if budget is not None:
                    budget[0] -= len(inner) + len(scalars)
                    if budget[0] < 0: raise _TamperUnsupported('shell expansion budget')
                if _complex_expansion_uncertain(inner, scalars):
                    uncertain = True
                else:
                    word.append('\x00'); active.append(False)
                started = True; i = end + 1; continue
            if m:
                value = _UNCERTAIN if m[2] == '__DSG_RISKY' else scalars.get(m[1] or m[2], '\x00')
                if value is _UNCERTAIN:
                    uncertain = True
                    if budget is not None:
                        budget[0] -= 1
                        if budget[0] < 0: raise _TamperUnsupported('shell expansion budget')
                    started = True; i += len(m[0]); continue
                if len(word) + len(value) > 262144:
                    raise _TamperUnsupported('shell scalar length budget')
                if budget is not None:
                    budget[0] -= len(value)
                    if budget[0] < 0: raise _TamperUnsupported('shell expansion budget')
                word.extend(value); active.extend([quote is None] * len(value))
                started = True; i += len(m[0]); continue
        if not quote and c == '#' and not started:
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
            continue
        if not quote and c in ' \t\r\n;&|()<>':
            # An adjacent numeric word before a redirect is an IO descriptor.
            if c in '<>' and started and not uncertain and ''.join(word).isdigit() and all(active):
                word, active, started = [], [], False
            finish()
            if c in '<>':
                op = next((s for s in ('<<<', '<<-', '>>', '>|', '<>', '<<', '>&', '<&') if text.startswith(s, i)), c)
                segment.append((True, op, ())); i += len(op); continue
            if c in '\n;&|()':
                if structure:
                    # Structure items carry their end offset (uninspected-region start).
                    if segment: yield ('command', text[segment_start:i], i)
                    op = c * 2 if c in '&|' and text.startswith(c * 2, i) else c
                    yield ('operator', op, i + len(op))
                    i += len(op); segment_start = i; segment = []; continue
                if segment: yield segment
                segment = []
            i += 1; continue
        word.append(c); active.append(quote is None); started = True; i += 1
    if quote:
        raise _TamperUnsupported('shell unclosed quote')
    finish()
    if segment:
        yield ('command', text[segment_start:], len(text)) if structure else segment


_PROTECTED_NAMES = ('denial-ledger.jsonl', 'denial-ledger.cap', 'denial-ledger.lock', 'denial-grants.json')
_HOME_RAW = re.compile(r'''^["']?(?:~|\$\{?HOME\}?)["']?/?["']?$''')
# A cd/pushd into the ledger root, home or OLDPWD (static spelling only).
_CD_ROOT = re.compile(r'''(?:^|[;&|(){}\s])(?:cd|pushd)(?:\s+-[LPe@]+)*(?:\s*(?:$|[;&|)])|\s+["']?(?:~/?|\$\{?HOME\}?/?|\$\{?HERMES_HOME\}?/?|-|[^\s;&|)'"]*\.hermes/?)["']?(?:$|[\s;&|)]))|\bchdir\s*\([^)]{0,256}?(?:\.hermes|HERMES_HOME|~['"]|HOME)''')


def _ancestor_paths():
    # An explicit ledger root (classify(root=...)) is the protected root; the
    # account root is resolved only when none was given (production hooks).
    extra = getattr(_PY_STATE, 'ledger_root', None)
    roots = [Path(extra)] if extra else []
    if not roots:
        try:
            roots.append(resolve_root())
        except Exception:
            pass
    return {str(p) for root in roots for p in (root, *root.parents)}


_CD_TARGET = re.compile(r'''(?:^|[;&|(){}\s])(?:cd|pushd)(?:\s+-[LPe@]+)*\s+["']?([^\s;&|)'"]{1,4096})''')


def _cd_into_root(text):
    if _CD_ROOT.search(text): return True
    return any(_root_dir(m[1]) for m in _CD_TARGET.finditer(text))


def _root_dir(value, raw=''):
    """Whether a path names the ledger root (any `.hermes` dir, $HERMES_HOME)
    or an ancestor of the account root (home, /). Spelling-based."""
    v = value.rstrip('/') or ('/' if value else '')
    while v.endswith('/.'): v = v[:-2].rstrip('/') or '/'
    if not v or v == '.': return False
    if v == '.hermes' or v.endswith('/.hermes') or v in ('~', '/'): return True
    if v in _ancestor_paths(): return True
    if v == '\x00' and (_HOME_RAW.match(raw.strip()) or re.search(r'\$\{?HERMES_HOME\b', raw)): return True
    return False


def _glob_protected(value, flags, raw=''):
    """A glob in the root directory (or in the cwd after a cd into it) that
    matches a ledger file name; flags mark active glob characters."""
    base = value.rsplit('/', 1)[-1]
    if '\x00' in base: return False
    bflags = flags[len(value) - len(base):]
    if not any(f and c in '*?[' for c, f in zip(base, bflags)): return False
    head = value[:len(value) - len(base)]
    if head.strip('/') in ('', '.'):
        if not getattr(_PY_STATE, 'cwd_root', False): return False
    elif not _root_dir(head, raw.rsplit('/', 1)[0] if '/' in raw else ''):
        return False
    pattern = ''.join(c if f or c not in '*?[' else {'*': '[*]', '?': '[?]', '[': '[[]'}[c] for c, f in zip(base, bflags))
    return any(fnmatch.fnmatchcase(name, pattern) for name in _PROTECTED_NAMES)


def _protected_word(word):
    if len(word) > 4 and word[4]: return True
    _, value, active = word[:3]
    if any(name in value for name in PROTECTED): return True
    raw = word[3] if len(word) > 3 else ''
    if _root_dir(value, raw) or _glob_protected(value, active, raw): return True
    if 'denial' in value and any(c == '{' and flag for c, flag in zip(value, active)):
        return True  # unexpanded brace expansion around a protected fragment
    base = value.rsplit('/', 1)[-1]
    if not base.startswith('denial') or '\x00' in base: return False
    flags = active[len(value) - len(base):]
    # Escape inactive pattern metacharacters; never expand against a filesystem.
    pattern = ''.join(c if flag or c not in '*?[' else {'*': '[*]', '?': '[?]', '[': '[[]'}[c]
                      for c, flag in zip(base, flags))
    return any(fnmatch.fnmatchcase(name, pattern) for name in
               ('denial-ledger.jsonl', 'denial-ledger.cap', 'denial-ledger.lock', 'denial-grants.json'))


class _TamperUnsupported(ValueError):
    """Explicit analysis limit, not evidence of authorization or containment."""


# Shell analysis runs two passes per outermost shell text. The legacy pass is
# the accepted 9caf-equivalent structure (no compound/function/case parsing,
# no $'..'/complex ${..} lexing: those regions stay unsupported syntax under
# the baseline rule); the precise pass parses them. Either holding holds, so
# the precise pass can only add holds (a floor, not a replacement).
_SHELL_MODE = __import__('threading').local()


def _legacy():
    return getattr(_SHELL_MODE, 'legacy', False)


def _precise_used():
    """Record that the precise pass parsed a construct the legacy floor does
    not (only then can their verdicts differ and the floor must run)."""
    _SHELL_MODE.precise_used = True
    return True


# Fragments of protected file names: a value containing one may become a
# protected name after slicing/concatenation, so expansions of it are uncertain.
_PROTECTED_FRAGMENTS = ('denial', 'grants.json', 'ledger.jsonl', 'ledger.cap', 'ledger.lock')
_POSITIONAL = ('1', '2', '3', '4', '5', '6', '7', '8', '9')
_DECLARE = ('export', 'local', 'declare', 'typeset', 'readonly')


def _risky_value(value):
    return value is _UNCERTAIN or any(f in value for f in _PROTECTED_FRAGMENTS)


def _brace_expansion_end(text, i):
    """Index of the '}' closing the ${..} starting at i (nested ${..}, quotes), or -1."""
    depth, j, quote = 0, i, None
    while j < len(text):
        c = text[j]
        if c == '\\' and quote != "'": j += 2; continue
        if quote:
            if c == quote: quote = None
        elif c in '\'"': quote = c
        elif text.startswith('${', j): depth += 1; j += 2; continue
        elif c == '}':
            depth -= 1
            if depth == 0: return j
        j += 1
    return -1


def _complex_expansion_uncertain(inner, scalars):
    """True when ${inner} may carry a protected or top value.

    Uncertain: a protected fragment literally in inner; the base name (or any
    $NAME reference in an operand) holds a protected fragment or top; any
    ${!..} indirection while some tracked value is risky. ${#NAME} is a
    length. :=/= stores make the base top when risky (never a strong store).
    """
    if any(f in inner for f in _PROTECTED_FRAGMENTS): risky = True
    else:
        risky = False
        head = re.match(r'([!#]?)([A-Za-z_][A-Za-z_0-9]*|[0-9]+|[@*])', inner)
        if head and head[1] == '#' and head.end() == len(inner):
            return False
        if head and head[1] == '!' or not head:
            risky = any(_risky_value(v) for v in scalars.values())
        elif _risky_value(scalars.get(head[2], '')):
            risky = True
        names = re.findall(r'\$\{?([A-Za-z_][A-Za-z_0-9]*|[0-9]+|[@*])', inner)
        if any(_risky_value(scalars.get(n, '')) for n in names): risky = True
    store = re.match(r'([A-Za-z_][A-Za-z_0-9]*)(?:\[[^]]*\])?:?=', inner)
    if store:
        if risky: scalars[store[1]] = _UNCERTAIN
        else: scalars.setdefault(store[1], '\x00')
    return risky


def _bind_positionals(words, scalars):
    """Function/set positional parameters: a protected argument's value for
    every position (top when any is uncertain or glob-protected), otherwise
    the literal per-index values."""
    value = None
    for w in words:
        if not w[4] and _protected_word(w):
            value = w[1] if any(n in w[1] for n in PROTECTED) else _UNCERTAIN
            break
    if value is None and any(w[4] for w in words): value = _UNCERTAIN
    for index, key in enumerate(_POSITIONAL):
        scalars[key] = value if value is not None else (words[index][1] if index < len(words) else '\x00')
    joined = value if value is not None else (' '.join(w[1] for w in words) if words else '\x00')
    scalars['@'] = scalars['*'] = joined


def _shift_positionals(args, scalars):
    values = [scalars.get(k, '\x00') for k in _POSITIONAL]
    if all(v is values[0] or v == values[0] for v in values): return
    n = 1
    if args:
        if args[0][4] or not args[0][1].isdigit():
            for key in _POSITIONAL + ('@', '*'): scalars[key] = _UNCERTAIN
            return
        n = int(args[0][1])
    values = values[n:] + ['\x00'] * min(n, 9)
    for key, value in zip(_POSITIONAL, values): scalars[key] = value
    remaining = [v for v in values if v is _UNCERTAIN or v != '\x00']
    if any(v is _UNCERTAIN for v in remaining):
        scalars['@'] = scalars['*'] = _UNCERTAIN
    else:
        scalars['@'] = scalars['*'] = ' '.join(remaining) if remaining else '\x00'


_LITERAL_STOP = set(' \t\r\n\'"`;&|()<>{}$')


def _protected_literal(text):
    """First protected name written literally in text, with its adjacent
    word characters (linear scan; never a backtracking regex)."""
    hits = [i for i in (text.find(name) for name in PROTECTED) if i >= 0]
    if not hits: return None
    start = end = min(hits)
    while start > 0 and text[start - 1] not in _LITERAL_STOP: start -= 1
    while end < len(text) and text[end] not in _LITERAL_STOP: end += 1
    return text[start:end]


def _call_arguments(text, scalars, budget):
    """Expanded argument words of the first command in text, after reserved
    words, NAME=value prefixes and the called name; None if unavailable."""
    for tokens in _tamper_words(text, dict(scalars), budget=budget):
        words, i = [], 0
        while i < len(tokens):
            if tokens[i][0]: i += 2; continue
            words.append(tokens[i]); i += 1
        while words and not words[0][4] and (words[0][3] in _RESERVED_PREFIX or words[0][3] in ('-p', 'command', 'builtin')
                                            or re.match(r'[A-Za-z_][A-Za-z_0-9]*\+?=', words[0][3])):
            words.pop(0)
        return words[1:]
    return []


# Compound openers -> closers. Body commands are analyzed with join semantics.
_COMPOUND_CLOSERS = {'if': 'fi', '{': '}', 'while': 'done', 'until': 'done', 'for': 'done', 'select': 'done'}
# Unquoted reserved words that only introduce/continue/close a command.
_RESERVED_PREFIX = {'if', 'then', 'else', 'elif', 'fi', 'while', 'until', 'do', 'done', '{', '}', '!', 'time'}


def _first_word(command_text):
    """First unquoted word after reserved prefixes that keep the command going."""
    for match in re.finditer(r'[^ \t\r\n;&|()<>]+', command_text):
        if match[0] not in ('then', 'do', 'else', 'elif', '!', 'time', '-p'):
            return match[0]
    return None


def _called_name(command_text):
    """Literal command word after reserved words and NAME=value prefixes."""
    for match in re.finditer(r'[^ \t\r\n;&|()<>]+', command_text):
        word = match[0]
        if word in _RESERVED_PREFIX or word in ('-p', 'command', 'builtin'): continue
        if re.match(r'[A-Za-z_][A-Za-z_0-9]*\+?=', word): continue
        return word
    return None


def _shell_baseline(text, tainted=()):
    """Reviewed baseline rule for shell text the bounded analysis did not inspect.

    A shell write keyword together with a literal protected name, or with a
    reference to a tracked name that held a protected or top value. Literal
    only: names built dynamically inside the uninspected text stay a gap.
    """
    if not WRITE_SHELL.search(text): return False
    if any(name in text for name in PROTECTED): return True
    return bool(tainted) and not set(tainted).isdisjoint(re.findall(r'\$\{?([A-Za-z_][A-Za-z_0-9]*)', text))


def _shell_tree(text, issues):
    """Retain complete lexical prefixes; never recover inside an opaque word.

    The unparsed remainder (unsupported syntax, group depth, lexer error, token
    cap) becomes one trailing ('opaque', text) node checked by the baseline
    rule. A stray top-level ')' means the prefix itself may be misparsed (case
    patterns, a substitution closed early), so the whole text becomes opaque.
    """
    items, ends = [], []
    lexed = True
    try:
        for kind, value, end in _tamper_words(text, {}, structure=True):
            if len(items) == 16384:
                issues.append('shell token budget; suffix uninspected')
                lexed = False
                break
            items.append((kind, value)); ends.append(end)
    except _TamperUnsupported as error:
        issues.append(str(error))
        # An unclosed quote is NOT treated as a non-executing syntax error: this
        # lexer does not model heredocs, $'...' quoting and other constructs,
        # so its 'unclosed quote' can be bash's valid text. The remainder after
        # the last complete item stays uninspected (baseline rule).
        lexed = False
    position = 0
    halted = False  # a failure inside a compound: everything after is opaque
    def newlines():
        nonlocal position
        while peek('\n'): position += 1
    def peek(value):
        return position < len(items) and items[position] == ('operator', value)
    def atom(level):
        nonlocal position
        if level > 6: raise _TamperUnsupported('shell group depth')
        if peek('('):
            position += 1
            result = sequence(level + 1)
            if not peek(')'):
                issues.append('unclosed shell group')
            else:
                position += 1
            return ('group', result)
        if position < len(items) and items[position][0] == 'command':
            first = _first_word(items[position][1])
            if not _legacy():
                if first == 'case':
                    _precise_used()
                    raise _TamperUnsupported('shell case unsupported')
                definition = function_definition(level)
                if definition is not None:
                    _precise_used(); return definition
                if first in _COMPOUND_CLOSERS:
                    _precise_used()
                    return compound(level, _COMPOUND_CLOSERS[first], first not in ('if', '{'))
            result = items[position]; position += 1
            return result
        raise _TamperUnsupported('unsupported shell list syntax')
    def function_definition(level):
        """`function NAME [()] BODY` or `NAME() BODY`: the body runs only when
        called, so it is a ('function', (NAME, body)) node."""
        nonlocal position
        command = items[position][1]
        keyword = re.match(r'(\s*(?:(?:then|do|else|elif)\s+)*)function\s+([^\s;&|()<>]+)\s*', command)
        if keyword:
            name = keyword[2]
            rest = command[keyword.end():]
            if rest.startswith('{') and (len(rest) == 1 or rest[1] in ' \t\r\n'):
                # body opener on the same item: drop the `function NAME` words
                items[position] = ('command', keyword[1] + rest)
                return ('function', (name, compound(level, '}', False)))
            if rest.strip():
                raise _TamperUnsupported('shell function form unsupported')
        else:
            plain = re.fullmatch(r'\s*(?:(?:then|do|else|elif)\s+)*([^\s;&|()<>\'"\\$`=]+)\s*', command)
            if not plain: return None
            name = plain[1]
        if not (peek_at(1, '(') and peek_at(2, ')')):
            if not keyword: return None
            position += 1  # `function NAME` then the body on a later line
        else:
            position += 3
        newlines()
        if position < len(items) and items[position][0] == 'command' and _first_word(items[position][1]) == '{':
            return ('function', (name, compound(level, '}', False)))
        if peek('('):
            return ('function', (name, atom(level)))
        raise _TamperUnsupported('shell function form unsupported')
    def peek_at(offset, value):
        return position + offset < len(items) and items[position + offset] == ('operator', value)
    def compound(level, closer, loop):
        """Opener command, body nodes and closer command; stops at ')' or end."""
        nonlocal position, halted
        nodes = [items[position]]
        position += 1
        while not halted:
            if peek(';') or peek('\n'):
                position += 1; continue
            if position >= len(items) or peek(')'):
                issues.append('unclosed shell compound')
                break
            if items[position][0] == 'command' and _first_word(items[position][1]) == closer:
                nodes.append(items[position]); position += 1
                break
            try:
                node = conditional(level + 1)
            except _TamperUnsupported as error:
                issues.append(str(error))
                halted = True
                break
            if peek('&'):
                position += 1; node = ('group', node)
            elif not halted and position < len(items) and not (peek(';') or peek('\n') or peek(')')):
                issues.append('unsupported shell separator; suffix uninspected')
                halted = True
            nodes.append(node)
        return ('compound', (loop, nodes))
    def pipeline(level):
        nonlocal position
        stages = [atom(level)]
        while not halted and peek('|'):
            position += 1
            if peek('&'):
                issues.append('shell |& recovery uses child scope')
                position += 1
            newlines()
            try:
                stages.append(atom(level))
            except _TamperUnsupported as error:
                issues.append(str(error))
                return ('pipe', stages)
        return stages[0] if len(stages) == 1 else ('pipe', stages)
    def conditional(level):
        nonlocal position
        stages = [pipeline(level)]
        while not halted and (peek('&&') or peek('||')):
            position += 1
            newlines()
            try:
                stages.append(pipeline(level))
            except _TamperUnsupported as error:
                issues.append(str(error))
                break
        return stages[0] if len(stages) == 1 else ('conditional', stages)
    def sequence(level):
        nonlocal position
        result = []
        while not halted and position < len(items) and not peek(')'):
            if peek(';') or peek('\n'):
                position += 1; continue
            try:
                node = conditional(level)
            except _TamperUnsupported as error:
                issues.append(str(error))
                break  # opaque suffix: do not invent synchronization/state
            if halted:
                result.append(node)
                break
            if peek('&'):
                position += 1; node = ('group', node)
            elif peek(';') or peek('\n'):
                position += 1
            elif position < len(items) and not peek(')'):
                issues.append('unsupported shell separator; suffix uninspected')
                result.append(node)
                break
            result.append(node)
        return ('sequence', result)
    result = sequence(0)
    if position != len(items): issues.append('uninspected shell suffix')
    if position < len(items) and items[position] == ('operator', ')'):
        result[1].append(('opaque', text))
    elif position < len(items) or not lexed:
        start = ends[position - 1] if position else 0
        result[1].append(('opaque', text[start:]))
    return result


def _shell_tamper(text, depth=0, budget=None, issues=None, initial=None):
    """Two passes (see _SHELL_MODE): the precise pass on the caller's budget
    list; when it allowed but parsed a precise-only construct (compound,
    function, case, $'..', complex ${..}), the 9caf-equivalent legacy floor
    reruns from the same starting budget on the same list. The caller keeps
    the larger consumption (total work <= 2x one pass, never replenished)."""
    if budget is None: budget = [1048576]
    if issues is None: issues = []
    if getattr(_SHELL_MODE, 'pass_active', False):
        return _shell_tamper_pass(text, depth, budget, issues, initial)
    _SHELL_MODE.pass_active = True
    _SHELL_MODE.precise_used = False
    try:
        start = budget[0]
        try:
            if _shell_tamper_pass(text, depth, budget, issues, initial): return True
            failure = None
        except _TamperUnsupported as error:
            if not _SHELL_MODE.precise_used: raise
            failure = error  # e.g. budget spent on precise-only work: the floor still decides
        except Exception as error:
            # A defect in precise-only modeling must not remove the floor:
            # the floor still decides, then the error propagates if it allows.
            if not _SHELL_MODE.precise_used: raise
            failure = error
        if not _SHELL_MODE.precise_used: return False
        spent = budget[0]
        budget[0] = start
        _SHELL_MODE.legacy = True
        floor_issues = []
        try:
            if _shell_tamper_pass(text, depth, budget, floor_issues, initial):
                issues.extend(floor_issues)
                return True
            if failure is not None: raise failure
            return False
        finally:
            _SHELL_MODE.legacy = False
            budget[0] = min(budget[0], spent)
    finally:
        _SHELL_MODE.pass_active = False
        _SHELL_MODE.precise_used = False


def _shell_tamper_pass(text, depth, budget, issues, initial):
    """Analyze finite alternative environments, never merge their string bytes.

    Conditionals conservatively retain skipped and evaluated states; pipeline
    stores and subshell stores never overwrite the parent. Shell lastpipe is
    unsupported. A bounded analysis failure is NOT a permission grant.
    """
    if budget is None: budget = [1048576]
    if issues is None: issues = []
    budget[0] -= len(text)
    if budget[0] < 0:
        raise _TamperUnsupported('shell source/work budget')
    tainted = {}
    def note(states):
        # Flow-insensitive: names that ever held a protected or top value.
        for state in states:
            for key, value in state.items():
                if key not in tainted and (value is _UNCERTAIN or any(name in value for name in PROTECTED)):
                    tainted[key] = value
    note([initial or {}])
    if depth > 6 or len(text) > 262144:
        # Nesting/source bound: the whole text is uninspected, so only the
        # reviewed baseline rule applies (an explicit limit, never permission).
        issues.append('shell nesting/source bound; baseline rule applied')
        return _shell_baseline(text, tainted)
    outer, children = _substitutions(text)
    child_hit = False
    for child in children:
        try:
            child_hit |= _shell_tamper(child, depth + 1, budget, issues)
        except _TamperUnsupported as error:
            if budget[0] < 0: raise
            issues.append(str(error))
    tree = _shell_tree(outer, issues)
    if child_hit: return True
    unique = _join_environments
    functions, defined, calling = [], {}, set()
    def run(node, states):
        kind, value = node
        if kind == 'opaque':
            # Unparsed remainder (always the end of this text): baseline rule
            # over its raw text plus references to tainted tracked names.
            budget[0] -= len(value)
            if budget[0] < 0: raise _TamperUnsupported('shell expansion budget')
            return _shell_baseline(value, tainted), states
        if kind == 'command':
            result = []
            for state in states:
                budget[0] -= len(value) + len(state) + sum(_scalar_cost(v) for v in state.values())
                if budget[0] < 0: raise _TamperUnsupported('shell expansion budget')
                updated = dict(state)
                try:
                    if _command_tamper(value, updated, depth, budget, issues): return True, states
                except _TamperUnsupported as error:
                    # A local analysis failure is not a successful strong store.
                    # Exhausted shared work cannot be recovered with a fresh budget.
                    if budget[0] < 0: raise
                    issues.append(str(error))
                    updated = _failed_command_state(value, state, updated, budget)
                result.append(updated)
            note(result)
            called = _called_name(value) if defined else None
            if called in defined and called not in calling:
                calling.add(called)
                entry = []
                for state, updated in zip(states, result):
                    bound = dict(updated)
                    try:
                        words = _call_arguments(value, state, budget)
                    except _TamperUnsupported:
                        if budget[0] < 0: raise
                        words = [(False, '', (), '', True)]  # unknown arguments: top
                    _bind_positionals(words, bound)
                    entry.append(bound)
                try:
                    hit, after = run(('compound', (True, [defined[called]])), unique(entry))
                finally:
                    calling.discard(called)
                if hit: return True, states
                # The caller's positional parameters are restored on return.
                restored = []
                for state in after:
                    state = dict(state)
                    for key in _POSITIONAL + ('@', '*'):
                        olds = [s.get(key) for s in result]
                        if all(o is None for o in olds): state.pop(key, None)
                        elif all(o is olds[0] or o == olds[0] for o in olds): state[key] = olds[0]
                        else: state[key] = _UNCERTAIN
                    restored.append(state)
                return False, unique(restored)
            return False, unique(result)
        if kind == 'group':
            hit, _ = run(value, [dict(s) for s in states])
            return hit, states
        if kind == 'function':
            # Defining a function runs nothing. Calls by its literal name run
            # the body inline (bounded loop semantics, recursion guarded); the
            # body is also analyzed after the main flow with every tracked
            # protected/top value (indirect calls, calls from substitutions).
            name, body = value
            defined[name] = body
            functions.append(body)
            return False, states
        if kind == 'pipe':
            for stage in value:
                hit, _ = run(stage, [dict(s) for s in states])
                if hit: return True, states
            return False, states
        if kind == 'compound':
            # Any body command may or may not run (if/else branches, zero-trip
            # loops): join after every command. Loops iterate to a bounded
            # fixpoint; still-changing values then widen to top.
            loop, nodes = value
            def body(start):
                current = start
                for child in nodes:
                    hit, after = run(child, current)
                    if hit: return True, current
                    current = unique(current + after)
                return False, current
            current = states
            for iteration in range(4 if loop else 1):
                hit, after = body(current)
                if hit: return True, states
                joined = unique(current + after)
                if _environment_set(joined) == _environment_set(current): return False, joined
                current = joined
            if not loop: return False, current
            # Widening: each further pass turns at least one key top or adds a
            # key, so this terminates (and every pass is charged to the budget).
            current = [_widen_environments(current)]
            while True:
                hit, after = body(current)
                if hit: return True, states
                joined = [_widen_environments(current + after)]
                if _environment_set(joined) == _environment_set(current): return False, joined
                current = joined
        for index, child in enumerate(value):
            hit, after = run(child, states)
            if hit: return True, states
            states = unique(states + after) if kind == 'conditional' and index else after
        return False, states
    hit = run(tree, [dict(initial or {})])[0]
    # Function bodies: bounded loop semantics from the flow-insensitive
    # tracked values (functions defined inside bodies are appended and run too).
    index = 0
    seed = dict(tainted)
    # Calls not resolved inline (indirect names, substitutions) pass unknown
    # arguments: positional parameters take a tracked protected value, else a
    # protected name written literally in this text, else top if any tracked
    # value is top.
    positional = next((v for v in tainted.values() if v is not _UNCERTAIN and any(n in v for n in PROTECTED)), None)
    if positional is None:
        positional = _protected_literal(outer)
        if positional is None and any(v is _UNCERTAIN for v in tainted.values()): positional = _UNCERTAIN
    if positional is not None:
        for key in _POSITIONAL + ('@', '*'): seed[key] = positional
    while not hit and index < len(functions):
        hit = run(('compound', (True, [functions[index]])), [dict(seed)])[0]
        index += 1
    if hit or not tainted: return hit
    # Substitutions run in a subshell that inherits the parent's variables.
    # They were analyzed before the parent flow; re-analyze those referencing
    # a tainted name with that (flow-insensitive) value.
    for child in children:
        names = set(re.findall(r'\$\{?([A-Za-z_][A-Za-z_0-9]*)', child)) & set(tainted)
        if not names: continue
        try:
            if _shell_tamper(child, depth + 1, budget, issues, {name: tainted[name] for name in names}): return True
        except _TamperUnsupported as error:
            if budget[0] < 0: raise
            issues.append(str(error))
    return False


def _failed_command_state(text, incoming, partial, budget):
    """Top covers old/partial/new stores after a local failure, never a grant.

    Re-lex only this already delimited command with unresolved scalars. This
    identifies raw assignment names without retrying failed expansion. Dynamic
    names/effects remain unsupported; no search through quoted code or data.
    """
    keys = set(incoming) | set(partial)
    budget[0] -= len(text)
    if budget[0] < 0: raise _TamperUnsupported('shell recovery work budget')
    for tokens in _tamper_words(text, {}, budget=budget):
        i = 0
        declaration = False
        while i < len(tokens):
            word = tokens[i]
            if word[0]:
                i += 2  # redirect and operand do not end assignment prefixes
                continue
            if not declaration and word[3] in _DECLARE:
                declaration = True; i += 1; continue  # its NAME= operands
            if word[3] in ('set', 'shift') and not declaration:
                keys.update(_POSITIONAL + ('@', '*'))
                break
            match = re.match(r'^([A-Za-z_][A-Za-z_0-9]*)\+?=', word[3])
            if not match:
                if declaration: i += 1; continue
                break
            keys.add(match[1])
            i += 1
    return dict.fromkeys(keys, _UNCERTAIN)


def _opaque_scalar_transfer(cmd, args, scalars, issues):
    """Preserve possible stores; opaque code can introduce untracked names.

    Only simple read names and unset [-v|-f] [--] names are localized. Unset
    is not assumed successful (e.g. readonly variables); its named old values
    become top rather than disappearing. Complex options/dynamic names widen
    tracked values and carry an explicit limitation, not permission.
    """
    words = list(args)
    functions_only = False
    if cmd == 'unset' and words and not words[0][4] and words[0][1] in ('-v', '-f'):
        functions_only = words.pop(0)[1] == '-f'
    if cmd in ('unset', 'read') and words and not words[0][4] and words[0][1] == '--':
        words.pop(0)
    names = [w[1] for w in words if not w[4] and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', w[1])]
    if cmd in ('unset', 'read') and len(names) == len(words):
        if cmd == 'unset':
            if not functions_only:
                for name in names:
                    if name in scalars: scalars[name] = _UNCERTAIN
        else:
            for name in names or ['REPLY']: scalars[name] = _UNCERTAIN
        return
    for key in scalars: scalars[key] = _UNCERTAIN
    if cmd == 'read':
        for name in names: scalars[name] = _UNCERTAIN
        scalars['REPLY'] = _UNCERTAIN
    if issues is not None:
        issues.append('opaque shell ' + cmd + ': dynamic/new names unsupported')


def _unwrap_tamper_command(words, budget, issues, child_env=None):
    """Decode only enumerated wrapper grammars; argv is already expanded.

    Returns (command, argv, child_scope, child_env). Environment mutation is
    NOT applied to outer expansion state. child_env holds only the known
    literal command-line assignments (prefix and env/sudo VAR=value, minus
    env -i/-u removals) for a direct interpreter child; inherited exported
    outer variables remain unknown holes, so child environment stays an
    explicit limitation. Unknown parameter holes and top values in wrapper or
    executable position are reported, never guessed as a command.
    """
    child_scope = False
    child_env = dict(child_env or {})
    def unsupported(reason):
        if issues is not None: issues.append(reason)
        return None, [], child_scope, child_env
    while words:
        budget[0] -= 1
        if budget[0] < 0: raise _TamperUnsupported('shell work budget')
        executable, *words = words
        if executable[4] or '\x00' in executable[1]:
            return unsupported('uncertain shell executable unsupported')
        cmd = executable[1].rsplit('/', 1)[-1]
        if cmd not in ('env', 'sudo', 'command', 'exec', 'token-broker'):
            return cmd, words, child_scope, child_env
        if cmd in ('env', 'sudo', 'token-broker'): child_scope = True
        if cmd == 'token-broker':
            if not words or words[0][4] or words[0][1] != 'exec':
                return unsupported('token-broker form unsupported')
            words = words[1:]
        ended = False
        while words:
            word = words[0]; value = word[1]
            if word[4] or value.startswith('\x00'):
                return unsupported('uncertain shell executable unsupported' if cmd in ('command', 'exec')
                                   else 'uncertain wrapper argument unsupported')
            if value == '--':
                words = words[1:]; ended = True; break
            if cmd == 'command' and value in ('-v', '-V'):
                return None, [], child_scope, child_env  # lookup only, not invocation
            flags = {'env': {'-i', '--ignore-environment'},
                     'sudo': {'-n', '-H', '-E'}, 'command': {'-p'},
                     'exec': {'-c', '-l'}, 'token-broker': set()}[cmd]
            values = {'env': {'-u', '--unset', '-C', '--chdir'},
                      'sudo': {'-u', '--user', '-g', '--group'},
                      'exec': {'-a'}, 'command': set(),
                      'token-broker': {'--scope', '--reason'}}[cmd]
            if value in flags:
                if cmd == 'env': child_env.clear()
                words = words[1:]; continue
            if value in values:
                if len(words) < 2 or words[1][4]:
                    return unsupported('missing or uncertain wrapper option value')
                if cmd == 'env' and value in ('-u', '--unset'): child_env.pop(words[1][1], None)
                words = words[2:]; continue
            if any(value.startswith(option + '=') for option in values if option.startswith('--')):
                if cmd == 'env' and value.startswith('--unset='): child_env.pop(value[8:], None)
                words = words[1:]; continue
            if value.startswith('-'):
                return unsupported('wrapper option unsupported')
            break
        if cmd == 'token-broker' and not ended:
            return unsupported('token-broker delimiter missing')
        if cmd in ('env', 'sudo'):
            # Both treat any leading NAME=VALUE word (not only identifiers) as
            # an environment assignment, before the command.
            while words and not words[0][4] and '=' in words[0][1] and not words[0][1].startswith(('=', '\x00')):
                name, value = words[0][1].split('=', 1)
                child_env[name] = value
                words = words[1:]
            if words and not words[0][4] and words[0][1].startswith('='):
                # env treats '=x' as an assignment; never guess it as a command.
                return unsupported('wrapper assignment form unsupported')
        if not words: return None, [], child_scope, child_env
    return None, [], child_scope, child_env


def _command_tamper(text, scalars, depth, budget, issues=None):
    for tokens in _tamper_words(text, scalars, budget=budget):
        args, i = [], 0
        while i < len(tokens):
            op, value, _ = tokens[i][:3]
            if op:
                if i + 1 < len(tokens):
                    target = tokens[i + 1]
                    writing = value in ('>', '>>', '>|', '<>') or (value == '>&' and (target[4] or not re.fullmatch(r'[0-9]+|-', target[1])))
                    if writing and _protected_word(target): return True
                    i += 2; continue
            else: args.append(tokens[i])
            i += 1
        # Reserved words only introduce/continue/close commands (compound
        # structure is built by _shell_tree); the remaining words are a command.
        while args and not args[0][4] and args[0][3] in _RESERVED_PREFIX:
            reserved = args.pop(0)[3]
            if reserved == 'time':
                while args and not args[0][4] and args[0][3] in ('-p', '--'): args.pop(0)
        if args and not args[0][4] and args[0][3] in ('for', 'select'):
            # Loop header: the variable takes some word's value (a protected one
            # if any, top if any is uncertain); no words means "$@" (unknown).
            name = args[1][1] if len(args) > 1 and not args[1][4] else ''
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name):
                if issues is not None: issues.append('shell loop variable unsupported')
                continue
            words = args[3:] if len(args) > 2 and args[2][3] == 'in' else []
            value = next(((w[1] if any(n in w[1] for n in PROTECTED) else _UNCERTAIN)
                          for w in words if not w[4] and _protected_word(w)), None)
            if value is None and any(w[4] for w in words): value = _UNCERTAIN
            if not (len(args) > 2 and args[2][3] == 'in'):
                value = scalars.get('@', '\x00')  # no `in` list: "$@"
            scalars[name] = value if value is not None else (words[0][1] if words else '\x00')
            continue
        if args and not args[0][4] and args[0][3] in _DECLARE:
            # Declaration builtins assign NAME=value operands like plain
            # assignments (no dynamic scoping: function locals are global).
            nameref = False
            for w in args[1:]:
                raw = w[3]
                if raw.startswith(('-', '+')) and not w[4]:
                    nameref |= 'n' in raw[1:]
                    continue
                m = re.match(r'([A-Za-z_][A-Za-z_0-9]*)(\+?)=', raw)
                if not m:
                    if w[4] and issues is not None: issues.append('shell declaration operand unsupported')
                    continue
                if nameref:
                    if issues is not None: issues.append('shell nameref unsupported')
                    scalars[m[1]] = _UNCERTAIN
                    continue
                rhs = list(_tamper_words('value ' + raw[m.end():], scalars, budget=budget))
                word = rhs[0][1] if rhs and len(rhs[0]) == 2 else (False, '', (), '', False)
                old = scalars.get(m[1], '')
                if word[4] or (m[2] and old is _UNCERTAIN): scalars[m[1]] = _UNCERTAIN
                else: scalars[m[1]] = (old + word[1]) if m[2] else word[1]
            continue
        if args and not args[0][4] and args[0][3] in ('set', 'shift'):
            if args[0][3] == 'shift':
                _shift_positionals(args[1:], scalars)
                continue
            rest = args[1:]
            while rest and not rest[0][4] and rest[0][1][:1] in ('-', '+') and rest[0][1] != '--':
                rest = rest[2:] if rest[0][1] in ('-o', '+o') else rest[1:]
            if rest and not rest[0][4] and rest[0][1] == '--': rest = rest[1:]
            elif not rest: continue  # options only
            _bind_positionals(rest, scalars)
            continue
        assignments, prefix_words = [], []
        while args and re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', args[0][3]):
            prefix_words.append(args[0])
            assignments.append(args.pop(0)[3].split('=', 1))
        if not args:
            for key, raw_value in assignments:
                rhs = list(_tamper_words('value ' + raw_value, scalars, budget=budget))
                word = rhs[0][1] if rhs and len(rhs[0]) == 2 else (False, '', (), '', False)
                scalars[key] = _UNCERTAIN if word[4] else word[1]
            continue
        # Environment prefixes do not affect expansions of this command's argv;
        # they seed only a direct interpreter child. Reuse the already
        # expanded assignment words (no re-lex, no extra work). Limitation:
        # each prefix RHS sees the outer state, not earlier prefixes.
        prefix_env = {}
        for word in prefix_words:
            key, value = word[1].split('=', 1)
            prefix_env[key] = _UNCERTAIN if word[4] else value
        cmd, args, child_scope, child_env = _unwrap_tamper_command(args, budget, issues, prefix_env)
        if cmd is None: continue
        if child_scope and cmd in ('bash', 'sh', 'zsh'):
            if issues is not None: issues.append('wrapper child environment unsupported')
        if cmd == 'eval' and args:
            # eval runs its joined arguments as shell code in this shell, with
            # the current variables. An uncertain word built from a risky
            # value (protected fragment, top, risky substitution) holds.
            if any(w[4] for w in args) and ('__DSG_RISKY' in text or any(
                    _risky_value(scalars.get(n, '')) for n in re.findall(r'\$\{?!?([A-Za-z_][A-Za-z_0-9]*|[0-9@*])', text))):
                return True
            try:
                if _shell_tamper(' '.join(w[1] for w in args), depth + 1, budget, issues, dict(scalars)): return True
            except _TamperUnsupported as error:
                if budget[0] < 0: raise
                if issues is not None: issues.append(str(error))
        if cmd in ('unset', 'read', 'eval', 'source', '.'):
            _opaque_scalar_transfer(cmd, args, dict(scalars) if child_scope else scalars, issues)
        if cmd in ('bash', 'sh', 'zsh', 'python', 'python3') and any(word[4] for word in args):
            if issues is not None: issues.append('uncertain interpreter argument unsupported')
            continue
        if cmd in ('bash', 'sh', 'zsh') and len(args) >= 2 and args[0][1] in ('-c', '-lc', '-ec'):
            try:
                if _shell_tamper(args[1][1], depth + 1, budget, issues, child_env): return True
            except _TamperUnsupported as error:
                # Exhausted shared work in the child is not 'child clean'
                # (REVIEW-FLOOR B1): the caller's exhaustion rule decides.
                if budget[0] < 0: raise
                if issues is not None: issues.append(str(error))
        if cmd in ('python', 'python3') and len(args) >= 2 and args[0][1] == '-c':
            if _tamper('python', args[1][1]): return True
        if cmd == 'dd':
            for word in args:
                if word[4] and not word[3].startswith(('if=', 'of=')):
                    return True  # uncertainty can supply the output selector
                if word[1].startswith('of=') and _protected_word(_slice_word(word, 3)): return True
            continue
        # sudoedit writes its file operands; unknown options skip as flags,
        # so an option value becomes an extra operand (conservative).
        # Further mutators, conservatively: a protected operand anywhere holds
        # (mode/owner/link-target operands are never protected names).
        if cmd in ('shred', 'chmod', 'chown', 'chgrp', 'ln', 'link', 'rsync', 'find'):
            if cmd == 'find' and not any(w[4] or w[1] in ('-delete', '-exec', '-execdir', '-ok', '-okdir', '-fprint',
                                                           '-fprint0', '-fprintf', '-fls') for w in args):
                continue  # find without an action that mutates is a read
            if any(w[4] and w[1].startswith('-') for w in args): return True
            words = [w for w in args if not w[1].startswith('-') or cmd == 'find']
            if cmd == 'rsync' and not any(w[1] == '--remove-source-files' for w in args): words = words[-1:]
            if any(_protected_word(w) for w in words): return True
            if cmd == 'find':
                # -name/-path patterns are matched by find itself, quoted or not.
                names = ('denial-ledger.jsonl', 'denial-ledger.cap', 'denial-ledger.lock', 'denial-grants.json')
                for k, w in enumerate(args[:-1]):
                    if w[1] in ('-name', '-iname', '-path', '-ipath', '-wholename', '-iwholename', '-regex', '-iregex'):
                        pattern = args[k + 1][1]
                        base = pattern.rsplit('/', 1)[-1] or pattern
                        fold = (lambda s: s.lower()) if w[1].startswith('-i') else (lambda s: s)
                        if args[k + 1][4] or 'denial' in pattern.lower() or (w[1] not in ('-regex', '-iregex') and any(
                                fnmatch.fnmatchcase(fold(n), fold(base)) for n in names)):
                            return True
            for w in args:
                if '=' in w[1] and w[1].startswith('--') and _protected_word(_slice_word(w, w[1].index('=') + 1)):
                    return True
            continue
        if cmd not in ('rm', 'unlink', 'rmdir', 'cp', 'mv', 'tee', 'truncate', 'touch', 'install', 'sed', 'sudoedit'): continue
        # Known option arguments are not output operands. Unknown options remain
        # unsupported; this bounded adapter does not claim arbitrary CLI grammar.
        takes_value = {'--reference', '--date', '--size', '--suffix', '-t', '--target-directory'}
        takes_value |= {'truncate': {'-s', '-r'}, 'touch': {'-r', '-d', '-t'},
                        'install': {'-m', '-o', '-g'}, 'sed': {'-e', '-f'}}.get(cmd, set())
        operands, target, end_options, inplace, sed_script = [], None, False, False, False
        j = 0
        while j < len(args):
            word = args[j]; value = word[1]
            if cmd == 'sed' and word[4] and not end_options:
                return True  # top can include -i even with no retained dash
            if word[4] and value.startswith('-'):
                return True  # recognized mutator with uncertain option structure
            if not end_options and value == '--': end_options = True; j += 1; continue
            if not end_options and cmd == 'sed' and (value.startswith('-i') or value.startswith('--in-place')):
                inplace = True
                if value == '-i' and j + 1 < len(args) and args[j + 1][1] == '': j += 1
                j += 1; continue
            if not end_options and value.startswith('-t') and len(value) > 2 and cmd in ('cp', 'mv', 'install'):
                target = _slice_word(word, 2); j += 1; continue
            if not end_options and value.split('=', 1)[0] in takes_value:
                option = value.split('=', 1)[0]
                if '=' in value:
                    n = len(option) + 1; argument = _slice_word(word, n)
                else:
                    j += 1; argument = args[j] if j < len(args) else None
                if option in ('-t', '--target-directory') and cmd in ('cp', 'mv', 'install'): target = argument
                if option in ('-e', '-f') and cmd == 'sed': sed_script = True
                j += 1; continue
            if not end_options and value.startswith('-'): j += 1; continue
            operands.append(word); j += 1
        if cmd == 'sed':
            if not inplace: continue
            if not sed_script: operands = operands[1:]
        if cmd in ('cp', 'install') and not (cmd == 'install' and any(w[1] == '-d' for w in args)):
            operands = [target] if target else operands[-1:]
        elif cmd == 'mv' and target: operands.append(target)
        if any(_protected_word(word) for word in operands): return True
    return False


_PY_HOLE = '\x00'
_PY_MAX_ALTERNATIVES = 8
_PY_MAX_NODES = 32768
_PY_MAX_DEPTH = 6
_PY_ROUNDS = 6
_PY_MAX_WORK = 16 * 1024 * 1024  # characters produced/scanned per top-level analysis
_PY_SHELL_WORK = 1048576  # shell-analyzer units shared by every nested shell text of one top-level call
_PY_STATE = __import__('threading').local()
_PY_READ_FLAGS = {'O_RDONLY', 'O_CLOEXEC', 'O_NONBLOCK', 'O_NOFOLLOW', 'O_DIRECTORY', 'O_NOCTTY',
                  'O_NOATIME', 'O_NDELAY', 'O_BINARY', 'O_TEXT', 'O_LARGEFILE', 'O_PATH'}
_PY_PATH_CTORS = {'pathlib.Path', 'pathlib.PosixPath', 'pathlib.PurePath', 'pathlib.PurePosixPath',
                  'pathlib.WindowsPath', 'pathlib.PureWindowsPath'}
_PY_OPEN = {'open', 'builtins.open', 'io.open', 'codecs.open', 'io.FileIO', 'gzip.open', 'bz2.open', 'lzma.open'}
# qualname -> (positional indexes, keyword names) of affected path operands.
_PY_PATH_OPS = {
    'os.remove': ((0,), ('path',)), 'os.unlink': ((0,), ('path',)), 'os.rmdir': ((0,), ('path',)),
    'os.removedirs': ((0,), ('name',)), 'os.truncate': ((0,), ('path',)), 'os.chmod': ((0,), ('path',)),
    'os.lchmod': ((0,), ('path',)), 'os.chown': ((0,), ('path',)), 'os.lchown': ((0,), ('path',)),
    'os.utime': ((0,), ('path',)), 'os.mkdir': ((0,), ('path',)), 'os.makedirs': ((0,), ('name',)),
    'os.mkfifo': ((0,), ('path',)), 'os.mknod': ((0,), ('path',)), 'os.setxattr': ((0,), ('path',)),
    'os.removexattr': ((0,), ('path',)), 'os.rename': ((0, 1), ('src', 'dst')),
    'os.replace': ((0, 1), ('src', 'dst')), 'os.renames': ((0, 1), ('old', 'new')),
    'os.link': ((1,), ('dst',)), 'os.symlink': ((1,), ('dst',)), 'shutil.rmtree': ((0,), ('path',)),
    'shutil.copyfile': ((1,), ('dst',)), 'shutil.copytree': ((1,), ('dst',)), 'shutil.copymode': ((1,), ('dst',)),
    'shutil.copystat': ((1,), ('dst',)), 'shutil.chown': ((0,), ('path',)),
}
_PY_DERIVED_OPS = {'shutil.copy': False, 'shutil.copy2': False, 'shutil.move': True}  # True: source also affected
_PY_PATH_METHODS = {'write_text', 'write_bytes', 'touch', 'unlink', 'rmdir', 'mkdir', 'chmod', 'lchmod',
                    'symlink_to', 'hardlink_to', 'link_to'}
_PY_SHELL_CALLS = {'os.system': 'command', 'os.popen': 'cmd', 'subprocess.run': 'args', 'subprocess.call': 'args',
                   'subprocess.check_call': 'args', 'subprocess.check_output': 'args', 'subprocess.Popen': 'args',
                   'subprocess.getoutput': 'cmd', 'subprocess.getstatusoutput': 'cmd',
                   'asyncio.create_subprocess_shell': 'cmd', 'pty.spawn': 'argv'}
# argv given as one list operand (index) or as variadic words starting at index.
_PY_EXEC_LIST = {'os.execv': 1, 'os.execve': 1, 'os.execvp': 1, 'os.execvpe': 1, 'os.spawnv': 2, 'os.spawnve': 2,
                 'os.spawnvp': 2, 'os.spawnvpe': 2, 'os.posix_spawn': 1, 'os.posix_spawnp': 1}
_PY_EXEC_VARIADIC = {'os.execl': 1, 'os.execle': 1, 'os.execlp': 1, 'os.execlpe': 1, 'os.spawnl': 2,
                     'os.spawnle': 2, 'os.spawnlp': 2, 'os.spawnlpe': 2, 'asyncio.create_subprocess_exec': 0}


def _py_work(amount):
    """Shared character-work budget for one top-level analysis (W1b).

    Exhaustion is an explicit analysis bound: the caller applies the baseline
    co-occurrence rule, never 'no mutation'."""
    _PY_STATE.work = getattr(_PY_STATE, 'work', 0) + amount
    if _PY_STATE.work > _PY_MAX_WORK:
        raise _TamperUnsupported('python analysis bound: work')


def _py_reset():
    """Fresh shared budgets for one top-level _tamper/_python_analysis call."""
    _PY_STATE.work = 0
    _PY_STATE.shell_budget = [_PY_SHELL_WORK]
    _PY_STATE.shell_cache = {}


def _py_baseline(code):
    """Reviewed conservative rule for code the bounded analysis cannot finish.

    Protected name (also after literal folding) plus any Python or shell write
    keyword: bound exhaustion holds shell-wrapped deletes/redirects as well."""
    candidates = [code]
    try:
        candidates.extend(value for node in ast.walk(ast.parse(code)) if (value := _literal(node)) is not None)
    except (ValueError, SyntaxError, RecursionError, MemoryError):
        pass
    return bool(any(name in candidate for candidate in candidates for name in PROTECTED)
                and (WRITE_PY.search(code) or WRITE_SHELL.search(code) or _PY_BASELINE_EXTRA.search(code)))


# Bound fallback only: further file-mutator spellings the regex baseline lacked
# (launchers are covered by the shell write keywords of their command text).
_PY_BASELINE_EXTRA = re.compile(r"copy|move|chmod|chown|mkdir|symlink|link|touch|dump", re.I)


def _py_cap(values):
    """Bounded alternative set. Protected-bearing alternatives are kept first."""
    out = set()
    for kind, value in values:
        _py_work(len(value) + 1)
        if len(value) > 262144:
            names = [name for name in PROTECTED if name in value]
            value = _PY_HOLE + (_PY_HOLE.join(names) + _PY_HOLE if names else '')
        out.add((kind, re.sub('\x00+', _PY_HOLE, value) if _PY_HOLE * 2 in value else value))
    if len(out) <= _PY_MAX_ALTERNATIVES:
        return frozenset(out)
    ranked = sorted(out, key=lambda item: (not _py_protected(item[1]), item[0] != 'argv', item))
    return frozenset(ranked[:_PY_MAX_ALTERNATIVES - 1]) | {('unknown', _PY_HOLE)}


def _py_protected(value):
    _py_work(len(value) + 1)
    if any(name in value for name in PROTECTED): return True
    if _root_dir(value) or _glob_protected(value, [True] * len(value)): return True
    base = value.rsplit('/', 1)[-1]
    if not base.startswith('denial') or _PY_HOLE in base: return False
    return any(fnmatch.fnmatchcase(name, base) for name in
               ('denial-ledger.jsonl', 'denial-ledger.cap', 'denial-ledger.lock', 'denial-grants.json'))


def _py_join(left, right):
    if right.startswith('/'): return right
    if not left: return right
    return left.rstrip('/') + '/' + right if left != '/' else '/' + right


def _py_join_words(words):
    """Linear equivalent of folding _py_join over words (trailing-slash edge cases approximate)."""
    start = max((i for i, w in enumerate(words) if w.startswith('/')), default=0)
    out, empty, root = [], True, False
    for word in words[start:]:
        if empty or word.startswith('/'):
            out, empty, root = [word], word == '', word == '/'
        elif root:
            out.append(word); root = False
        else:
            out[-1] = out[-1].rstrip('/'); out.append('/'); out.append(word)
    return ''.join(out)


def _py_sequence(sequence, finish, issues=None):
    """Combine per-position alternative sets in linear time.

    Alternatives multiply only while the total stays <= the cap; after that each
    position appends its protected/argv-first value and the bound is reported.
    `finish(words)` builds the final string once per alternative.
    """
    alternatives = [[]]
    for values in sequence:
        ordered = sorted(values, key=lambda item: (not _py_protected(item[1]), item[0] != 'argv', item))
        if len(ordered) == 1:
            for words in alternatives: words.append(ordered[0])
        elif len(alternatives) * len(ordered) <= _PY_MAX_ALTERNATIVES:
            alternatives = [words + [item] for words in alternatives for item in ordered]
        else:
            if issues is not None: issues.append('python alternative bound in sequence')
            for words in alternatives: words.append(ordered[0])
    return [finish(words) for words in alternatives]


class _PyAnalysis:
    """Bounded AST mutation-site analysis. Potential effects, not reachability.

    Values are finite alternative sets of (kind, text); unknown parts are NUL
    holes. Bindings are flow-insensitive per lexical scope, so a later store
    never erases a prior possible value. No code is executed or imported.
    """

    UNKNOWN = frozenset({('unknown', _PY_HOLE)})

    def __init__(self, code, depth):
        self.depth, self.issues, self.found = depth, [], False
        if len(code) > 262144: raise _TamperUnsupported('python analysis bound: source length')
        self.tree = tree = ast.parse(code)
        nodes = 0
        for _ in ast.walk(tree):
            nodes += 1
            if nodes > _PY_MAX_NODES: raise _TamperUnsupported('python analysis bound: node count')
        self.parent = {tree: None}
        self.node_scope, self.local, self.decl = {}, {tree: set()}, {}
        self.assigns, self.imports, self.defs, self.star = [], {}, {}, set()
        self._index(tree, tree)
        for (scope, name), kind in self.decl.items():
            self.local[scope].discard(name)
            if kind == 'global': self.local[tree].add(name)  # a function-only global still binds the module
        self.env = {}

    # ----- indexing -------------------------------------------------------
    def _bind(self, scope, name):
        self.local[scope].add(name)

    def _index(self, node, scope):
        self.node_scope[node] = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            parent = scope
            while isinstance(parent, ast.ClassDef): parent = self.parent[parent]
            self.parent[node], self.local[node] = parent, set()
            if not isinstance(node, ast.Lambda): self._bind(scope, node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.defs.setdefault(node.name, []).append(node)
            arguments = getattr(node, 'args', None)
            if arguments:
                for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                            arguments.vararg, arguments.kwarg):
                    if arg: self._bind(node, arg.arg)
            for field, value in ast.iter_fields(node):
                target = node if field == 'body' else scope
                for child in (value if isinstance(value, list) else [value]):
                    if isinstance(child, ast.AST): self._index(child, target)
            return
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                self.decl[scope, name] = 'global' if isinstance(node, ast.Global) else 'nonlocal'
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split('.')[0]
                self.imports.setdefault(name, set()).add(alias.name if alias.asname else alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if alias.name != '*':
                    self.imports.setdefault(alias.asname or alias.name, set()).add(node.module + '.' + alias.name)
                else:
                    # Any otherwise unbound call name may come from the module.
                    self.star.add(node.module)
        elif isinstance(node, ast.Assign):
            for target in node.targets: self._target(target, node.value, scope)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            self._target(node.target, node.value, scope)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                op = 'add' if isinstance(node.op, ast.Add) else 'div' if isinstance(node.op, ast.Div) else 'unknown'
                self._bind(scope, node.target.id)
                self.assigns.append((scope, node.target.id, op, node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            self._target(node.target, ('iter', node.iter), scope)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None: self._target(item.optional_vars, None, scope)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self._bind(scope, node.name); self.assigns.append((scope, node.name, 'unknown', None))
        for child in ast.iter_child_nodes(node): self._index(child, scope)

    def _target(self, target, value, scope):
        if isinstance(target, ast.Name):
            self._bind(scope, target.id)
            if isinstance(value, tuple):
                self.assigns.append((scope, target.id, 'iter', value[1]))
            else:
                self.assigns.append((scope, target.id, 'set' if value is not None else 'unknown', value))
        elif isinstance(target, (ast.Tuple, ast.List)):
            elts = value.elts if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts) \
                and not any(isinstance(e, ast.Starred) for e in target.elts) else None
            for index, element in enumerate(target.elts):
                self._target(element.value if isinstance(element, ast.Starred) else element,
                             elts[index] if elts else None, scope)
        elif isinstance(target, ast.Starred):
            self._target(target.value, None, scope)

    # ----- lookup ---------------------------------------------------------
    def _owner(self, name, scope):
        declaration = self.decl.get((scope, name))
        if declaration == 'global': return self.tree
        if declaration == 'nonlocal':
            parent = self.parent[scope]
            while parent is not None and parent is not self.tree:
                if name in self.local[parent]: return parent
                parent = self.parent[parent]
            return None
        return scope

    def _resolve_scope(self, name, scope):
        while scope is not None:
            owner = self._owner(name, scope)
            if owner is not scope: return owner
            if name in self.local[scope]: return scope
            scope = self.parent[scope]
        return None

    def _lookup(self, name, scope):
        owner = self._resolve_scope(name, scope)
        if owner is None: return self.UNKNOWN
        return self.env.get((owner, name), frozenset()) or self.UNKNOWN

    def _qualnames(self, func, scope, depth=0):
        """Possible library identities of a call target; locals never hide builtins."""
        if depth > 8: return set()
        if isinstance(func, ast.Name):
            names = set(self.imports.get(func.id, ()))
            if func.id in ('open', 'exec', 'eval', 'compile', 'print', 'str'): names.add(func.id)
            if func.id in SHELL_TOOLS | FILE_TOOLS | PY_TOOLS and not names: names.add('hermes_tools.' + func.id)
            if self._resolve_scope(func.id, scope) is None or func.id in self.imports:
                names.update(module + '.' + func.id for module in self.star)
            return names
        if isinstance(func, ast.Attribute):
            return {base + '.' + func.attr for base in self._qualnames(func.value, scope, depth + 1)}
        if isinstance(func, ast.Call) and func.args:
            # __import__('os') / importlib.import_module('os') with a literal name.
            if self._qualnames(func.func, scope, depth + 1) & {'__import__', 'importlib.import_module'} or \
                    isinstance(func.func, ast.Name) and func.func.id == '__import__':
                return {v for kind, v in self.eval(func.args[0], scope) if _PY_HOLE not in v}
        return set()

    # ----- evaluation -----------------------------------------------------
    def _product(self, lefts, rights, combine):
        return _py_cap((kind, combine(lv, rv)) for kind, lv in lefts for _, rv in rights)

    def eval(self, node, scope, depth=0):
        """Never empty: an empty result would silently mean 'no possible value'."""
        return self._eval(node, scope, depth) or self.UNKNOWN

    def _eval(self, node, scope, depth=0):
        if depth > 24 or node is None: return self.UNKNOWN
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str): return frozenset({('str', node.value)})
            if isinstance(node.value, bytes): return frozenset({('str', node.value.decode('latin-1'))})
            return self.UNKNOWN
        if isinstance(node, ast.Name): return self._lookup(node.id, scope)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            # Containers are the union of their elements (iteration/subscript),
            # plus one argv alternative so a bound list stays a command line.
            elements = [e.value if isinstance(e, ast.Starred) else e for e in node.elts]
            values = [self.eval(e, scope, depth + 1) for e in elements]
            union = frozenset().union(*values)
            return _py_cap(union | self._argv(node, scope, depth, values))
        if isinstance(node, ast.Dict):
            return _py_cap(frozenset().union(*(self.eval(v, scope, depth + 1) for v in node.values)))
        if isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Slice):
            return self.eval(node.value, scope, depth + 1) if isinstance(node.value, (ast.Name, ast.List, ast.Tuple, ast.Dict, ast.Subscript)) else self.UNKNOWN
        if isinstance(node, ast.NamedExpr): return self.eval(node.value, scope, depth + 1)
        if isinstance(node, ast.IfExp):
            return _py_cap(self.eval(node.body, scope, depth + 1) | self.eval(node.orelse, scope, depth + 1))
        if isinstance(node, ast.BoolOp):
            return _py_cap(frozenset().union(*(self.eval(v, scope, depth + 1) for v in node.values)))
        if isinstance(node, ast.JoinedStr):
            pieces = [self.eval(part.value, scope, depth + 1) if isinstance(part, ast.FormattedValue)
                      and part.format_spec is None else self.eval(part, scope, depth + 1)
                      if isinstance(part, ast.Constant) else self.UNKNOWN for part in node.values]
            return _py_cap(_py_sequence(pieces, lambda words: ('str', ''.join(v for _, v in words)), self.issues))
        if isinstance(node, ast.BinOp):
            left, right = self.eval(node.left, scope, depth + 1), self.eval(node.right, scope, depth + 1)
            if isinstance(node.op, ast.Add):
                # List concatenation keeps command-line word boundaries.
                return _py_cap(('argv', lv + ' ' + rv) if lk == rk == 'argv' else (lk, lv + rv)
                               for lk, lv in left for rk, rv in right)
            if isinstance(node.op, ast.Div):
                return _py_cap(('path', _py_join(lv, rv)) for _, lv in left for _, rv in right)
            if isinstance(node.op, ast.Mod):
                return _py_cap((kind, re.sub(r'%(?:\([^)]*\))?[-#0 +]*[\d*]*(?:\.[\d*]+)?[a-zA-Z%]', _PY_HOLE, v))
                               for kind, v in left)
            return self.UNKNOWN
        if isinstance(node, ast.Attribute):
            receiver = self.eval(node.value, scope, depth + 1)
            if node.attr == 'parent': return _py_cap(('path', v.rsplit('/', 1)[0] if '/' in v else _PY_HOLE) for _, v in receiver)
            if node.attr in ('name', 'stem'): return _py_cap(('str', v.rsplit('/', 1)[-1]) for _, v in receiver)
            return self.UNKNOWN
        if isinstance(node, ast.Call): return self._eval_call(node, scope, depth)
        return self.UNKNOWN

    def _arg(self, call, index, keyword):
        for item in call.keywords:
            if item.arg == keyword: return item.value
        unknown = ast.Constant(value=None)  # an operand exists but has no static identity
        if any(isinstance(a, ast.Starred) for a in call.args[:index + 1]): return unknown
        if len(call.args) > index: return call.args[index]
        return unknown if any(item.arg is None for item in call.keywords) else None

    def _eval_call(self, node, scope, depth):
        names = self._qualnames(node.func, scope)
        values = [self.eval(a, scope, depth + 1) for a in node.args if not isinstance(a, ast.Starred)]
        if names & _PY_PATH_CTORS:
            if not values: return frozenset({('path', '')})
            return _py_cap(_py_sequence(values, lambda words: ('path', _py_join_words([v for _, v in words])), self.issues))
        if names & {'os.path.join', 'posixpath.join'} and values:
            return _py_cap(_py_sequence(values, lambda words: ('str', _py_join_words([v for _, v in words])), self.issues))
        if names & {'os.path.expanduser', 'os.path.abspath', 'os.path.realpath', 'os.path.normpath',
                    'os.path.expandvars', 'os.fspath', 'os.fsdecode', 'glob.glob', 'glob.iglob', 'str'} and values:
            return _py_cap(('str', v) for _, v in values[0])
        if names & {'os.path.basename'} and values:
            return _py_cap(('str', v.rsplit('/', 1)[-1]) for _, v in values[0])
        if names & {'os.path.dirname'} and values:
            return _py_cap(('str', v.rsplit('/', 1)[0] if '/' in v else _PY_HOLE) for _, v in values[0])
        if isinstance(node.func, ast.Attribute):
            receiver = self.eval(node.func.value, scope, depth + 1)
            attr = node.func.attr
            paths = frozenset(item for item in receiver if item[0] == 'path')
            if paths and attr == 'joinpath':
                return _py_cap(_py_sequence([paths, *values], lambda words: ('path', _py_join_words([v for _, v in words])),
                                            self.issues))
            if paths and attr in ('expanduser', 'resolve', 'absolute', 'with_suffix') :
                return paths if attr != 'with_suffix' else _py_cap(('path', v + _PY_HOLE) for _, v in paths)
            if paths and attr == 'with_name' and values:
                return _py_cap(('path', _py_join(a.rsplit('/', 1)[0] if '/' in a else '', b))
                               for _, a in paths for _, b in values[0])
            if attr == 'glob' or attr == 'rglob':
                return _py_cap(('path', _py_join(a, b)) for _, a in paths for _, b in (values[0] if values else self.UNKNOWN))
            strings = frozenset(item for item in receiver if item[0] == 'str')
            if strings and attr == 'format':
                return _py_cap(('str', re.sub(r'\{[^{}]*\}', _PY_HOLE, v)) for _, v in strings)
        return self.UNKNOWN

    # ----- fixpoint -------------------------------------------------------
    def solve(self):
        for _ in range(_PY_ROUNDS):
            changed = False
            updates = {}
            for scope, name, op, value in self.assigns:
                owner = self._owner(name, scope)
                if owner is None: continue
                if op == 'set': new = self.eval(value, scope)
                elif op == 'iter':
                    new = self.eval(value, scope)
                elif op == 'add': new = self._product(self._lookup(name, scope), self.eval(value, scope), lambda a, b: a + b)
                elif op == 'div':
                    new = _py_cap(('path', _py_join(a, b)) for _, a in self._lookup(name, scope) for _, b in self.eval(value, scope))
                else: new = self.UNKNOWN
                updates.setdefault((owner, name), set()).update(new)
            for call in self.calls():
                self._bind_params(call, updates)
            for key, values in updates.items():
                merged = _py_cap(self.env.get(key, frozenset()) | frozenset(values))
                if merged != self.env.get(key):
                    self.env[key], changed = merged, True
            if not changed: return
        self.issues.append('python binding fixpoint bound reached')

    def calls(self):
        return (node for node in self.node_scope if isinstance(node, ast.Call))

    def _bind_params(self, call, updates):
        if not isinstance(call.func, ast.Name): return
        scope = self.node_scope[call]
        for definition in self.defs.get(call.func.id, ()):
            arguments = definition.args
            positional = [*arguments.posonlyargs, *arguments.args]
            for index, value in enumerate(call.args):
                if isinstance(value, ast.Starred): break
                if index < len(positional):
                    updates.setdefault((definition, positional[index].arg), set()).update(self.eval(value, scope))
            for item in call.keywords:
                if item.arg:
                    updates.setdefault((definition, item.arg), set()).update(self.eval(item.value, scope))

    def defaults(self):
        for definitions in self.defs.values():
            for definition in definitions:
                arguments = definition.args
                positional = [*arguments.posonlyargs, *arguments.args]
                outer = self.node_scope[definition]
                for arg, default in zip(positional[len(positional) - len(arguments.defaults):], arguments.defaults):
                    self.env[definition, arg.arg] = self.eval(default, outer)
                for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
                    if default is not None: self.env[definition, arg.arg] = self.eval(default, outer)

    # ----- mutation sites -------------------------------------------------
    def _hit(self, values):
        return any(_py_protected(v) for _, v in values)

    def _mode_mutates(self, values, default):
        for _, mode in (values if values is not None else [('str', default)]):
            if _PY_HOLE in mode or any(c in mode for c in 'wax+'): return True
        return False

    def _flags_mutate(self, node):
        if node is None: return False
        # Only os.O_* / bare O_* names, 0 and bitwise OR are understood; any
        # other flag expression is unknown and therefore possibly mutating.
        def read_only(part):
            if isinstance(part, ast.BinOp) and isinstance(part.op, ast.BitOr):
                return read_only(part.left) and read_only(part.right)
            if isinstance(part, ast.Constant): return part.value == 0
            if isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name): return part.attr in _PY_READ_FLAGS
            if isinstance(part, ast.Name): return part.id in _PY_READ_FLAGS
            return False
        return not read_only(node)

    def _shell(self, values):
        for _, text in values:
            if _PY_HOLE in text and text.strip(_PY_HOLE + ' ') == '':
                self.issues.append('python shell command unresolved'); continue
            cache = getattr(_PY_STATE, 'shell_cache', None)
            if cache is None: _py_reset(); cache = _PY_STATE.shell_cache
            # Key includes the python nesting depth: a nested python -c child is
            # analyzed precisely or by the depth-bound baseline depending on it.
            key = (text, getattr(_PY_STATE, 'depth', 0), _legacy(), getattr(_SHELL_MODE, 'pass_active', False))
            if key in cache:
                found, issue = cache[key]  # same text at the same depth: same verdict
            else:
                if len(text) > 262144:
                    raise _TamperUnsupported('python analysis bound: nested shell length')
                _py_work(len(text) + 1)
                # Every nested shell text of one top-level call draws on ONE shared
                # shell-analyzer budget; exhaustion is a bound, never 'no mutation'.
                budget = _PY_STATE.shell_budget
                shell_issues = []
                try:
                    found = _shell_tamper(text, budget=budget, issues=shell_issues)
                except _TamperUnsupported as error:
                    # RecursionError propagates: the caller applies the baseline rule.
                    found = False
                    shell_issues.append(str(error))
                if budget[0] < 0:
                    # Exhausted shared shell work fails closed for the whole
                    # Python code, as at top level: later nested strings are
                    # never examined (REVIEW-FLOOR B1-CORRECTION F2).
                    found = True
                issue = '; '.join(dict.fromkeys(shell_issues)) or None
                if found: return True
                if budget[0] < 0 or getattr(_PY_STATE, 'work', 0) > _PY_MAX_WORK:
                    # A nested child that exhausted either shared budget was only
                    # baseline-checked: the outer code gets the baseline rule too.
                    raise _TamperUnsupported('python analysis bound: nested shell work')
                cache[key] = (found, issue)
            if issue: self.issues.append(issue)
            if found: return True
        return False

    def _argv(self, node, scope, depth=0, element_values=None):
        """Command-line alternatives over element combinations, built in linear time."""
        if element_values is None:
            element_values = [self.eval(e.value if isinstance(e, ast.Starred) else e, scope, depth + 1) for e in node.elts]
        quote = lambda kind, word: shlex.quote(word) if _PY_HOLE not in word and kind != 'argv' else word
        return _py_cap(_py_sequence(element_values, lambda words: ('argv', ' '.join(quote(k, w) for k, w in words)),
                                    self.issues))

    def _command_values(self, node, scope):
        if isinstance(node, (ast.List, ast.Tuple)): return self._argv(node, scope)
        return self.eval(node, scope)

    def check(self):
        self.defaults()
        self.solve()
        for call in self.calls():
            if self._check_call(call, self.node_scope[call]):
                self.found = True
                return True
        return False

    def _check_call(self, call, scope):
        names = self._qualnames(call.func, scope)
        arg = lambda index, keyword: self._arg(call, index, keyword)
        known = names & (_PY_OPEN | {'os.open'} | set(_PY_PATH_OPS) | set(_PY_DERIVED_OPS) | set(_PY_SHELL_CALLS)
                         | set(_PY_EXEC_LIST) | set(_PY_EXEC_VARIADIC)) or any(
            n.startswith('hermes_tools.') for n in names)
        if known and (any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords)):
            # Unpacked operands have no positional identity: any protected value holds.
            dynamic = [a.value for a in call.args if isinstance(a, ast.Starred)] + [k.value for k in call.keywords if k.arg is None]
            if any(self._hit(self.eval(node, scope)) for node in dynamic): return True
        for name in names & set(_PY_EXEC_LIST):
            node = arg(_PY_EXEC_LIST[name], 'argv')
            if node is not None and self._shell(self._command_values(node, scope)): return True
        for name in names & set(_PY_EXEC_VARIADIC):
            words = call.args[_PY_EXEC_VARIADIC[name]:]
            if words and self._shell(self._argv(ast.List(elts=list(words), ctx=ast.Load()), scope)): return True
        if names & _PY_OPEN:
            path = arg(0, 'file') or arg(0, 'filename')
            mode = arg(1, 'mode')
            if path is not None and self._hit(self.eval(path, scope)) and \
                    self._mode_mutates(None if mode is None else self.eval(mode, scope), 'r'):
                return True
        if 'os.open' in names:
            path = arg(0, 'path')
            if path is not None and self._hit(self.eval(path, scope)) and self._flags_mutate(arg(1, 'flags')): return True
        for name in names & set(_PY_PATH_OPS):
            indexes, keywords = _PY_PATH_OPS[name]
            for index, keyword in zip(indexes, keywords):
                node = arg(index, keyword)
                if node is not None and self._hit(self.eval(node, scope)): return True
        for name in names & set(_PY_DERIVED_OPS):
            source, destination = arg(0, 'src'), arg(1, 'dst')
            sources = self.eval(source, scope) if source is not None else self.UNKNOWN
            if _PY_DERIVED_OPS[name] and self._hit(sources): return True
            if destination is not None:
                for _, dst in self.eval(destination, scope):
                    if _py_protected(dst): return True
                    if dst.endswith(('/', _PY_HOLE)) or dst in ('.', '..') or dst.endswith('/.'):
                        if any(_py_protected(_py_join(dst, s.rsplit('/', 1)[-1])) for _, s in sources): return True
        for name in names:
            if name in _PY_SHELL_CALLS or name.startswith('hermes_tools.') and name.split('.')[-1] in SHELL_TOOLS:
                keyword = _PY_SHELL_CALLS.get(name, 'command')
                node = arg(0, keyword)
                if node is not None and self._shell(self._command_values(node, scope)): return True
            if name.startswith('hermes_tools.') and name.split('.')[-1] in FILE_TOOLS:
                node = arg(0, 'path')
                if node is not None and self._hit(self.eval(node, scope)): return True
                patch_text = arg(99, 'patch')
                if patch_text is not None and any(_py_protected(line) for _, v in self.eval(patch_text, scope)
                                                  for line in re.findall(r'^\*\*\* (?:Update File|Add File|Delete File|Move to): (.+)$', v, re.M)):
                    return True
            if name.startswith('hermes_tools.') and name.split('.')[-1] in PY_TOOLS or name in ('exec', 'eval', 'compile'):
                node = arg(0, 'code') if name.startswith('hermes_tools.') else arg(0, 'source')
                if node is not None and self._nested(self.eval(node, scope)): return True
        if isinstance(call.func, ast.Attribute):
            attr = call.func.attr
            receiver = self.eval(call.func.value, scope)
            paths = frozenset(item for item in receiver if item[0] == 'path')
            if paths:
                if attr in _PY_PATH_METHODS and self._hit(paths): return True
                if attr == 'open' and self._hit(paths):
                    mode = arg(0, 'mode')
                    if self._mode_mutates(None if mode is None else self.eval(mode, scope), 'r'): return True
                if attr in ('rename', 'replace', 'move', 'copy', 'copy_into', 'move_into'):
                    target = arg(0, 'target')
                    if attr not in ('copy', 'copy_into') and self._hit(paths): return True
                    if target is not None and self._hit(self.eval(target, scope)): return True
        return False

    def _nested(self, values):
        for _, code in values:
            if _PY_HOLE in code:
                self.issues.append('python dynamic code unresolved'); continue
            try:
                found, issue = _python_analysis(code, self.depth + 1)
            except SyntaxError as error:
                # Literal code that cannot compile cannot run either.
                self.issues.append('python nested code unanalyzable: ' + type(error).__name__); continue
            except (RecursionError, MemoryError, ValueError) as error:
                # Bounds/limits: keep the baseline rule for this nested code only,
                # and continue with the remaining outer mutation sites.
                self.issues.append('python nested analysis bound: ' + type(error).__name__)
                found, issue = _py_baseline(code), None
                if found: return True
                if getattr(_PY_STATE, 'work', 0) > _PY_MAX_WORK:
                    # Exhausted shared work: the outer code gets the baseline rule too.
                    raise _TamperUnsupported('python analysis bound: work')
            if issue: self.issues.append(issue)
            if found: return True
        return False


def _python_analysis(code, depth=0):
    """Return (mutation found, unsupported reason); not a sandbox verdict."""
    if depth > _PY_MAX_DEPTH:
        # Beyond the nesting bound only the reviewed baseline co-occurrence applies.
        return _py_baseline(code), 'python nesting bound'
    if depth == 0 and not getattr(_PY_STATE, 'active', False):
        # Direct outermost call: one shared work/shell budget for everything nested.
        _PY_STATE.active = True
        _py_reset()
        try:
            return _python_analysis(code, depth)
        finally:
            _PY_STATE.active = False
    analysis = _PyAnalysis(code, depth)
    # RecursionError/MemoryError propagate: callers apply the baseline rule to
    # this code instead of treating an unfinished analysis as "no mutation".
    found = analysis.check()
    return found, '; '.join(dict.fromkeys(analysis.issues)) or None


def _shell_analysis(text):
    """Return (mutation found, unsupported reason); not a sandbox verdict."""
    issues = []
    budget = [1048576]
    try:
        found = _shell_tamper(text, budget=budget, issues=issues)
    except (_TamperUnsupported, RecursionError) as error:
        # The text was not fully inspected (never 'no mutation'). Exhausted
        # shared work or recursion fails closed: the whole text holds. A
        # keyword test is not enough (REVIEW-FLOOR B1, B1-CORRECTION F1:
        # unlink/rmdir/touch/sudoedit, split command names `$A$B`). Other
        # unsupported structure keeps the reviewed baseline rule.
        if budget[0] < 0 or isinstance(error, RecursionError):
            found = True
            issues.append('shell work exhausted; held')
        else:
            found = _shell_baseline(text)
        issues.append(str(error))
    return found, '; '.join(dict.fromkeys(issues)) or None


def _tamper(kind, text):
    """Top-level entry: all nested python/shell analyses share one set of budgets."""
    if getattr(_PY_STATE, 'active', False):
        return _tamper_once(kind, text)
    _PY_STATE.active = True
    _PY_STATE.cwd_root = _cd_into_root(text)
    _py_reset()
    try:
        return _tamper_once(kind, text)
    finally:
        _PY_STATE.active = False
        _PY_STATE.cwd_root = False


def _tamper_once(kind, text):
    if kind == 'overflow':
        return None  # held by classify as static analysis unavailable
    if kind == 'shell':
        found, issue = _shell_analysis(text)
        if found:
            return "DENIAL GUARD: blocked apparent ledger/grant mutation. Agent-written state is not authority. No in-agent reset or grant route exists."
        return None
    candidates = [text]
    if kind == "python":
        depth = getattr(_PY_STATE, 'depth', 0)
        if depth <= _PY_MAX_DEPTH:
            _PY_STATE.depth = depth + 1
            try:
                found, issue = _python_analysis(text)
            except (ValueError, SyntaxError, RecursionError, MemoryError):
                found = None  # unanalyzable: baseline conservative rule below
            finally:
                _PY_STATE.depth = depth
            if found is not None:
                return "DENIAL GUARD: blocked apparent ledger/grant mutation. Agent-written state is not authority. No in-agent reset or grant route exists." if found else None
        # Only code the bounded analysis cannot parse (syntax/version skew,
        # size/node/nesting/work bounds) keeps the reviewed baseline co-occurrence.
        return "DENIAL GUARD: blocked apparent ledger/grant mutation. Agent-written state is not authority. No in-agent reset or grant route exists." if _py_baseline(text) else None
    if any(name in candidate for candidate in candidates for name in PROTECTED) and (kind == "file" or kind == "shell" and WRITE_SHELL.search(text)):
        return "DENIAL GUARD: blocked apparent ledger/grant mutation. Agent-written state is not authority. No in-agent reset or grant route exists."
    return None


def _resource_match(kind, text, row, cache=None):
    if row["resource"] == "UNKNOWN": return True
    if not re.fullmatch(r"arn:[a-z0-9-]+:ec2:[a-z0-9-]+:[0-9]{12}:instance/i-[0-9a-f]+", row["resource"]):
        return True  # InstanceIds cannot disprove a document or wildcard denial.
    # Only the SSM instance adapter can establish an explicit unrelated target.
    # Other operations retain conservative matching, including unknown scope.
    cache = {} if cache is None else cache
    if (kind, text) not in cache:
        cache[kind, text] = _shell_invocations(text) if kind == "shell" else _py_invocations(text)
    calls = cache[kind, text]
    matching = [resources for s, o, resources in calls
                if row["action"] == "UNKNOWN" or _action_matches(row["action"], [(s, o)], row.get("action_kind"))]
    return any(resources is None or _rid(row["resource"]).lower() in resources for resources in matching)


def classify(tool_name, args, now=None, *, root=None, **native_ids):
    texts = _call_texts(tool_name, args)
    try:
        _PY_STATE.ledger_root = str(_root(root)) if root is not None else None
    except (OSError, RuntimeError, ValueError):
        _PY_STATE.ledger_root = None
    try:
        for kind, text in texts:
            why = _tamper(kind, text)
            if why:
                return why
    finally:
        _PY_STATE.ledger_root = None
    invocations = [(kind, text, _calls(kind, text)) for kind, text in texts if kind != "file"]
    if any(("UNKNOWN", "UNKNOWN") in calls for _, _, calls in invocations):
        return "DENIAL GUARD: AWS-shaped invocation held: static analysis unavailable."
    # A recorded denial is evidence for the exact invocation even when static
    # parsing cannot see through a script or map its CLI operation to IAM.
    # Empty extraction is not an identity: unrelated unsupported tools share it.
    if not invocations:
        return None
    state = read_ledger(root=root)
    rows = state.rows
    if state.status == "UNHEALTHY":
        # Durable rows still apply; add this process's bounded overflow
        # sample of denials observed after the cap (not durable).
        rows = rows + list(_OVERFLOW.get(str(_root(root)), ()))
    fingerprint = _fingerprint(tool_name, args)
    for row in rows:
        if row.get("fingerprint") == fingerprint:
            return message(row, invocation_identity(texts[0][1]))
    if not any(calls for _, _, calls in invocations):
        return None
    if state.status == "UNHEALTHY" and is_mutating(tool_name, args):
        return "DENIAL GUARD: recognized AWS mutation held: ledger unhealthy (" + state.reason + ")."
    cache = {}
    for kind, text, calls in invocations:
        for row in rows:
            if row["action"] == row["resource"] == "UNKNOWN":
                matches = row.get("fingerprint") == fingerprint
            else:
                action_match = _action_matches(row["action"], calls, row.get("action_kind")) if row["action"] != "UNKNOWN" \
                    else any(c not in POSITIVE_READS for c in calls)
                matches = action_match and _resource_match(kind, text, row, cache)
            if matches:
                return message(row, invocation_identity(text))
    return None


def message(row, identity):
    return (f"DENIAL GUARD: blocked repeat of denied {row['action']} on {row['resource']}, "
            f"recorded principal {row['principal']} (invocation hint {row.get('identity', 'unknown')}). "
            "Time, identity, transport, command success and agent-authored rationale do not establish subsequent authority. "
            "Independent exact-denial authority verification is UNAVAILABLE; this hold cannot be lifted by this plugin.")
