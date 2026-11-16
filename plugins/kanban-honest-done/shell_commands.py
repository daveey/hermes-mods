"""Small static shell scanner: retain argv boundaries and separate code from data.

No shell is executed. This is deliberately not a shell evaluator (variables containing
commands, sourced files and the shell's startup state are not resolved). It fails
closed: only the arguments and input of known data commands are data, and printed text
counts as code once it can reach anything that may run it (a pipe into a non-data
command, an unquoted substitution that becomes command words, a piped group, a process
substitution, eval). A data command is data only by its bare name (or from /bin or
/usr/bin) and only while the input does not redefine that name: a function, an alias,
``hash``, ``enable``, an imported ``BASH_FUNC_`` or a PATH change anywhere in the code
revokes it. Operators split by a backslash-newline are read the way the shell joins them.
"""
import contextvars
import os
import re

_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*=")
_SHELLS = {"sh", "bash", "dash", "zsh", "ksh"}
# Commands that never run their arguments or what they read. Every other command word
# fails closed: it may run its arguments (nice, xargs, sudo, find -exec ...) or its input.
DATA_COMMANDS = frozenset({"echo", "printf", "grep", "egrep", "fgrep", "rg", "cat"})
# Data commands whose output is their own arguments (the others print stdin or files).
_ARG_PRINTERS = frozenset({"echo", "printf"})
# A data command named by a path is data only from these directories: ./cat may be anything.
_SYSTEM_DIRS = frozenset({"/bin", "/usr/bin"})
_HERMES = frozenset({"hermes", "hermes.exe", "hermes_cli.main"})
_RESERVED = frozenset({'if', 'then', 'else', 'elif', 'do', 'while', 'until', '!', '{'})
_UNSET = object()
# A word that sets PATH or reads into it, and an imported bash function definition.
_PATH_WORD = re.compile(r"PATH(?:\+?=|$)")
_BASH_FUNC = re.compile(r"BASH_FUNC_(.+?)(?:%%|\(\))=")
# Data command names the input redefines (revoked for the whole scan) and the names
# found while scanning. The outermost segments() call sets both.
_SHADOWED = contextvars.ContextVar('shadowed_data_commands', default=frozenset())
_FOUND: contextvars.ContextVar[set | None] = contextvars.ContextVar('found_redefinitions', default=None)


def _lmatch(text, i, token):
    """End index when ``token`` starts at ``i`` once backslash-newline pairs are joined.

    The shell removes an unquoted or double-quoted backslash-newline before it reads
    operators, so ``$\\<newline>(`` opens a substitution and ``<\\<newline><`` is a
    heredoc. Returns None when ``token`` is not there.
    """
    for ch in token:
        while text.startswith('\\\n', i):
            i += 2
        if i >= len(text) or text[i] != ch:
            return None
        i += 1
    return i


def _next_is(text, i, ch):
    """Whether ``ch`` comes next at or after ``i``, past blanks and continuations."""
    while i < len(text):
        if text.startswith('\\\n', i):
            i += 2
        elif text[i] in ' \t':
            i += 1
        else:
            return text[i] == ch
    return False


def command_start(words):
    """Find the executable after assignments and explicit execution wrappers."""
    i = 0
    while i < len(words):
        if _ASSIGNMENT.match(words[i]):
            i += 1
            continue
        if words[i] in _RESERVED:
            i += 1
            continue
        name = os.path.basename(words[i])
        if name in {"command", "exec", "nohup", "time", "env"}:
            i += 1
            while i < len(words) and words[i].startswith('-'):
                opt = words[i]
                # command -v/-V queries a name; it does not run that name.
                if name == 'command' and opt in {'-v', '-V'}:
                    return len(words)
                i += 1
                if opt == '--':
                    break
                if (name == 'env' and opt in {'-u', '--unset', '-C', '--chdir'}
                        or name == 'exec' and opt == '-a'
                        or name == 'time' and opt in {'-f', '--format', '-o', '--output'}):
                    i += 1
            continue
        if name == 'timeout':
            i += 1
            while i < len(words) and words[i].startswith('-'):
                opt = words[i]
                i += 1
                if opt in {'-s', '--signal', '-k', '--kill-after'}:
                    i += 1
            i += 1  # duration
            continue
        break
    return i


def command_name(words):
    """Basename of the word this argv executes, or None when it executes nothing."""
    start = command_start(words)
    return os.path.basename(words[start]) if start < len(words) else None


def data_commands():
    """The data commands whose names the input being scanned has not redefined."""
    return DATA_COMMANDS - _SHADOWED.get()


def data_word(word):
    """True when this command word runs a data command.

    Its name must be a data command the input has not redefined, spelled bare or as a
    path in /bin or /usr/bin: ``./cat`` or ``~/bin/grep`` may be any program.
    """
    name = os.path.basename(word)
    return name in data_commands() and (word == name or os.path.dirname(word) in _SYSTEM_DIRS)


