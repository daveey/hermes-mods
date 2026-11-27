"""Bounded lexical analysis of shell file operations across execution boundaries.

This is not a shell interpreter. Values never become syntax. Only simple path
assignments and explicit ssh / shell -c boundaries are followed; ordinary data
arguments are never recursively interpreted. No remote filesystem is observed.
"""
import os
import re
import itertools
import shlex  # delimiter quote removal only; never used to reserialize code

_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
_FILES = {"rm", "unlink", "shred", "truncate", "mv", "cp", "install", "rsync", "ln", "tee", "dd"}
_CONTROL = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac"}


class _Budget:
    """One aggregate budget shared by every variant and execution boundary."""
    def __init__(self):
        self.remaining = 32768

    def spend(self, amount):
        self.remaining -= amount
        if self.remaining < 0:
            raise ValueError("aggregate shell analysis budget exhausted")


def _raw_segments(text):
    """Keep raw quote provenance and split only literal command punctuation."""
    seg, word, quote, escaped, i = [], [], "", False, 0
    while i < len(text):
        c = text[i]
        if escaped:
            word.append(c)
            escaped = False
        elif c == "\\" and quote != "'":
            word.append(c)
            escaped = True
        elif quote:
            word.append(c)
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
            word.append(c)
        elif c == "#" and not word:
            end = text.find("\n", i)
            i = len(text) if end < 0 else end - 1
        elif c in "<>" or text.startswith("&>", i):
            # Split syntax before expansion; quoted/expanded '>' stays data.
            if word:
                if not "".join(word).isdigit():
                    seg.append("".join(word))
                word = []
            match = re.match(r"(?:&>>?|>>>|>>|>\||>&|<>|<<|<&|[<>])", text[i:])
            assert match is not None
            op = match.group()
            seg.append(op)
            i += len(op) - 1
        elif c.isspace() or c in ";&|()":
            if word:
                seg.append("".join(word))
                word = []
            if c in "\n;&|()":
                if seg:
                    yield seg
                    seg = []
                if text[i:i + 2] in {"&&", "||"}:
                    yield [text[i:i + 2]]
                    i += 1
                else:
                    yield [c]
        else:
            word.append(c)
        i += 1
    if word and not quote:
        seg.append("".join(word))
    if seg:
        yield seg


def _value(raw, env, budget):
    out, quote, i = [], "", 0
    # Charge every prospective fragment BEFORE join can allocate its result.
    # Counting raw source/variants alone misses repeated assignment doubling.
    def append(value):
        budget.spend(len(value))
        out.append(value)

    while i < len(raw):
        c = raw[i]
        if c in "'\"" and (not quote or quote == c):
            quote = "" if quote else c
            i += 1
            continue
        if c == "\\" and quote != "'" and i + 1 < len(raw):
            # Within double quotes only these characters are escaped by bash.
            if not quote or raw[i + 1] in '$`"\\\n':
                append(raw[i + 1])
                i += 2
                continue
        match = _VAR.match(raw, i) if quote != "'" else None
        if match:
            name = match.group(1) or match.group(2)
            append(env.get(name, match.group()))
            i = match.end()
        else:
            append(c)
            i += 1
    return "".join(out)


def _ssh_body(args):
    # OpenSSH options with arguments; attached values consume no next argv.
    takes = set("BbcDEeFIiJLlmOoPpQRSWw")
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if not arg.startswith("-") or arg == "-":
            break
        flags = arg[1:]
        consumed = False
        for j, flag in enumerate(flags):
            if flag in takes:
                i += 1 if j + 1 < len(flags) else 2
                consumed = True
                break
        if not consumed:
            i += 1
    # OpenSSH also accepts options after the destination. Stop as soon as the
    # remote command starts; its options belong to that command, not SSH.
    i += 1
    while i < len(args) and args[i].startswith("-"):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        flags = arg[1:]
        consumed = False
        for j, flag in enumerate(flags):
            if flag in takes:
                i += 1 if j + 1 < len(flags) else 2
                consumed = True
                break
        if not consumed:
            i += 1
    # SSH joins remote command argv with spaces, not shell quoting.
    return " ".join(args[i:])


