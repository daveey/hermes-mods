"""F5/F6/N5/N6: decoded indirect commands and argparse-compatible options."""
import os
import shlex
import subprocess

import pytest
from test_source_review_regressions import OWN, OTHER, board, consent, core, stop


@pytest.mark.parametrize('prefix', ['(xargs', 'true&&xargs', 'true&xargs', '"xargs"', "x''args", '(parallel', '"parallel"'])
@pytest.mark.parametrize('nested', [False, True])
def test_decoded_indirect_consent(tmp_path, prefix, nested):
    env = {'HERMES_HOME': str(tmp_path)}
    stop(env)
    command = prefix + ' -a ids hermes kanban comment retry'
    if prefix.startswith('('):
        command += ')'
    if nested:
        command = 'sh -c ' + shlex.quote(command)
    assert consent.check_relay('terminal', {'command': command}, ['chat-a'], env)
    assert consent.check_relay('tool_call', {'calls': [{'name': 'terminal', 'arguments': {'command': command}}]}, ['chat-a'], env)


@pytest.mark.parametrize('verb', ['complete', 'done', 'request-review'])
@pytest.mark.parametrize('flag', ['--force', '--for', '--f'])
def test_force_is_zero_arity(tmp_path, verb, flag):
    db = board(tmp_path, 'DONE: check 4 requires >= 25% free disk.')
    option = '--summary' if verb == 'request-review' else '--result'
    assert core.check_shell(f'hermes kanban {verb} {flag} {OWN} {option} "check 4 failed; waived"', OWN, lambda: [db])


@pytest.mark.parametrize('verb,option', [('complete', '--res'), ('complete', '--summ'), ('request-review', '--summ')])
@pytest.mark.parametrize('duplicate', [False, True])
def test_option_prefix_and_last_wins(tmp_path, verb, option, duplicate):
    db = board(tmp_path, 'DONE: check 4 requires >= 25% free disk.')
    earlier = f'{option} "all verified" ' if duplicate else ''
    assert core.check_shell(f'hermes kanban {verb} {OWN} {earlier}{option} "check 4 failed; waived"', OWN, lambda: [db])


@pytest.mark.parametrize('option', ['--summ', '--res', '--metad'])
def test_dynamic_abbreviated_handoff(option):
    assert core.check_shell(f'hermes kanban complete {OWN} {option} "$VALUE"', OWN, lambda: [])


@pytest.mark.parametrize('verb,flags', [('edit', '--title x'), ('complete', '--result verified'), ('request-review', '--summary verified')])
def test_parallel_appended_targets(verb, flags):
    assert core.check_shell(f'parallel -a ids hermes kanban {verb} {flags}', OWN, lambda: [])


def test_conservative_unrelated_indirect_bound():
    # Intentionally held: static all-view provenance is not a shell evaluator.
    assert core.check_shell(f'xargs echo < ids; hermes kanban complete {OTHER} --result verified', OWN, lambda: [])
    assert core.check_shell(f'hermes kanban complete {OTHER} --result verified', OWN, lambda: []) is None


def test_end_options_and_last_value():
    assert core._opt(['--summary', 'first', '--summ=last'], '--summary') == 'last'
    assert core._opt(['--', '--summary', 'data'], '--summary') is None
    assert core._positionals(['--force', OWN, '--result', OTHER]) == [OWN]


@pytest.mark.parametrize('prefix', ['(xargs', 'true&&xargs', '"xargs"', "x''args"])
def test_real_shell_indirect_spelling(tmp_path, prefix):
    fake, capture = tmp_path / 'hermes', tmp_path / 'argv'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    fake.chmod(0o700)
    (tmp_path / 'ids').write_text(OWN + '\n')
    command = prefix + ' -a ids hermes kanban comment'
    if prefix.startswith('('):
        command += ')'
    subprocess.run(['/bin/bash', '-c', command], cwd=tmp_path, check=True,
                   env={'PATH': str(tmp_path) + os.pathsep + '/usr/bin:/bin', 'CAPTURE': str(capture)})
    assert capture.read_text().splitlines() == ['kanban', 'comment', OWN]


def _real_parser():
    pytest.importorskip('hermes_cli.kanban_parser')
    from test_comment_branches_cli_options import _core_root, _sub
    return _sub(_core_root(), 'kanban')


def _assert_schema(parser, verb):
    from test_comment_branches_cli_options import _options
    values, flags = _options(parser)
    assert values == core._CLI_VALUES[verb]
    assert flags == core._CLI_FLAGS


@pytest.mark.parametrize('verb', ['complete', 'edit', 'request-review'])
def test_exact_parser_schema(verb):
    from test_comment_branches_cli_options import _sub
    _assert_schema(_sub(_real_parser(), verb), verb)


@pytest.mark.parametrize('nargs', ['?', '*', '+', 1, 2])
def test_arity_drift_killed(nargs):
    from test_comment_branches_cli_options import _sub
    parser = _sub(_real_parser(), 'complete')
    parser._option_string_actions['--summary'].nargs = nargs
    with pytest.raises(AssertionError):
        _assert_schema(parser, 'complete')


@pytest.mark.parametrize('verb', ['complete', 'done', 'request-review'])
def test_real_parser_force_prefixes_and_duplicate_handoff(verb):
    from test_comment_branches_cli_options import _core_root
    pytest.importorskip('hermes_cli.kanban_parser')
    root = _core_root()
    option = '--summ'
    args = ['--for', OWN, option, 'first', option, 'check 4 failed; waived']
    # Legacy done is classified conservatively even on cores without its alias.
    ns = root.parse_args(['kanban', 'complete' if verb == 'done' else verb, *args])
    assert getattr(ns, 'task_ids', [getattr(ns, 'task_id', None)]) == [OWN]
    assert ns.force is True
    assert core._positionals(args, verb) == [OWN]
    assert core._opt(args, '--summary', verb=verb) == ns.summary