def is_data(words):
    """True when this argv provably runs neither its arguments nor its input."""
    start = command_start(words)
    return start >= len(words) or data_word(words[start])


def redefined(argv):
    """Data command names this command may redefine for the commands that follow it.

    Fail closed: ``function NAME``, ``alias NAME=…``, ``hash`` or ``enable`` naming a
    data command, an imported ``BASH_FUNC_NAME%%=…`` and any PATH change (an assignment,
    or ``PATH`` as a word of a command that is not a data command: ``read PATH``,
    ``export PATH=…``) all count. ``NAME () …`` definitions are found by the scanner.
    """
    start = command_start(argv)
    found = set()
    # In front of a data command only its assignments can change anything.
    words = argv[:start] if start < len(argv) and data_word(argv[start]) else argv
    for word in words:
        if _PATH_WORD.match(word):
            found |= DATA_COMMANDS  # env runs echo and printf through PATH as well
        match = _BASH_FUNC.match(word)
        if match:
            found.add(match.group(1))
    if start < len(argv):
        name = os.path.basename(argv[start])
        args = argv[start + 1:]
        if name == 'function':
            for word in args:
                if word in ('{', '('):
                    break
                found.add(word)
        elif name == 'alias':
            found.update(word.split('=', 1)[0] for word in args if '=' in word)
        elif name in ('hash', 'enable'):
            found.update(os.path.basename(word) for word in args)
    return found & DATA_COMMANDS


def _shell_options(argv, start):
    """``(index after the options, has c, reads stdin)`` for the shell at ``argv[start]``.

    Options run up to the first operand, ``--`` or a lone ``-`` (the same as ``--``).
    bash and dash read ``c`` whatever its sign, and bash reads ``s`` whatever its sign
    (/bin/sh is bash on macOS), so the sign is ignored: fail closed. Every ``o``/``O``
    letter consumes one option name, also inside a cluster (``-coO posix extglob``).
    """
    i = start + 1
    stdin = command = False
    while i < len(argv):
        arg = argv[i]
        if arg in {'--', '-'}:
            i += 1
            break
        if not arg.startswith(('-', '+')):
            break
        if arg in {'--rcfile', '--init-file'}:
            i += 2
            continue
        if arg.startswith('--'):
            i += 1
            continue
        flags = arg[1:]
        stdin = stdin or 's' in flags
        command = command or 'c' in flags
        i += 1 + sum(flags.count(f) for f in 'oO')
    return i, command, stdin


def _shell_at(argv):
    """Index of the shell this argv runs, or None.

    The shell at the executable position or, failing closed, the first shell word in the
    arguments of any other command that is not a data command or Hermes: ``nice``,
    ``sudo``, ``xargs`` and ``find -exec`` all run the shell they name.
    """
    start = command_start(argv)
    if start >= len(argv):
        return None
    name = os.path.basename(argv[start])
    if name in _SHELLS:
        return start
    if data_word(argv[start]) or name in _HERMES:
        return None
    return next((k for k in range(start + 1, len(argv)) if os.path.basename(argv[k]) in _SHELLS), None)


def shell_input(argv):
    """Return (inline script, reads stdin) for the shell this argv runs.

    ``-c`` anywhere among the options makes the first operand after *all* options the
    command string (``bash -c -e Q`` runs Q). Otherwise ``-s``, or no operand at all,
    reads the script from stdin; a first operand is a script file.
    """
    start = _shell_at(argv)
    if start is None:
        return None, False
    i, command, stdin = _shell_options(argv, start)
    if command:
        return (argv[i] if i < len(argv) else None), False
    return None, stdin or i >= len(argv)


def stdin_is_code(argv, depth=0):
    """Whether text this argv reads on stdin may run as code (fail closed).

    Data commands never run it. A shell runs it when it reads its script from stdin,
    when its ``-c`` script is anything but data commands (``bash -c 'source /dev/stdin'``)
    and when its script file is anything but /dev/null. Any other command (``.``,
    ``source``, ``sudo``, ``ssh``, ``python3``, a redefined ``cat`` ...) may run it.
    """
    start = command_start(argv)
    if start >= len(argv):
        return False
    if data_word(argv[start]):
        return False
    if os.path.basename(argv[start]) not in _SHELLS:
        return True
    i, command, stdin = _shell_options(argv, start)
    if command:
        return i < len(argv) and not all(is_data(seg) for seg in segments(argv[i], depth + 1))
    return stdin or i >= len(argv) or argv[i] != '/dev/null'


