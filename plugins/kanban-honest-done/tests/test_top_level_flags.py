"""`hermes [-p/--profile X ...] kanban ...` is the same command as `hermes kanban ...` (core strips top-level flags)."""
import pytest

from test_honest_done import core, plugin, OWN

CASES = {
    'waiver': 'hermes {f} kanban complete ' + OWN + ' --result "check 4 failed; waived"',
    'narrow': 'hermes {f} kanban edit ' + OWN + ' --title new',
    'handoff': 'hermes {f} kanban complete ' + OWN + ' --result "$(cat s)"',
}
FLAGS = ['', '-p default', '--profile default', '--profile=default', '-p x --model m', '--reasoning high -p x', '-c -p x', '--', '-p x --', '--model m --',
         '--mod m', '--reas high', '--prov x', '--usage x', '--cpu x', '--tool x', '--skil x', '--res x', '--one x']


def held(cmd):
    return core.check_shell(cmd, OWN, lambda: [])


@pytest.mark.parametrize('name', CASES)
@pytest.mark.parametrize('flags', FLAGS)
def test_flagged_form_is_held_like_plain(name, flags):
    plain = held(CASES[name].format(f=''))
    assert plain  # control: the plain command is refused
    assert held(CASES[name].format(f=flags)) == plain


def test_allow_controls_still_allowed():
    ok = [f'hermes {f} kanban complete {OWN} --result "Shipped the fix; tests pass."'.replace('  ', ' ') for f in FLAGS]
    ok += [f'hermes {f} kanban show {OWN}'.replace('  ', ' ') for f in FLAGS]
    for cmd in ok:
        assert held(cmd) is None, cmd


def test_flag_before_non_kanban_is_not_kanban():
    assert core._kanban_verb(['hermes', '-p', 'default', 'chat', 'kanban', 'edit', OWN]) is None
    assert core._kanban_verb(['hermes', '-p', 'kanban', 'status']) is None  # `kanban` is the profile value
    assert core._kanban_verb(['hermes', '-p', 'x', 'kanban', 'edit', OWN, '--title', 'n'])[0] == 'edit'
    assert core._kanban_verb(['python', '-m', 'hermes_cli.main', '--profile=x', 'kanban', 'edit', OWN])[0] == 'edit'


@pytest.mark.parametrize('flags', FLAGS)
def test_comment_branches_and_consent_cover_profile_flag(flags):
    command = f'hermes {flags} kanban --board b comment {OWN} "IF FAIL x: y"'
    assert core._kanban_verb(core._segments(command)[0]) == ('comment', [OWN, 'IF FAIL x: y'])
    writes = list(plugin.comment_branches.writes('terminal', {'command': command}, core))
    assert writes == [(OWN, 'IF FAIL x: y', 'b', False)]
    assert plugin.consent_mod.write_texts('terminal', {'command': command})


def test_static_list_matches_core():
    pytest.importorskip('hermes_cli._parser')
    from hermes_cli._parser import top_level_value_flag_sets, PRE_ARGPARSE_INHERITED_FLAGS
    required, optional = top_level_value_flag_sets()
    mine = plugin.comment_branches
    pre = {name for name, takes in PRE_ARGPARSE_INHERITED_FLAGS if takes}
    assert mine.TOP_LEVEL_VALUE_FLAGS == required | pre
    assert mine.TOP_LEVEL_OPTIONAL_VALUE_FLAGS == optional
    from hermes_cli._parser import build_top_level_parser
    parser, _, _ = build_top_level_parser()
    assert mine.TOP_LEVEL_OPTIONS == {o for a in parser._actions for o in a.option_strings} | {n for n, _ in PRE_ARGPARSE_INHERITED_FLAGS}
    # Exhaust every unique prefix of every real value flag; profile is stripped before argparse.
    options = parser._option_string_actions
    for option in required:
        if not option.startswith('--'):
            continue
        for length in range(3, len(option) + 1):
            prefix = option[:length]
            hits = [o for o in options if o.startswith(prefix)]
            if prefix in options or hits == [option]:
                for form in (prefix + ' x', prefix + '=x'):
                    assert held(f'hermes {form} kanban edit {OWN} --title new'), form


@pytest.mark.parametrize('flags', ['-- --', '-- -p x', '-p x -- --', '--model m -- --', '-- chat'])
def test_marker_negative_controls(flags):
    command = f'hermes {flags} kanban edit {OWN} --title new'
    assert core._kanban_verb(core._segments(command)[0]) is None
    assert held(command) is None


@pytest.mark.parametrize('flags', ['--i', '--c', '--i x', '--c x', '--i=x', '--c=x', '--con -p x'])
def test_ambiguous_and_optional_value_flags_fail_closed(flags):
    # Ambiguous options are invalid CLI, but neither arity may conceal a write.
    assert held(f'hermes {flags} kanban edit {OWN} --title new')


@pytest.mark.parametrize('flags', FLAGS)
@pytest.mark.parametrize('args', [
    'complete ' + OWN + ' --result "check 4 failed; waived"',
    'edit ' + OWN + ' --title new',
    'complete ' + OWN + ' --result "$(cat s)"',  # held by the unresolved-handoff check once it lands (PR 532)
])
def test_verb_is_recognised_identically(flags, args):
    plain = [core._kanban_verb(s) for s in core._segments('hermes kanban ' + args)]
    assert plain[0] is not None
    assert [core._kanban_verb(s) for s in core._segments(f'hermes {flags} kanban {args}')] == plain
