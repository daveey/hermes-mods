"""Expected-output list literals are data, not argv. Payloads are classified, never executed."""
import pytest
from tests_guard import plugin, run, protected  # noqa: F401  (autouse fixture)

# A test that spawns unrelated helpers, polls for a while, then compares output to ["yes"].
CUSTODY = '''import subprocess, sys
from pathlib import Path


def test_custody(tmp_path, holds):
    owner = subprocess.Popen([sys.executable, "-c", "pass"])
    for _ in range(300):
        if owner.poll() is not None:
            break
    out = tmp_path / "out"
    assert out.read_text().split() == {expected}, owner
'''
EXPECTED = {'direct': '["yes"]', 'conditional': '(["yes"] if holds else ["no"])',
            'reversed': '["yes"]', 'membership': '[["yes"], ["no"]]'}


def payload(kind):
    text = CUSTODY.format(expected=EXPECTED[kind])
    if kind == 'reversed':
        text = text.replace('out.read_text().split() == ["yes"]', '["yes"] == out.read_text().split()')
    if kind == 'membership':
        text = text.replace('== [["yes"], ["no"]]', 'in [["yes"], ["no"]]')
    return text


KINDS = sorted(EXPECTED)


@pytest.mark.parametrize('kind', KINDS)
def test_compared_list_is_not_argv_write_and_exec(kind, tmp_path):
    code = payload(kind)
    assert run('write_file', {'path': str(tmp_path / 'test_x.py'), 'content': code}) is None
    assert run('execute_code', {'code': code}) is None


@pytest.mark.parametrize('kind', KINDS)
def test_compared_list_is_not_argv_patch(kind, tmp_path):
    path = tmp_path / 'test_x.py'
    path.write_text(payload(kind))
    args = {'path': str(path), 'old_string': 'break', 'new_string': 'break  # done'}
    assert run('patch', args) is None


def test_custody_death_predicate_edit_allowed(tmp_path):
    # The originally refused one-line edit, against a file of the same shape.
    path = tmp_path / 'test_update_custody.py'
    path.write_text(payload('conditional').replace(
        '        if owner.poll() is not None:',
        '        if not Path(f"/proc/{owner.pid}").exists() or "Z" in _state(owner.pid):'))
    args = {'path': str(path),
            'old_string': 'if not Path(f"/proc/{owner.pid}").exists() or "Z" in _state(owner.pid):',
            'new_string': 'if _state(owner.pid) in {"X", "Z"}:'}
    assert run('patch', args) is None


LAUNCHES = {
    'direct-call': 'import subprocess\nfor _ in range(16):\n    subprocess.Popen(["yes"], stdout=subprocess.DEVNULL)\n',
    'assigned-argv': 'import subprocess\ncmd = ["yes"]\nfor _ in range(16):\n    subprocess.Popen(cmd)\n',
    'argv-in-list': ('import subprocess\nargvs = [["yes"] for _ in range(16)]\n'
                     'procs = [subprocess.Popen(a) for a in argvs]\n'),
    'assigned-plus-compare': ('import subprocess\ncmd = ["yes"]\nassert cmd == ["yes"]\n'
                              'for _ in range(16):\n    subprocess.Popen(cmd)\n'),
    'conditional-argv': ('import subprocess\nfor i in range(16):\n'
                         '    subprocess.Popen(["yes"] if i else ["stress"])\n'),
    'compare-test-not-branch': ('import subprocess\nfor i in range(16):\n'
                                '    subprocess.Popen(["yes"] if ["yes"] == [i] else ["sha256sum"])\n'),
    'unparseable': 'import subprocess\ncmd = ["yes"]\nfor _ in range(16):\n    subprocess.Popen(cmd\n',
    'embedded-script': ('import subprocess, sys\nw = "import subprocess; subprocess.Popen(cmd)"\n'
                        'x = \'cmd = ["yes"]\'\nassert w != \'["yes"]\'\nfor _ in range(16):\n'
                        '    subprocess.Popen([sys.executable, "-c", x + w])\n'),
}


@pytest.mark.parametrize('name', sorted(LAUNCHES))
def test_real_launches_still_refused(name, tmp_path):
    code = LAUNCHES[name]
    assert run('write_file', {'path': str(tmp_path / 'load.py'), 'content': code})['action'] == 'block'
    assert run('execute_code', {'code': code})['action'] == 'block'
    path = tmp_path / 'edit.py'
    path.write_text(code)
    assert run('patch', {'path': str(path), 'old_string': 'import subprocess',
                         'new_string': 'import subprocess  # x'})['action'] == 'block'


def test_argv_evidence_unit():
    assert not plugin.guard._argv_evidence('x == ["yes"]\n')
    assert not plugin.guard._argv_evidence('x == (["yes"] if c else ["no"])\n')
    assert plugin.guard._argv_evidence('x = ["yes"]\n')
    assert plugin.guard._argv_evidence('x == ["yes"]\nif (\n')  # unparseable stays conservative
    assert plugin.guard._argv_evidence('ok = x == ["no"]; run(["yes"])\n')
