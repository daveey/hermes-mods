"""Scoped landing-path accident prevention; stdlib-only, including standalone imports.

This is a bounded static analyzer, NOT an adversarial security boundary. Repository
queries use fixed git argv, never shell=True or execution of submitted command text.
Public interfaces return a refusal string or None: check_argv, check_shell,
check_python, and check_tool. No configuration means no protected repositories.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

MARKER = "HERMES_CORE_WT_LAND"
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.S)


def load_repos(env=None):
    env = os.environ if env is None else env
    try:
        override = env.get("SHIP_ONLY_GUARD_REPOS")
        if override is not None:
            data = json.loads(override)
        else:
            home = Path(env.get("HERMES_HOME", str(Path.home() / ".hermes")))
            policy = home / "ship-only-guard.json"
            # A configured profile inherits the host policy when it has no override.
            if not policy.exists() and home.parent.name == "profiles":
                policy = home.parent.parent / "ship-only-guard.json"
            data = json.loads(policy.read_text())["repos"]
        if not isinstance(data, list) or not all(isinstance(x, str) and REPO.fullmatch(x) for x in data):
            return set()
        return {x.lower() for x in data}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _slug(value):
    value = value.removesuffix(".git").rstrip("/")
    match = re.fullmatch(r"(?:https?://[^/]+/|ssh://(?:[^@/]+@)?[^/]+/|[^@/]+@[^:/]+:|[A-Za-z0-9.-]+\.[A-Za-z0-9.-]+/)?([\w.-]+/[\w.-]+)", value)
    return match[1].lower() if match else ""


def _git(cwd, *args):
    # Strip process-level repository overrides; probes refer only to the given cwd.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        p = subprocess.run(["git", "--no-optional-locks", "-C", str(cwd), *args],
                           env=env, capture_output=True, text=True, timeout=2)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _remote(cwd, name="origin", push=False):
    direct = _slug(name)
    if direct:
        return direct
    return _slug(_git(cwd, "remote", "get-url", *( ["--push"] if push else []), name))


def _context(cwd, env):
    return _slug(env.get("GH_REPO", "")) or _remote(cwd)


def _deny(repo, operation, repos):
    if repo in repos:
        return (f"[ship-only-guard] Refused direct {operation} for {repo}. "
                "Land only through `hermes-core-wt ship <slug>`; only its land child "
                "sets HERMES_CORE_WT_LAND=1 after the required gates.")
    return None


def _path(value, cwd):
    return os.path.abspath(os.path.join(cwd, os.path.expanduser(value)))


def _state_merge(words, repo, cwd, env):
    """Narrow state-only exception, never a general non-main merge allowance.

    Independent exact-head review and required-CI coverage remain caller duties;
    this preflight additionally refuses missing/non-green visible checks. It is
    not an atomic base lock (gh only offers an atomic head fence).
    """
    # Accept explicit numeric PR and repo only; avoid gh's implicit current PR,
    # URL/repo precedence, aliases, unknown options, auto-merge or admin override.
    if words[:2] != ["pr", "merge"]:
        return False
    args, options = [], {}
    i = 2
    while i < len(words):
        word = words[i]
        if word in {"--repo", "-R", "--match-head-commit"}:
            key = "--repo" if word == "-R" else word
            if key in options or i + 1 >= len(words):
                return False
            options[key] = words[i + 1]
            i += 2
        elif word == "--rebase" and word not in options:
            options[word] = True
            i += 1
        elif not word.startswith("-"):
            args.append(word)
            i += 1
        else:
            return False
    if (len(args) != 1 or not re.fullmatch(r"[1-9][0-9]*", args[0])
            or options.get("--repo", "").lower() != repo
            or not options.get("--rebase")
            or not re.fullmatch(r"[0-9a-f]{40}", options.get("--match-head-commit", ""))):
        return False
    fields = "number,url,state,baseRefName,headRefOid,statusCheckRollup"
    probe_env = dict(env)
    probe_env["GH_PROMPT_DISABLED"] = "1"
    try:
        p = subprocess.run(["gh", "pr", "view", args[0], "--repo", repo, "--json", fields],
                           cwd=cwd, env=probe_env, capture_output=True, text=True, timeout=15)
        if p.returncode != 0:
            return False
        data = json.loads(p.stdout)
        if not isinstance(data, dict):
            return False
        if (data.get("number") != int(args[0]) or data.get("state") != "OPEN"
                or data.get("baseRefName") != "state"
                or data.get("headRefOid") != options["--match-head-commit"]
                or not isinstance(data.get("url"), str)
                or data["url"].lower() != f"https://github.com/{repo}/pull/{args[0]}"):
            return False
        checks = data.get("statusCheckRollup")
        # gh's connection is bounded; never infer success from a possibly capped
        # rollup. Skipped/neutral/unknown checks conservatively require the ship path.
        if not isinstance(checks, list) or not 0 < len(checks) < 100:
            return False
        return all(isinstance(c, dict) and (
            c.get("__typename") == "CheckRun" and c.get("status") == "COMPLETED"
            and c.get("conclusion") == "SUCCESS" or
            c.get("__typename") == "StatusContext" and c.get("state") == "SUCCESS"
        ) for c in checks)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False


def _gh(words, cwd, env, repos):
    # gh's repository flag may occur before, between, or after command words.
    rest, selected, i = [], None, 0
    value_flags = {"--hostname", "-H", "--method", "-X", "--field", "-F", "--raw-field", "-f",
                   "--header", "--input", "--jq", "-q", "--template", "-t", "--match-head-commit",
                   "--subject", "--body", "--body-file", "--author-email"}
    while i < len(words):
        w = words[i]
        if w in {"-R", "--repo"} and i + 1 < len(words):
            selected = _slug(words[i + 1]); i += 2; continue
        if w.startswith("--repo=") or (w.startswith("-R") and len(w) > 2):
            selected = _slug(w.split("=", 1)[1] if "=" in w else w[2:]); i += 1; continue
        if w in value_flags:
            i += 2; continue
        if not w.startswith("-"):
            rest.append(w)
        i += 1
    if len(rest) >= 2 and rest[:2] == ["pr", "merge"]:
        url = next((re.fullmatch(r"https?://[^/]+/([^/]+/[^/]+)/pull/\d+/?", w) for w in rest[2:]
                    if re.fullmatch(r"https?://[^/]+/([^/]+/[^/]+)/pull/\d+/?", w)), None)
        repo = _slug(url[1]) if url else selected if selected is not None else _context(cwd, env)
        if repo in repos and _state_merge(words, repo, cwd, env):
            return None
        return _deny(repo, "gh pr merge (state requires explicit repo, numeric PR, --rebase, "
                     "--match-head-commit FULL_SHA and readable green exact-head state preflight)", repos)
    if rest and rest[0] == "api":
        for w in rest[1:]:
            match = re.fullmatch(r"(?:https?://[^/]+/)?/?repos/([^/]+/[^/]+)/(?:pulls/\d+/merge|merges)/?(?:\?.*)?", w)
            if match:
                return _deny(_slug(match[1]), "gh api merge endpoint", repos)
    return None


def _main_ref(ref):
    return ref.lstrip("+") in {"main", "refs/heads/main"}


def _push(words, cwd, repos, config):
    args, i = [], 0
    value_flags = {"--repo", "--receive-pack", "--exec", "-o", "--push-option"}
    remote_opt = None
    while i < len(words):
        w = words[i]
        if w in value_flags and i + 1 < len(words):
            if w == "--repo":
                remote_opt = words[i + 1]
            i += 2; continue
        if w.startswith("--repo="):
            remote_opt = w.split("=", 1)[1]
        elif not w.startswith("-") or w == ":":
            args.append(w)
        i += 1
    branch = _git(cwd, "symbolic-ref", "--short", "HEAD")
    def cfg(key):
        return config[key] if key in config else _git(cwd, "config", "--get", key)
    remote = remote_opt or (args.pop(0) if args else None)
    remote = remote or cfg(f"branch.{branch}.pushRemote") or cfg("remote.pushDefault") or cfg(f"branch.{branch}.remote") or "origin"
    repo = _remote(cwd, remote, push=True)
    if repo not in repos:
        return None
    if "--dry-run" in words or "-n" in words:
        return None
    if "--all" in words or "--mirror" in words:
        return _deny(repo, "git push updating main", repos)
    refs = args
    if not refs and "--tags" in words:
        return None  # tag-only push does not apply default branch refspecs
    if not refs:
        refs = cfg(f"remote.{remote}.push").splitlines()
    if not refs:
        mode = cfg("push.default") or "simple"
        if mode == "nothing":
            return None
        if mode == "matching":
            return _deny(repo, "git push potentially updating main", repos)
        target = cfg(f"branch.{branch}.merge") if mode in {"upstream", "simple"} else branch
        refs = [target or branch]
    for ref in refs:
        target = ref.lstrip("+").split(":", 1)[-1]
        if _main_ref(target) or (ref.lstrip("+") == "HEAD" and branch == "main") or target == "" or "*" in target:
            return _deny(repo, "git push updating main", repos)
    return None


def check_argv(argv, cwd=None, repos=None, env=None, _depth=0):
    """Check a literal executable argv, with optional cwd/environment snapshot."""
    if _depth > 12 or not isinstance(argv, (list, tuple)) or not all(isinstance(x, str) for x in argv):
        return None
    env = dict(os.environ if env is None else env)
    repos = load_repos(env) if repos is None else {x.lower() for x in repos}
    cwd = os.path.abspath(cwd or env.get("TERMINAL_CWD") or os.getcwd())
    words = list(argv)
    while words and (m := ASSIGN.fullmatch(words[0])):
        env[m[1]] = m[2]; words.pop(0)
    if not words or not repos:
        return None
    exe = os.path.basename(words.pop(0))
    if exe == "env":
        while words:
            if words[0] in {"-u", "--unset"} and len(words) > 1:
                env.pop(words[1], None); words = words[2:]
            elif words[0] in {"-i", "--ignore-environment", "-"}:
                env = {}; words.pop(0)
            elif words[0] in {"-C", "--chdir"} and len(words) > 1:
                cwd = _path(words[1], cwd); words = words[2:]
            elif words[0] == "--":
                words.pop(0); break
            else:
                break
        return check_argv(words, cwd, repos, env, _depth + 1)
    if exe in {"command", "exec", "nohup", "sudo"}:
        while words and words[0] in {"--", "-n", "-E"}:
            words.pop(0)
        return check_argv(words, cwd, repos, env, _depth + 1)
    if exe in {"sh", "bash", "zsh", "dash"}:
        for i, w in enumerate(words[:-1]):
            if w.startswith("-") and "c" in w:
                return check_shell(words[i + 1], cwd, repos, env, _depth + 1)
    if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", exe) and "-c" in words:
        i = words.index("-c")
        if i + 1 < len(words):
            return check_python(words[i + 1], cwd, repos, env, _depth + 1)
    if env.get(MARKER) == "1":
        return None
    if exe == "gh":
        return _gh(words, cwd, env, repos)
    if exe == "git":
        config = {}
        while words and words[0].startswith("-"):
            w = words.pop(0)
            if w in {"-C", "-c", "--work-tree", "--git-dir"} and words:
                value = words.pop(0)
                if w in {"-C", "--work-tree"}:
                    cwd = _path(value, cwd)
                elif w == "-c" and "=" in value:
                    k, v = value.split("=", 1); config[k] = v
            elif w.startswith("-C"):
                cwd = _path(w[2:], cwd)
        if words and words[0] == "push":
            return _push(words[1:], cwd, repos, config)
    return None


def _segments(text):
    """Split operators only outside quotes; quoted prose cannot become commands."""
    start, quote, escape, i = 0, None, False, 0
    while i < len(text):
        c = text[i]
        if escape:
            escape = False
        elif c == "\\" and quote != "'":
            escape = True
        elif c in "\"'" and (quote is None or quote == c):
            quote = c if quote is None else None
        elif quote is None and c == "#" and (i == 0 or text[i - 1].isspace()):
            end = text.find("\n", i)
            yield text[start:i]
            if end < 0:
                return
            start = end + 1; i = end
        elif quote is None and c in ";\n&|()":
            yield text[start:i]
            start = i + 1
        i += 1
    yield text[start:]


def check_shell(command, cwd=None, repos=None, env=None, _depth=0):
    env = dict(os.environ if env is None else env)
    repos = load_repos(env) if repos is None else repos
    cwd = os.path.abspath(cwd or env.get("TERMINAL_CWD") or os.getcwd())
    for segment in _segments(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            continue
        # Only cwd is propagated. Command-prefix assignments, especially the land
        # marker, never leak to later commands in a compound expression.
        if words and words[0] in {"cd", "pushd"} and len(words) == 2:
            cwd = _path(words[1], cwd)
        why = check_argv(words, cwd, repos, env, _depth + 1)
        if why:
            return why
    return None


def _literal(node, bindings):
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _source_nodes(node):
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _source_nodes(child)


def check_python(code, cwd=None, repos=None, env=None, _depth=0):
    """Inspect literal subprocess/os.system/terminal/tool_call calls, not prose."""
    if _depth > 12:
        return None
    env = dict(os.environ if env is None else env)
    repos = load_repos(env) if repos is None else repos
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    bindings = {}
    # Visit source order. Only literal assignments are resolved, never eval/exec.
    for node in _source_nodes(tree):
        if isinstance(node, ast.Assign):
            value = _literal(node.value, bindings)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = value
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        kwargs = {x.arg: _literal(x.value, bindings) for x in node.keywords if x.arg}
        first = _literal(node.args[0], bindings) if node.args else None
        call_cwd = kwargs.get("cwd") or kwargs.get("workdir") or cwd
        call_env = kwargs.get("env") if isinstance(kwargs.get("env"), dict) else env
        why = None
        if name in {"run", "Popen", "call", "check_call", "check_output", "system", "popen"}:
            value = first if first is not None else kwargs.get("args")
            if isinstance(value, (list, tuple)):
                why = check_argv(value, call_cwd, repos, call_env, _depth + 1)
            elif isinstance(value, str):
                # subprocess strings without shell=True are one executable name.
                if kwargs.get("shell") or name in {"system", "popen"}:
                    why = check_shell(value, call_cwd, repos, call_env, _depth + 1)
        elif name == "terminal":
            args = first if isinstance(first, dict) else {**kwargs, **({"command": first} if isinstance(first, str) else {})}
            why = check_tool("terminal", args, call_cwd, repos, call_env, _depth + 1)
        elif name == "tool_call":
            args = first if isinstance(first, dict) else kwargs
            why = check_tool("tool_call", args, call_cwd, repos, call_env, _depth + 1)
        if why:
            return why
    return None


def check_tool(name, args, cwd=None, repos=None, env=None, _depth=0):
    if _depth > 12 or not isinstance(args, dict):
        return None
    name = name.rsplit("__", 1)[-1].rsplit(".", 1)[-1]
    cwd = args.get("workdir") or cwd
    if name == "terminal":
        return check_shell(str(args.get("command") or ""), cwd, repos, env, _depth + 1)
    if name == "execute_code":
        return check_python(str(args.get("code") or ""), cwd, repos, env, _depth + 1)
    if name == "tool_call":
        for call in args.get("calls", []):
            if isinstance(call, dict):
                why = check_tool(str(call.get("name", "")), call.get("arguments"), cwd, repos, env, _depth + 1)
                if why:
                    return why
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "--gh-args":
        print("usage: guard.py --gh-args <gh arguments...>", file=sys.stderr)
        return 2
    why = check_argv(["gh", *argv[1:]])
    if why:
        print(why, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
