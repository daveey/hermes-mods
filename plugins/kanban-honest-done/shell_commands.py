"""Small static shell scanner: retain argv boundaries and separate code from data.

No shell is executed. This is deliberately not a shell evaluator (aliases, variables
containing commands and sourced files are not resolved).
"""
import os
import re

_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*=")
_SHELLS = {"sh", "bash", "dash", "zsh", "ksh"}


def command_start(words):
    """Find the executable after assignments and explicit execution wrappers."""
    i = 0
    while i < len(words):
        if _ASSIGNMENT.match(words[i]):
            i += 1
            continue
        if words[i] in {'if', 'then', 'else', 'elif', 'do', 'while', 'until', '!', '{'}:
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


def segments(text, depth=0, _stop=None):
    """Return argv lists, including explicit substitutions and shell -c scripts.

    Quotes are tracked before quote removal, so quoted punctuation cannot start a
    command. Heredoc bodies are data unless fed to a shell; unquoted heredocs still
    perform command substitution. Keep raw substitution spelling in outer argv so
    acceptance.py can resolve --body "$(cat file)" without executing it.
    """
    if depth > 24:
        return ([], len(text)) if _stop else []
    result, words, word = [], [], []
    active = False
    quote = None
    pending_docs = []
    expect_doc = None
    expect_redirect = False
    word_quoted = False
    i = 0
    parens = 0

    def finish_word():
        nonlocal active, word_quoted, expect_doc, expect_redirect
        if active:
            value = ''.join(word)
            if expect_doc is not None:
                pending_docs.append((value, word_quoted, expect_doc))
                expect_doc = None
            elif expect_redirect:
                expect_redirect = False
            else:
                words.append(value)
        word.clear()
        active = word_quoted = False

    def finish_command():
        finish_word()
        if words:
            argv = words[:]
            result.append(argv)
            start = command_start(argv)
            if start < len(argv) and os.path.basename(argv[start]) in _SHELLS:
                for j in range(start + 1, len(argv) - 1):
                    if re.fullmatch(r'-[a-zA-Z]*c[a-zA-Z]*', argv[j]):
                        result.extend(segments(argv[j + 1], depth + 1))
                        break
            words.clear()

    while i < len(text):
        ch = text[i]
        if quote is None and ch == _stop and parens == 0:
            finish_command()
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
        if quote != "'" and (text.startswith('$(', i) or ch == '`'):
            start = i
            backtick = ch == '`'
            i += 1 if backtick else 2
            nested, consumed = segments(text[i:], depth + 1, '`' if backtick else ')')
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
            if text.startswith('<<', i) and not text.startswith('<<<', i):
                if ''.join(word).isdigit():
                    word.clear()
                    active = False
                finish_word()
                i += 2
                expect_doc = i < len(text) and text[i] == '-'
                i += int(expect_doc)
                continue
            if ch in '<>':
                if ''.join(word).isdigit():
                    word.clear()
                    active = False
                finish_word()
                # Redirection operands are data, but their substitutions still run.
                op = re.match(r'(?:<<<|>>|<>|>|<)(?:[&|])?', text[i:])
                assert op is not None
                i += len(op.group())
                expect_redirect = True
                continue
            if ch in ' \t\r':
                finish_word()
                i += 1
                continue
            if ch in ';|&()\n':
                # Save the command consuming the pending heredoc before clearing it.
                finish_word()
                start = command_start(words)
                shell_doc = (start < len(words) and os.path.basename(words[start]) in _SHELLS
                             and not any(re.fullmatch(r'-[a-zA-Z]*c[a-zA-Z]*', arg)
                                         for arg in words[start + 1:]))
                finish_command()
                i += 1
                if ch == '\n' and pending_docs:
                    for delimiter, quoted, strip_tabs in pending_docs:
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
                        if shell_doc:
                            result.extend(segments(content, depth + 1))
                        elif not quoted:
                            # Double-quoted context scans substitutions, not body text.
                            escaped = content.replace('"', '\\"')
                            result.extend(segments('echo "' + escaped + '"', depth + 1)[0:-1])
                    pending_docs.clear()
                continue
        word.append(ch)
        active = True
        i += 1
    finish_command()
    return (result, i) if _stop else result