def segments(text, depth=0, _stop=None, sink=False):
    """Return argv lists, including explicit substitutions and shell -c scripts.

    Quotes are tracked before quote removal, so quoted punctuation cannot start a
    command. A heredoc body or here-string is data only when its consumer is a data
    command (or a shell that provably does not run its input) whose output reaches no
    command that may run it; unquoted heredocs still perform command substitution.
    echo/printf arguments piped into a command that may run them are code. With ``sink``
    everything this text prints is code, because its output becomes command words.
    A data command name that any code in the text redefines (see ``redefined``) is not
    data anywhere in it; its arguments are kept as the command it may run, both as
    words and joined and read again. The outermost call scans until no new redefinition
    turns up. Keep raw substitution spelling in outer argv so acceptance.py can resolve
    --body "$(cat file)" without executing it.
    """
    if depth == 0 and _stop is None and _FOUND.get() is None:
        shadowed = _SHADOWED.get()
        while True:
            found = set()
            found_token, shadowed_token = _FOUND.set(found), _SHADOWED.set(shadowed)
            try:
                result = _scan(text, depth, _stop, sink)
            finally:
                _SHADOWED.reset(shadowed_token)
                _FOUND.reset(found_token)
            if found <= shadowed:
                return result
            shadowed = shadowed | found
    return _scan(text, depth, _stop, sink)


