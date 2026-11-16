"""Round-5 grammar membership is an explicit test oracle, not parser self-report."""

import pytest

from test_command_position import hits, _shape, _shell_ran, _STILL_DATA
from test_honest_done import core, plugin, OWN, make_board


REVIEW_CALLS = [
    'if true; then echo() {{ "$@"; }}; fi; echo {cmd}',
    '{{ echo() {{ "$@"; }}; }}; echo {cmd}',
    '! echo() {{ "$@"; }}; echo {cmd}',
    'shopt -s expand_aliases\nbuiltin alias echo=env\necho {cmd}',
    'builtin hash -p "$PWD/run.sh" cat; cat {cmd}',
    'builtin eval \'echo() {{ "$@"; }}\'; echo {cmd}',
    "shopt -s expand_aliases\nbuiltin alias cat=bash\ncat <<'EOF'\n{cmd}\nEOF",
    'if true; then cat() {{ bash; }}; fi; cat <<\'EOF\'\n{cmd}\nEOF',
    '{{ cat() {{ bash; }}; }}; cat <<\'EOF\'\n{cmd}\nEOF',
    'cat <<EOF > notes\nE\\\nOF\n{cmd}',
    'echo {cmd} > >(bash); wait',
    'echo {cmd} > >\\\n(bash); wait',
    "printf -v 'BASH_CMDS[cat]' /usr/bin/env; cat {cmd}",
    "printf -v 'a[$({cmd})]' x",
]


@pytest.mark.parametrize('shape', REVIEW_CALLS)
def test_round5_review_repros(tmp_path, monkeypatch, shape):
    command = _shape(shape)
    assert hits(command)
    assert plugin.consent_mod.write_texts('terminal', {'command': command})
    assert _shell_ran(tmp_path, command)
    db = make_board(tmp_path, tasks=[(OWN, 'test', 'running')])
    monkeypatch.setattr(plugin.comment_branches, 'target_paths', lambda board: [db])
    monkeypatch.setattr(plugin, 'classify_consent', lambda *a, **kw: None)
    monkeypatch.setattr(plugin, '_in_worker', lambda: False)
    cmd = f'hermes kanban comment {OWN} "IF FAIL x: y"'
    assert plugin._pre_tool_call('terminal', {'command': _shape(shape, cmd)})['action'] == 'block'


# 200 distinct strings, independently outside G: append a forbidden construct to
# each of ten valid data shapes. The entire string must lose its exemption, even
# when the unknown construct occurs AFTER the mention, or in a later pipeline.
_DATA_SHAPES = [
    'echo {cmd}', 'printf %s {quoted}', 'grep {quoted} f', 'git commit -m {quoted}',
    "cat <<'EOF'\n{cmd}\nEOF", 'echo {cmd} > f', 'echo {cmd} | cat',
    'cd x; echo {cmd}', 'echo {cmd} && echo ok', 'echo {cmd} || echo ok',
]
_OUTSIDE_SUFFIXES = [
    '; unknown', '; env A=1 echo ok', '; timeout 30 echo ok', '; builtin echo ok',
    '; command echo ok', '; exec echo ok', '; eval echo ok', '; alias x=y',
    '; hash echo', '; enable echo', '; source f', '; . f', '; sudo echo ok',
    '; echo $x', '; echo `true`', '; echo x\\y', '; echo *', '; echo ?',
    '; echo [abc]', '; echo x > >(bash)',
]
_OUTSIDE = [(_shape(s) + '\n' + suffix.lstrip('; '))
            for s in _DATA_SHAPES for suffix in _OUTSIDE_SUFFIXES]


@pytest.mark.parametrize('command', _OUTSIDE)
def test_whole_input_property_outside_g(command):
    # Detection precedes the new API assertion so a9f2ae2d gives behavioral RED,
    # not merely AttributeError because it lacks within_inert_grammar.
    assert hits(command), command
    assert plugin.consent_mod.write_texts('terminal', {'command': command}), command
    # Frozen main tokenizer/any-position rule: all its verdicts must survive.
    import shlex
    import os
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=';&|()\n')
        lex.whitespace = ' \t\r'
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        tokens = command.split()
    main_detected = any(os.path.basename(a) in {'hermes', 'hermes.exe'}
                        and os.path.basename(b) == 'kanban'
                        for a, b in zip(tokens, tokens[1:]))
    assert not main_detected or hits(command)
    assert not core._shell_commands().within_inert_grammar(command)


@pytest.mark.parametrize('shape', _DATA_SHAPES + _STILL_DATA)
def test_exemptions_are_members_of_g(shape):
    command = _shape(shape)
    assert not hits(command)
    assert core._shell_commands().within_inert_grammar(command)


@pytest.mark.parametrize('command', [
    'echo hi & echo hermes kanban comment t_X hi',
    'echo hi |& echo hermes kanban comment t_X hi',
    'echo hi <> f; echo hermes kanban comment t_X hi',
    'echo hi >& f; echo hermes kanban comment t_X hi',
    'echo hi <& f; echo hermes kanban comment t_X hi',
    'A=1 echo hermes kanban comment t_X hi',
    'echo hermes kanban comment t_X hi; git -c x=y commit -m hi',
    'echo hermes kanban comment t_X hi; git commit --file f',
    'echo hermes kanban comment t_X hi; git commit -m',
    'echo hermes kanban comment t_X hi; echo "${x}"',
    'echo hermes kanban comment t_X hi; echo "x\\y"',
    'cat <<EOF\nhermes kanban comment t_X hi\n$x\nEOF',
    'cat <<EOF\nhermes kanban comment t_X hi\n`true`\nEOF',
    'cat <<EOF\nhermes kanban comment t_X hi\nx\\y\nEOF',
    'cat <<EOF\nhermes kanban comment t_X hi',
    'echo hermes kanban comment t_X hi; > file',
    'echo hermes kanban comment t_X hi 2>file',
    'cat <<E"OF"\nhermes kanban comment t_X hi\nEOF',
    "printf '%n' PATH; echo hermes kanban comment t_X hi",
    "printf '%10n' PATH; echo hermes kanban comment t_X hi",
    "printf '%+n' PATH; echo hermes kanban comment t_X hi",
    "printf '%ln' PATH; echo hermes kanban comment t_X hi",
    "printf '%s' '[core]' > .git/config; git commit -m 'hermes kanban comment t_X hi'",
    "git commit -m 'hermes kanban comment t_X hi' > .git/config",
])
def test_explicit_nonmembers(command):
    assert hits(command)
    assert not core._shell_commands().within_inert_grammar(command)


def test_property_corpus_size():
    assert len(set(_OUTSIDE)) >= 140


def test_unknown_segment_has_no_exemption():
    assert core._kanban_verb(['echo', 'hermes', 'kanban', 'comment', 't_X', 'hi'])


def test_segment_membership_is_whole_input_and_order_independent():
    unsafe = core._segments('echo hermes kanban comment t_X hi; unknown')
    safe = core._segments('echo hermes kanban comment t_X hi')
    assert not any(core._kanban_verb(s) for s in safe)
    assert any(core._kanban_verb(s) for s in unsafe)
    # A later parse cannot mutate the earlier decision (no ContextVar leakage).
    assert not any(core._kanban_verb(s) for s in safe)