def _heredocs(text):
    """Remove stdin bodies without moving their assignments into the parent.

    Markers belong to the command whose redirection supplied them. Quote state
    spans header lines, so a quoted SSH script is parsed only in its child scope.
    """
    lines = text.splitlines(keepends=True)
    output, bodies, quote, pos = [], {}, "", 0
    while pos < len(lines):
        line = lines[pos]
        pending, edits, i = [], [], 0
        while i < len(line):
            c = line[i]
            if c == "\\" and quote != "'":
                i += 2
                continue
            if quote:
                if c == quote:
                    quote = ""
                i += 1
                continue
            if c in "'\"":
                quote = c
                i += 1
                continue
            if c == "#" and (i == 0 or line[i - 1].isspace()):
                break
            if line.startswith("<<", i) and not line.startswith("<<<", i):
                match = re.match(r"<<(-?)[ \t]*((?:'[^']*'|\"[^\"]*\"|[^\s;&|<>])+)", line[i:])
                if match:
                    delimiter = "".join(shlex.split(match.group(2)))
                    marker = f"__SQLITE_GUARD_STDIN_{len(bodies) + len(pending)}__"
                    pending.append((delimiter, bool(match.group(1)), marker, any(c in match.group(2) for c in "'\"\\")))
                    edits.append((i, i + match.end(), marker))
                    i += match.end()
                    continue
            i += 1
        if not pending:
            output.append(line)
            pos += 1
            continue
        end, found = pos + 1, {}
        for delimiter, tabs, marker, quoted in pending:
            body = []
            while end < len(lines):
                candidate = lines[end].lstrip("\t") if tabs else lines[end]
                end += 1
                if candidate.rstrip("\r\n") == delimiter:
                    found[marker] = (quoted, "".join(body))
                    break
                body.append(candidate)
            else:
                # Incomplete syntax is not executable. Preserve it for the base
                # analyzer rather than mistaking arbitrary later lines for data.
                return text, {}
        for start, stop, marker in reversed(edits):
            line = line[:start] + " " + marker + " " + line[stop:]
        bodies.update(found)
        output.append(line)
        pos = end
    return "".join(output), bodies


def _values(raw, env, budget):
    names = sorted({a or b for a, b in _VAR.findall(raw)})
    choices = [env.get(name, frozenset({"$" + name})) for name in names]
    count = 1
    for choice in choices:
        count *= len(choice)
    if count > 256:
        raise ValueError("too many possible shell path values")
    return frozenset(_value(raw, dict(zip(names, combination)), budget)
                     for combination in itertools.product(*choices))


def check(text, guard, cwd="", depth=0, inherited=None, *, remote=False, in_home=None, budget=None):
    budget = budget if budget is not None else _Budget()
    try:
        budget.spend(len(text))
        return _check(text, guard, cwd, depth, inherited, remote, in_home, budget)
    except (ValueError, RecursionError):
        return "shell syntax exceeds the bounded live-database safety analysis; use explicit paths and swap_db.py"


