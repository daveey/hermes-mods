"""Apply the machine-wide tailnet workload policy to plain ``ssh``/``scp``/``sftp``/``rsync`` in shell tools.

The broker runner (runner.py) refuses heavy or unbounded work on a protected machine, but an agent could reach the
same machine with ``ssh <host> '<command>'`` from the ``terminal`` tool. This module is the ``pre_tool_call`` side
of that policy: it finds every ssh-family command in a shell command (compound commands, ``bash -c`` payloads,
wrappers, command substitutions and heredocs included), decides whether its destination is protected by
``Path.home()/.hermes/tailnet/workload-policy.json`` and, if so:

* ``ssh``: classifies the remote command (and a heredoc body a remote shell reads) with ``runner.workload_reason``.
  An interactive session, a remote shell reading unknown stdin, or text that cannot be classified is refused
  (fail closed, like the runner).
* ``scp``/``sftp``/``rsync`` to or from a protected destination: refused; ``tailnet_copy`` is the bounded route.

A destination is protected when its host part (after ``user@``), or the ``hostname`` that ``ssh -G`` (with the
command's own options) prints for it, is a protected alias or address, or resolves to a protected address.
No policy file: no-op. Stdlib only.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Iterator, Optional

HERE = Path(__file__).resolve().parent
REMOTE_TOOLS = {"ssh", "scp", "sftp", "rsync"}
MENTION = re.compile(r"(?<![A-Za-z0-9_.-])(?:ssh|scp|sftp|rsync)(?![A-Za-z0-9_.-])")
SSH_G_TIMEOUT_S = 3
# ssh options that take a value (as the next word or glued: -p22, -oBatchMode=yes)
SSH_VALUE_OPTS = set("BbcDEeFIiJLlmOoPpQRSWw")
SFTP_VALUE_OPTS = set("BbcDFiJloPRSsX")
SCP_VALUE_OPTS = set("cDFiJloPSX")
RSYNC_VALUE_LONG = {"--rsh", "--exclude", "--include", "--filter", "--exclude-from", "--include-from",
                    "--files-from", "--log-file", "--backup-dir", "--suffix", "--rsync-path", "--bwlimit",
                    "--temp-dir", "--partial-dir", "--compare-dest", "--copy-dest", "--link-dest", "--chmod",
                    "--chown", "--usermap", "--groupmap", "--timeout", "--contimeout", "--port", "--password-file",
                    "--max-size", "--min-size", "--block-size", "--checksum-choice", "--compress-choice",
                    "--outbuf", "--info", "--debug", "--out-format", "--log-file-format", "--sockopts"}
RSYNC_VALUE_SHORT = set("efBMT")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
REMOTE_OPERAND = re.compile(r"^(?:[^@/\s]+@)?(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9_][A-Za-z0-9_.-]*):")
URL_OPERAND = re.compile(r"^(?:ssh|sftp|scp|rsync)://(?:[^@/]+@)?(\[[0-9A-Fa-f:.]+\]|[^/:]+)", re.I)


class Refused(Exception):
    """A protected destination's command breaks the workload policy (message is the full refusal text)."""


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def runner():
    return _load("tailnet_terminal_runner", HERE / "runner.py")


def shell_parser():
    # The shared shell lexer (heredocs, substitutions, comments, wrappers) lives in mac-heavy-guard.
    return _load("tailnet_terminal_shell", HERE.parent / "mac-heavy-guard" / "guard.py")


# ---------------------------------------------------------------- finding remote commands

def _ssh_heredocs(g, text: str) -> tuple[str, list]:
    """(text without the bodies of heredocs fed to ssh, [(ssh argv before the operator, body)]).
    The delimiter line stays, so the shell text around it still parses as a closed heredoc."""
    if "<<" not in text:
        return text, []
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    found: list = []
    shell = ""
    i = 0
    while i < len(lines):
        logical = lines[i]
        i += 1
        while i < len(lines) and (g.CONTINUED.search(logical) or g._tokens(shell + logical) is None):
            logical += lines[i]
            i += 1
        out.append(logical)
        pending = []
        for m in g.HEREDOC.finditer(logical):
            argv = _open_argv(g, shell + logical[:m.start()])
            if argv is None:
                continue
            try:
                word = shlex.split(m.group(2))[0]
            except (ValueError, IndexError):
                continue
            remote = bool(argv) and os.path.basename(argv[0]) == "ssh"
            pending.append((argv if remote else None, m.group(1) == "-", word))
        shell += logical
        for argv, strip_tabs, word in pending:
            body = []
            while i < len(lines):
                line = lines[i]
                i += 1
                bare = line.rstrip("\r\n")
                if (bare.lstrip("\t") if strip_tabs else bare) == word:
                    out.append(line)
                    break
                if argv is None:
                    out.append(line)
                else:
                    body.append(line)
            if argv is not None:
                found.append((argv, "".join(body)))
    return "".join(out), found