def _scan(text, depth, _stop, sink):
    if depth > 24:
        return ([], len(text)) if _stop else []
    result, words, word = [], [], []
    active = False
    quote = None
    pending_docs = []
    herestrings = []
    pipeline = []
    state = {'pipe_open': False, 'resink': False}
    expect_doc = None
    expect_redirect = None
    word_quoted = False
    i = 0
    parens = 0

    def code(script):
        result.extend(segments(script, depth + 1))

    def printed(argv):
        return ' '.join(argv[command_start(argv) + 1:])

    def note(names):
        found = _FOUND.get()
        if found is not None:
            found.update(names)

    def emit(argv):
        result.append(argv)
        note(redefined(argv))
        script, _ = shell_input(argv)
        if script is not None:
            code(script)
        start = command_start(argv)
        name = os.path.basename(argv[start]) if start < len(argv) else None
        if name == 'eval':
            code(printed(argv))  # eval runs its arguments joined by spaces
        elif name in _SHADOWED.get():
            # A redefined data command may run its arguments as words ("$@") or joined
            # and read again (eval "$*"): keep both readings.
            tail = argv[start + 1:]
            joined = segments(printed(argv), depth + 1)
            if tail:
                emit(tail)
            if joined != [tail]:
                result.extend(joined)

    def finish_word():
        nonlocal active, word_quoted, expect_doc, expect_redirect
        if active:
            value = ''.join(word)
            if expect_doc is not None:
                pending_docs.append([value, word_quoted, expect_doc, _UNSET])
                expect_doc = None
            elif expect_redirect is not None:
                if expect_redirect == '<<<':
                    herestrings.append(value)
                expect_redirect = None
            else:
                words.append(value)
        word.clear()
        active = word_quoted = False

    def close_pipeline():
        # Each stage's output reaches every later stage; the last one's reaches the sink.
        for k, entry in enumerate(pipeline):
            later = pipeline[k + 1:]
            entry['downstream'] = sink or any(e['argv'] is None or not is_data(e['argv']) for e in later)
            argv = entry['argv']
            if argv is None or argv == ['}']:
                # A piped subshell or brace group: rescan with everything printed as code.
                state['resink'] = state['resink'] or entry['downstream']
                continue
            start = command_start(argv)
            if (entry['downstream'] and start < len(argv) and data_word(argv[start])
                    and os.path.basename(argv[start]) in _ARG_PRINTERS):
                code(printed(argv))
            if entry['herestrings'] and (entry['downstream'] or stdin_is_code(argv, depth)):
                for value in entry['herestrings']:
                    code(value)
        pipeline.clear()
        state['pipe_open'] = False

    def finish_command(pipe=False):
        finish_word()
        entry = None
        if words:
            argv = words[:]
            emit(argv)
            entry = {'argv': argv, 'downstream': None, 'herestrings': herestrings[:]}
            pipeline.append(entry)
            words.clear()
        elif pipe:
            # Nothing before `|` but a closed subshell: its output is what is piped.
            entry = {'argv': None, 'downstream': None, 'herestrings': []}
            pipeline.append(entry)
        herestrings.clear()
        for doc in pending_docs:
            if doc[3] is _UNSET:
                doc[3] = entry
        if pipe:
            state['pipe_open'] = True
        elif entry is not None or not state['pipe_open']:
            # An empty command (a newline right after `|`) continues the pipeline.
            close_pipeline()

    def doc_is_code(consumer):
        if consumer is None or consumer is _UNSET:
            return False
        if consumer['argv'] is None or stdin_is_code(consumer['argv'], depth):
            return True
        # A data consumer prints the body: code when that output may run, and a
        # pipeline still open at this newline fails closed.
        return consumer['downstream'] is not False

    def done():
        finish_command()
        if state['pipe_open']:
            close_pipeline()
        return state['resink'] and not sink

    while i < len(text):
        ch = text[i]
        if quote is None and ch == _stop and parens == 0:
            if done():
                return segments(text, depth, _stop, True)
            return result, i
        if quote is None and _stop == ')':
            if ch == '(':
                parens += 1
            elif ch == ')':
                parens -= 1
        if ch == '\\' and quote != "'" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == '\n':
                i += 2
                continue
            if quote == '"' and nxt not in '$`"\\':
                word.append(ch)
                active = True
                i += 1
                continue
            word.append(nxt)
            active = True
            word_quoted = True
            i += 2
            continue
        if ch in "\"'" and (quote is None or ch == quote):
            quote = ch if quote is None else None
            active = word_quoted = True
            i += 1
            continue
        dollar = _lmatch(text, i, '$(') if quote != "'" else None
        process = (_lmatch(text, i, '<(') or _lmatch(text, i, '>(')) if quote is None else None
        if process or dollar or quote != "'" and ch == '`':
            start = i
            opened = process or dollar
            backtick = opened is None
            # Output that becomes command words is code: an unquoted substitution at the
            # executable position or in the arguments of a command that may run them,
            # and every process substitution (its command reads or runs the output).
            name = command_name(words)
            becomes_words = bool(process) or quote is None and not _ASSIGNMENT.match(''.join(word)) and (
                name is None or not is_data(words) and name not in _HERMES)
            i = i + 1 if opened is None else opened
            nested, consumed = segments(text[i:], depth + 1, '`' if backtick else ')', sink or becomes_words)
            result.extend(nested)
            i = min(i + consumed + 1, len(text))
            word.append(text[start:i])
            active = True
            continue
        if quote is None:
            if ch == '#' and not active:
                end = text.find('\n', i)
                i = len(text) if end < 0 else end
                continue
            here = _lmatch(text, i, '<<')
            if here is not None and _lmatch(text, i, '<<<') is None:
                if ''.join(word).isdigit():
                    word.clear()
                    active = False
                finish_word()
                i = here
                strip = _lmatch(text, i, '-')
                expect_doc = strip is not None
                if strip is not None:
                    i = strip
                continue
            if ch in '<>':
                if ''.join(word).isdigit():
                    word.clear()
                    active = False
                finish_word()
                # Redirection operands are data, but their substitutions still run; a
                # here-string is the command's input.
                op, after = next((o, e) for o in ('<<<', '>>', '<>', '>', '<')
                                 if (e := _lmatch(text, i, o)) is not None)
                i = after
                suffix = _lmatch(text, i, '&') or _lmatch(text, i, '|')
                if suffix is not None:
                    i = suffix
                expect_redirect = op
                continue
            if ch in ' \t\r':
                finish_word()
                i += 1
                continue
            if ch in ';|&()\n':
                if ch == '(':
                    # `NAME ()` or `function NAME ()` defines a function called NAME.
                    finish_word()
                    named = words[1:] if words[:1] == ['function'] else words
                    if len(named) == 1 and _next_is(text, i + 1, ')'):
                        note({os.path.basename(named[0])} & DATA_COMMANDS)
                # Each heredoc retains its consumer across separators/pipelines.
                double = _lmatch(text, i, '||') if ch == '|' else None
                pipe = ch == '|' and double is None
                if double is not None:
                    i = double - 1  # `||` separates commands; it is not a pipe
                finish_command(pipe)
                i += 1
                amp = _lmatch(text, i, '&') if pipe else None
                if amp is not None:
                    i = amp  # `|&` pipes stderr as well
                if ch == '\n' and pending_docs:
                    for delimiter, quoted, strip_tabs, consumer in pending_docs:
                        body = []
                        while i < len(text):
                            end = text.find('\n', i)
                            end = len(text) if end < 0 else end
                            line = text[i:end]
                            i = min(end + 1, len(text))
                            if (line.lstrip('\t') if strip_tabs else line) == delimiter:
                                break
                            body.append(line)
                        content = '\n'.join(body)
                        if doc_is_code(consumer):
                            code(content)
                        elif not quoted:
                            # Double-quoted context scans substitutions, not body text.
                            escaped = content.replace('"', '\\"')
                            result.extend(segments('echo "' + escaped + '"', depth + 1)[0:-1])
                    pending_docs.clear()
                continue
        word.append(ch)
        active = True
        i += 1
    if done():
        return segments(text, depth, _stop, True)
    return (result, i) if _stop else result