def _check(text, guard, cwd, depth, inherited, remote, in_home, budget):
    if depth > 8:
        return "shell execution nesting exceeds the bounded live-database safety analysis"
    env = dict(inherited or {})
    exported = set(env)
    readonly = set()
    text = text.replace("\\\n", "")
    if in_home is None:
        in_home = (os.path.realpath(guard._expand(cwd)) if cwd else False) if not remote else False
    text, bodies = _heredocs(text)
    blocks, rhs = 0, False
    scopes, braces, pipeline = [], [], None
    failed_cd, succeeded_cd = [], []
    segments = list(_raw_segments(text))
    for index, raw in enumerate(segments):
        budget.spend(1)
        if raw in (["&&"], ["||"]):
            if pipeline is not None and pipeline[0] == "|":
                _, env, exported, readonly, in_home, failed_cd, succeeded_cd = pipeline
                pipeline = None
            if raw == ["&&"] and succeeded_cd:
                in_home = guard.merge_directory_contexts(in_home, *succeeded_cd)
                succeeded_cd = []
            if raw == ["||"] and failed_cd:
                in_home = guard.merge_directory_contexts(in_home, *failed_cd)
                failed_cd = []
            rhs = True
            continue
        if raw == ["("]:
            scopes.append((dict(env), set(exported), set(readonly), in_home, blocks, list(failed_cd), list(succeeded_cd), pipeline))
            pipeline = None
            continue
        if raw == [")"]:
            if scopes:
                env, exported, readonly, in_home, blocks, failed_cd, succeeded_cd, pipeline = scopes.pop()
            continue
        if raw in ([";"], ["\n"], ["&"], ["|"]):
            if raw != ["|"] and pipeline is not None:
                _, env, exported, readonly, in_home, failed_cd, succeeded_cd = pipeline
                pipeline = None
            if succeeded_cd:
                in_home = guard.merge_directory_contexts(in_home, *succeeded_cd)
                succeeded_cd = []
            if failed_cd:
                in_home = guard.merge_directory_contexts(in_home, *failed_cd)
                failed_cd = []
            continue
        # Pipeline/background commands execute outside the parent's assignment
        # scope. A pipeline ends before &&/||; background lists end at &.
        following = segments[index + 1] if index + 1 < len(segments) else []
        if following in (["|"], ["&"]) and pipeline is None:
            pipeline = (following[0], dict(env), set(exported), set(readonly),
                        in_home, list(failed_cd), list(succeeded_cd))
        if raw and raw[0] == "}":
            if braces:
                blocks = braces.pop()
            raw = raw[1:]
        if raw and raw[0] == "{":
            braces.append(blocks)
            if rhs:
                blocks += 1
            raw = raw[1:]
        while raw and raw[0] in _CONTROL:
            control, raw = raw[0], raw[1:]
            if control in {"if", "for", "while", "until", "case"}:
                blocks += 1
            elif control in {"fi", "done", "esac"}:
                blocks = max(0, blocks - 1)
        conditional, rhs = bool(blocks) or rhs, False
        if not raw:
            continue
        while raw and raw[0] in {"{", "!", "time"}:
            raw = raw[1:]
        if not raw:
            continue
        if raw and raw[0] == "env":
            raw = raw[1:]
        if not raw:
            continue
        local = dict(env)
        i = 0
        export = raw[0] in {"export", "readonly", "declare", "local", "typeset"}
        if export:
            i += 1
            while i < len(raw) and raw[i].startswith("-"):
                if "x" in raw[i]:
                    exported.update(m.group(1) for token in raw[i + 1:] if (m := _ASSIGN.match(token)))
                i += 1
            if raw[0] == "export":
                exported.update(token for token in raw[i:] if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token))
        assigned = set()
        while i < len(raw):
            match = _ASSIGN.match(raw[i])
            if not match:
                break
            name, value = match.group(1), _values(match.group(2), local, budget)
            assigned.add(name)
            if raw[0] == "export":
                exported.add(name)
            if name not in readonly:
                local[name] = value | local.get(name, frozenset({"$" + name})) if conditional else value
            i += 1
        if export and raw[0] == "readonly":
            readonly.update(assigned)
            readonly.update(token for token in raw[1:] if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token))
        if i == len(raw) or export:
            env = local
            continue
        # Prefix assignments reach the child, not this command's word expansion.
        choices = [_values(token, env, budget) for token in raw[i:]]
        count = 1
        for choice in choices:
            count *= len(choice)
        if count > 256:
            raise ValueError("too many command variants")
        next_contexts = [in_home] if conditional else []
        for variant in itertools.product(*choices):
            budget.spend(32)
            argv = [guard._Quoted(value) if token[:1] in {"'", '"', "\\"} or token != value else value
                    for token, value in zip(raw[i:], variant)]
            why, next_home = _command(argv, guard, in_home, depth, local, exported | assigned, bodies, remote, budget)
            if why:
                return why
            # A failed cd leaves cwd unchanged. Only && gates its continuation
            # on success; || runs solely on failure. Keep every possibility.
            unwrapped = guard._strip_wrappers(argv)
            if unwrapped and os.path.basename(unwrapped[0]) == "cd":
                if following == ["||"]:
                    # Success skips this RHS, but rejoins at && or list end.
                    succeeded_cd = [guard.merge_directory_contexts(*succeeded_cd, next_home)]
                    next_home = in_home
                elif following != ["&&"]:
                    next_home = guard.merge_directory_contexts(in_home, next_home)
                else:
                    # Success gates this RHS only; failure rejoins at the next
                    # list/OR boundary, even after other commands in the chain.
                    failed_cd = [guard.merge_directory_contexts(*failed_cd, in_home)]
            next_contexts.append(next_home)
        in_home = guard.merge_directory_contexts(*next_contexts)
        if raw[i] == "unset" and not conditional:
            for name in raw[i + 1:]:
                if name not in readonly:
                    env.pop(name, None)
    return None