def _open_argv(g, before: str) -> Optional[list]:
    """argv (past assignments and wrappers) of the simple command still open at the end of `before`."""
    toks = g._tokens(before + " " + g._PROBE)
    if not toks or toks[-1] != g._PROBE:
        return None
    toks = toks[:-1]
    start = 0
    for k, t in enumerate(toks):
        if t == "&" and k and toks[k - 1][-1:] in (">", "<"):
            continue
        if t in g.SEPARATORS or (t and set(t) <= set(";&|()\n")):
            start = k + 1
    rest, _ = g._strip_prefix(toks[start:])
    return [t for t in rest if not re.match(r"^\d*[<>]", t)]


def _simple_commands(g, text: str, depth: int = 0) -> Iterator[list]:
    """Each simple command's argv (wrappers stripped), recursing into `bash -c` payloads. Raises ValueError
    when a line that mentions an ssh-family tool cannot be lexed (fail closed)."""
    code = g._split_heredocs(text)[0].replace("\\\n", " ")
    dropped: list = []
    toks = g._command_lex(g._lexable(code, g._command_lex, dropped))
    if any(MENTION.search(line) for line in dropped):
        raise ValueError("cannot classify malformed shell command")
    cur: list = []
    for t in toks + [";"]:
        if t in g.SEPARATORS or (t and set(t) <= set(";&|()\n")):
            if cur:
                rest, _ = g._strip_prefix(cur)
                if rest:
                    name = os.path.basename(rest[0])
                    flag = next((k for k, a in enumerate(rest[1:], 1) if re.fullmatch(r"-[a-z]*c[a-z]*", a)), None)
                    if name in SHELLS and flag is not None and depth < 8:
                        payload = rest[flag + 1] if flag + 1 < len(rest) else ""
                        yield from _simple_commands(g, payload, depth + 1)
                    else:
                        yield rest
            cur = []
        else:
            cur.append(t)


# ---------------------------------------------------------------- argv parsing

def _split_options(argv: list, value_opts: set) -> tuple[list, list]:
    """(options as given, operands) for an OpenSSH-style argv (short flags, glued or separate values, `--`)."""
    opts, i = [], 1
    while i < len(argv):
        a = argv[i]
        if a == "--":
            i += 1
            break
        if not a.startswith("-") or a == "-":
            break
        opts.append(a)
        for k, c in enumerate(a[1:], 1):
            if c in value_opts:
                if k == len(a) - 1 and i + 1 < len(argv):
                    i += 1
                    opts.append(argv[i])
                break
        i += 1
    return opts, argv[i:]


def _ssh_flags(opts: list) -> set:
    flags, skip = set(), False
    for a in opts:
        if skip:
            skip = False
            continue
        for k, c in enumerate(a[1:], 1):
            flags.add(c)
            if c in SSH_VALUE_OPTS:
                skip = k == len(a) - 1
                break
    return flags


def _host_part(dest: str) -> str:
    m = URL_OPERAND.match(dest)
    if m:
        return m.group(1).strip("[]")
    host = dest.rsplit("@", 1)[-1]
    return host.strip("[]")


def _remote_operands(name: str, args: list) -> list:
    """Destinations named by scp/rsync/sftp operands (host parts)."""
    hosts = []
    if name == "sftp":
        opts, operands = _split_options([name] + args, SFTP_VALUE_OPTS)
        if operands:
            hosts.append(_host_part(operands[0].split(":", 1)[0] if not URL_OPERAND.match(operands[0])
                                    else operands[0]))
        return hosts
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if name == "scp" and a.startswith("-") and len(a) > 1:
            last = a[-1]
            skip = last in SCP_VALUE_OPTS and not any(c in SCP_VALUE_OPTS for c in a[1:-1])
            continue
        if name == "rsync" and a.startswith("-") and len(a) > 1:
            if a.startswith("--"):
                skip = a in RSYNC_VALUE_LONG
            else:
                skip = a[-1] in RSYNC_VALUE_SHORT and not any(c in RSYNC_VALUE_SHORT for c in a[1:-1])
            continue
        m = URL_OPERAND.match(a) or REMOTE_OPERAND.match(a)
        if m:
            hosts.append(m.group(1).strip("[]"))
    return hosts


# ---------------------------------------------------------------- protection

