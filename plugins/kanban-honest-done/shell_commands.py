"""A bounded grammar for inert mentions; everything else is fail-closed.

This is not a shell evaluator. Only whole strings in G may exempt data arguments.
Outside G we retain the original any-position token scan, also opening nested text
so shell -c, substitutions and heredocs cannot hide mentions. Ambient shell state,
executable contents and Git hooks are not resolved by this static guard.
"""

import re
import shlex
from functools import lru_cache

_DATA = frozenset({'echo', 'printf', 'cat', 'grep', 'head', 'tail', 'wc', 'cd'})
_CHAR = re.compile(r'[A-Za-z0-9_./:=@%+,-]')
_SEPARATORS = set(';|&()\n')


class Segment(list):
    """Carry the WHOLE input, not a segment-local exemption decision."""
    def __init__(self, words, command):
        super().__init__(words)
        self.command = command


def _simple(words):
    if not words:
        return False
    if words[0] == 'printf':
        # Bash -v can evaluate array subscripts and mutate BASH_CMDS/PATH.
        # Accept output-only conversions; unknown/decorated %n falls back closed.
        args = words[1:]
        if args[:1] == ['--']:
            args = args[1:]
        if not args or args[0].startswith('-'):
            return False
        fmt, i = args[0], 0
        while (i := fmt.find('%', i)) >= 0:
            match = re.match(r"%(?:%|[-+ #0']*(?:[0-9]+|\*)?(?:\.(?:[0-9]+|\*))?[diouxXfFeEgGaAcbsq])", fmt[i:])
            if match is None:
                return False
            i += match.end()
        return True
    if words[0] in _DATA or words[0] == 'hermes':
        return True
    if words[:2] != ['git', 'commit']:
        return False
    i = 2
    while i < len(words):
        if words[i] == '-m':
            if i + 1 >= len(words):
                return False
            i += 2
        elif words[i] in {'-a', '--amend', '--allow-empty', '-q', '--no-edit'}:
            i += 1
        else:
            return False
    return True


@lru_cache(maxsize=128)
def _inert_parse(text):
    """Return command argv tuples, or None unless ALL bytes belong to G.

    Heredoc bodies are consumed at the physical newline, in redirection order.
    A missing delimiter, malformed list, or unsupported token revokes the exemption
    for the entire input, including commands and heredocs already scanned.
    """
    i, n = 0, len(text)
    commands, words, docs = [], [], []
    need_command = False
    redirected = False
    had_redirection = False

    def word():
        nonlocal i
        parts, quoted, active = [], False, False
        while i < n:
            ch = text[i]
            if _CHAR.fullmatch(ch):
                parts.append(ch)
                i += 1
                active = True
            elif ch in "'\"":
                quote = ch
                end = text.find(quote, i + 1)
                if end < 0:
                    return None
                value = text[i + 1:end]
                if quote == '"' and any(c in value for c in '$`\\'):
                    return None
                parts.append(value)
                quoted = active = True
                i = end + 1
            else:
                break
        return (''.join(parts), quoted) if active else None

    def finish():
        nonlocal redirected
        if not _simple(words):
            return False
        commands.append(tuple(words))
        words.clear()
        redirected = False
        return True

    while i < n:
        ch = text[i]
        if ch in ' \t':
            i += 1
            continue
        if ch == '>':
            # A literal write can poison an executable/configuration used here
            # or by a later invocation. No filesystem provenance is available,
            # so output redirects never grant data exemptions (including >>).
            return None
        if ch == '<':
            had_redirection = True
            redirected = True
            op = ch
            i += 1
            if i < n and text[i] == ch:
                op += ch
                i += 1
            while i < n and text[i] in ' \t':
                i += 1
            start = i
            target = word()
            if target is None:
                return None
            if op == '<<':
                raw = text[start:i]
                # Only one quoted string or one unquoted literal delimiter.
                if (target[0].startswith('-') or
                        (target[1] and raw not in {"'" + target[0] + "'", '"' + target[0] + '"'})):
                    return None
                docs.append(target)
            continue
        if ch in ';|&\n':
            op = ch
            i += 1
            if ch in '|&' and i < n and text[i] == ch:
                op += ch
                i += 1
            if op == '&' or (i < n and text[i] == '&'):
                return None
            if words:
                if not finish():
                    return None
                need_command = op in {'&&', '||', '|'}
            elif op != '\n' or redirected:
                return None
            if ch == '\n' and docs:
                for delimiter, quoted in docs:
                    body = []
                    while True:
                        if i >= n:
                            return None
                        end = text.find('\n', i)
                        end = n if end < 0 else end
                        line = text[i:end]
                        i = min(end + 1, n)
                        if line == delimiter:
                            break
                        body.append(line)
                    if not quoted and any(c in '\n'.join(body) for c in '$`\\'):
                        return None
                docs.clear()
            continue
        parsed = word()
        if parsed is None:
            return None
        if not parsed[1] and parsed[0].isdigit() and i < n and text[i] in '<>':
            return None  # fd-qualified redirection is outside this small grammar
        words.append(parsed[0])
        need_command = False
    if docs or need_command or (redirected and not words):
        return None
    if words and not finish():
        return None
    # Retain the conservative Git/input-redirection restriction: Git can invoke
    # hooks and other consumers; input provenance is outside this tiny grammar.
    if had_redirection and any(argv[:2] == ('git', 'commit') for argv in commands):
        return None
    return tuple(commands)


def within_inert_grammar(cmd):
    """Only membership in the whole-command grammar G permits data exemptions."""
    return _inert_parse(cmd) is not None


def _legacy_segments(command):
    """Main's lexer, including its malformed-input fallback; never exempts data."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=';&|()\n')
        lex.whitespace = ' \t\r'
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        tokens = command.split()
    out, cur = [], []
    for token in tokens:
        if token and set(token) <= _SEPARATORS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(token)
    if cur:
        out.append(cur)
    return out


def segments(command):
    parsed = _inert_parse(command)
    if parsed is not None:
        return [Segment(words, command) for words in parsed]
    # Preserve main's segments first. Nested quoted scripts and substitutions are
    # additional conservative readings, NEVER independently eligible for G.
    out, seen = [], set()
    pending = [command]
    while pending:
        text = pending.pop()
        if text in seen:
            continue
        seen.add(text)
        for words in _legacy_segments(text):
            if words not in out:
                out.append(words)
            for value in words:
                if ('hermes-hub' in value or 'kanban' in value and 'hermes' in value) and value != text:
                    pending.append(value)
        # Continuations join before shell lexing. Backticks delimit nested scripts;
        # read a second conservative view, but do not truncate already recovered
        # argument values (notably --body "$(cat file)").
        normalized = text.replace('\\\n', '').replace('`', '\n')
        if normalized != text:
            pending.append(normalized)
    return [Segment(words, command) for words in out]


def exempt_segment(seg):
    """Unannotated argv cannot prove membership and therefore defaults closed."""
    command = getattr(seg, 'command', None)
    return (command is not None and within_inert_grammar(command)
            and bool(seg) and seg[0] != 'hermes')