def _command(argv, guard, in_home, depth, local, inherited_names, bodies, remote, budget):
    why = guard._check_redirects(argv, in_home, resolve_local=not remote)
    if why:
        return why, in_home
    argv = guard._strip_wrappers(argv)
    if not argv:
        return None, in_home
    word, args = os.path.basename(argv[0]), argv[1:]
    stdin = []
    for arg in args:
        if arg not in bodies:
            continue
        quoted, code = bodies[arg]
        if quoted:
            stdin = [code]  # last stdin redirection wins
        else:
            names = sorted({a or b for a, b in _VAR.findall(code)})
            choices = [local.get(name, frozenset({"$" + name})) for name in names]
            count = 1
            for choice in choices:
                count *= len(choice)
            if count > 256:
                raise ValueError("too many heredoc expansion variants")
            stdin = []
            for variant in itertools.product(*choices):
                budget.spend(32 + len(code))
                values = dict(zip(names, variant))
                def replacement(match):
                    value = values[match.group(1) or match.group(2)]
                    budget.spend(len(value))
                    return value
                stdin.append(_VAR.sub(replacement, code))
    args = [a for a in args if a not in bodies]
    child_env = {name: local[name] for name in inherited_names if name in local}
    if word == "ssh":
        body = _ssh_body(args)
        if body:
            why = check(body, guard, depth=depth + 1, remote=True, budget=budget)
            if why:
                return why, in_home
        # Only stdin consumed as code by a shell (or the login shell) is code.
        remote_segments = list(_raw_segments(body))
        remote_word = os.path.basename(remote_segments[0][0]) if remote_segments and remote_segments[0] else ""
        if not body or remote_word in {"sh", "bash", "dash", "zsh", "ksh"}:
            for code in stdin:
                why = check(code, guard, depth=depth + 1, remote=True, budget=budget)
                if why:
                    return why, in_home
    elif word in {"sh", "bash", "dash", "zsh", "ksh"}:
        explicit = False
        for j, arg in enumerate(args[:-1]):
            if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", arg):
                explicit = True
                k = j + 1
                if args[k] == "--" and k + 1 < len(args):
                    k += 1
                why = check(args[k], guard, depth=depth + 1, inherited=child_env,
                            remote=remote, in_home=in_home, budget=budget)
                if not why and remote and k + 1 < len(args):
                    # `ssh h bash -lc 'sqlite3 DB ...'`: ssh joined the words, so the remote script is only the first
                    # one. Judge the words the author meant as the script too (fail closed).
                    why = check(" ".join(args[k:]), guard, depth=depth + 1, inherited=child_env,
                                remote=remote, in_home=in_home, budget=budget)
                if why:
                    return why, in_home
                break
        if not explicit:
            for code in stdin:
                why = check(code, guard, depth=depth + 1, inherited=child_env,
                            remote=remote, in_home=in_home, budget=budget)
                if why:
                    return why, in_home
    elif remote and word == "sqlite3":
        # The remote SQLite build cannot be probed from here: assume the WAL-reset bug (fail closed).
        db = guard._sqlite3_argv(args)[0]
        if db is not None and guard.is_live(db, in_home, resolve_local=False):
            return guard.remote_sqlite3_refusal(argv[0], db), in_home
    elif word == "cd":
        target = (guard._operands(args) or ["~"])[0]
        in_home = guard.directory_context(target, in_home, not remote)
    elif word in _FILES:
        ops = guard._operands(guard._without_redirects(args))
        if guard.destructive_directory(word, args):
            directories = guard.move_sources(args, ops) if word == "mv" else ops
            hit = next((p for p in directories if guard.protects_directory(p, in_home, not remote)), None)
            if hit:
                return f"`{word}` would remove a directory containing live database files: {hit}", in_home
        if word in {"rm", "unlink", "shred", "truncate", "mv", "tee"}:
            hit = next((p for p in ops if guard.is_live(p, in_home, not remote)
                        or guard.is_quarantine(p, in_home, not remote)), None)
        elif word == "dd":
            hit = next((p[3:] for p in args if p.startswith("of=") and guard.is_live(p[3:], in_home, not remote)), None)
        else:
            hit = ops[-1] if ops and guard.is_live(ops[-1], in_home, not remote) else None
        # Preserve local directory policy; never stat a remote path locally.
        if not hit and word in {"mv", "cp", "install", "rsync", "ln"} and len(ops) > 1:
            dest = ops[-1]
            sources = ops[:-1]
            explicit_dir = False
            for j, arg in enumerate(args):
                if arg in {"-t", "--target-directory"} and j + 1 < len(args):
                    dest = args[j + 1].rstrip("/")
                    sources = [p for p in ops if p != args[j + 1]]
                    explicit_dir = True
                elif arg.startswith("--target-directory="):
                    dest = arg.split("=", 1)[1].rstrip("/")
                    sources = ops
                    explicit_dir = True
            directory = (explicit_dir or dest.endswith("/") or guard._HOME_PREFIX.sub("", dest).rstrip("/") == "") if remote else guard._dir_like(dest)
            if directory:
                dest = dest.rstrip("/")
                hit = next((dest + "/" + os.path.basename(src) for src in sources
                            if guard.is_live(dest + "/" + os.path.basename(src), in_home, not remote)), None)
        if hit:
            return f"`{word}` would modify live database file {hit}; use swap_db.py for a guarded replacement", in_home
    return None, in_home