def _ssh_g_hostname(opts: list, dest: str) -> Optional[str]:
    ssh = shutil.which("ssh")
    if not ssh:
        return None
    try:
        p = subprocess.run([ssh] + opts + ["-G", dest], capture_output=True, text=True,
                           timeout=SSH_G_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    for line in p.stdout.splitlines():
        key, _, value = line.strip().partition(" ")
        if key.lower() == "hostname" and value.strip():
            return value.strip()
    return None


def _protected(r, policy: dict, host: str, ssh_opts: Optional[list] = None, dest: str = "") -> bool:
    names = [host]
    if ssh_opts is not None:
        resolved = _ssh_g_hostname(ssh_opts, dest or host)
        if resolved:
            names.append(resolved)
    for name in names:
        canon = r.canonical_address(name)
        if canon in policy["protected_aliases"] or canon in policy["protected_addresses"]:
            return True
    for name in names:
        try:
            _, addresses = r.resolved_target({"addr": name})
        except ValueError:
            continue  # cannot resolve here: ssh cannot reach it by this name either
        if set(addresses) & policy["protected_addresses"]:
            return True
    return False


def _reads_stdin_shell(g, remote: str) -> bool:
    """True when the remote command is a shell that reads its script from stdin (`bash`, `bash -s`, `sh -l`)."""
    try:
        words = shlex.split(remote)
    except ValueError:
        return False
    rest, _ = g._strip_prefix(words)
    if not rest or os.path.basename(rest[0]) not in SHELLS:
        return False
    args = rest[1:]
    if any(re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a) for a in args):
        return False
    return "-s" in args or not any(not a.startswith("-") for a in args)


def check_ssh(g, r, policy: dict, argv: list, body: Optional[str]) -> Optional[str]:
    opts, operands = _split_options(argv, SSH_VALUE_OPTS)
    if not operands:
        return None  # `ssh -V`, `ssh -G` with no host: nothing runs remotely
    dest, remote = operands[0], " ".join(operands[1:])
    flags = _ssh_flags(opts)
    if "G" in flags or "V" in flags:
        return None
    if not _protected(r, policy, _host_part(dest), opts, dest):
        return None
    if "s" in flags:
        return "ssh subsystem (sftp) transfer to a protected machine; use bounded tailnet_copy instead"
    if not remote.strip():
        if body is not None:
            remote = body
        elif flags & {"N", "W"} or any(o.startswith("-O") for o in opts):
            return None  # forwarding/control only, no remote command
        else:
            return ("interactive ssh session (no command) on a protected machine cannot be classified; "
                    "use tailnet_exec with a specific command")
    elif _reads_stdin_shell(g, remote):
        if body is None:
            return ("remote shell reads an unclassified script from stdin; send the commands themselves "
                    "(tailnet_exec)")
        remote = remote + "\n" + body
    return r.workload_reason(remote)


def check_command(command: str) -> Optional[str]:
    """Refusal text for a shell command that breaks the workload policy on a protected machine, else None."""
    if not command or not MENTION.search(command):
        return None
    r = runner()
    try:
        policy = r.load_policy()
    except ValueError as exc:
        return f"tailnet workload policy: {exc}"
    if policy is None:
        return None
    try:
        g = shell_parser()
    except Exception:
        return "tailnet workload policy: classifier unavailable"
    route = policy["routing_message"]
    try:
        code, heredocs = _ssh_heredocs(g, command)
        for argv, body in heredocs:
            reason = check_ssh(g, r, policy, argv, body)
            if reason:
                return f"tailnet workload policy: {reason}; {route}"
        judged = [a for a, _ in heredocs]
        for argv in _simple_commands(g, code):
            name = os.path.basename(argv[0])
            if name not in REMOTE_TOOLS:
                continue
            if name == "ssh":
                seen = next((k for k, a in enumerate(judged) if argv[:len(a)] == a), None)
                if seen is not None:
                    judged.pop(seen)  # already judged together with its heredoc body
                    continue
                reason = check_ssh(g, r, policy, argv, None)
            else:
                hosts = _remote_operands(name, argv[1:])
                reason = next((f"{name} to or from a protected machine is an unbounded transfer; use bounded "
                               "tailnet_copy instead" for h in hosts if _protected(r, policy, h, [], h)), None)
            if reason:
                return f"tailnet workload policy: {reason}; {route}"
    except ValueError as exc:
        msg = str(exc)
        if not msg.startswith("tailnet workload policy"):
            msg = "tailnet workload policy: " + msg
        return f"{msg}; {route}"
    except Exception:
        return f"tailnet workload policy: classifier unavailable; {route}"
    return None
