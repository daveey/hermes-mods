"""Deterministic differential corpus for mac-heavy-guard (strings only; nothing is ever executed).

`build()` returns the command list. tests/fixtures/main_verdicts.json holds origin/main's verdict for each
command (produced by `python3 diff_corpus.py MAIN_PLUGIN_DIR OUT.json` run against a separate checkout of main).
"""
import importlib.util
import json
import shlex
import sys
from pathlib import Path

HEAVY = ['pytest', 'cargo build --release', './build.sh', 'py""test', "ca''rgo build", r'p\ytest',
         'docker build .', 'make -j8', 'swift build']

# Every reviewer repro from F1-F14 (reviews 6092831609, 6092967625, 6093092970, 6093203097, 6093320272,
# 6093400492, 6093461628, 6093503973, 6093656803) plus the original C1-C4 fixtures. Repros that need real
# script files on disk (`bash run.sh; x=$(cd proj)` family) stay in tests/test_source_review.py.
ANSI_ARITH_PAIRS = [
    ('echo "$(( $(pytest) + 1 ))"', 'echo "$(( $(echo 0) + 1 ))"'),
    ('echo "$(( `pytest` + 1 ))"', 'echo "$(( `echo 0` + 1 ))"'),
    ('cat <<EOF\n$(( $(pytest) + 1 ))\nEOF', 'cat <<EOF\n$(( $(echo 0) + 1 ))\nEOF'),
    ('echo "$((pytest) | (ls))"', 'echo "$((echo ok) | (ls))"'),
    ('echo "$((pytest)|(true))"', 'echo "$((echo ok)|(true))"'),
    ('echo "$((true)|(pytest))"', 'echo "$((true)|(echo ok))"'),
    ('echo "$((pytest) && (true))"', 'echo "$((echo ok) && (true))"'),
    ('echo "$((pytest) |& (true))"', 'echo "$((echo ok) |& (true))"'),
    ('echo "$((pytest)>/dev/null|(true))"', 'echo "$((echo ok)>/dev/null|(true))"'),
    ('echo "a $((pytest) && (true)) b"', 'echo "a $((echo ok) && (true)) b"'),
    ('bash -c \'echo "$((pytest) | (true))"\'', 'bash -c \'echo "$((echo ok) | (true))"\''),
    ('cat <<EOF\n$((pytest) )\nEOF', 'cat <<EOF\n$((echo ok) )\nEOF'),
    ('cat <<EOF\n$((pytest) | (true))\nEOF', 'cat <<EOF\n$((echo ok) | (true))\nEOF'),
    ("$'pytest'", "$'echo' ok"),
    ("bash -c $'pytest'", "bash -c $'echo ok'"),
    ("bash -c $'py\\\ntest'", "bash -c $'ec\\\nho ok'"),
    ('echo "$((pytest) )"', 'echo "$((echo ok) )"'),
    ('echo "$((pytest) | cat)"', 'echo "$((echo ok) | cat)"'),
    ('x="$((pytest);(pytest))"', 'x="$((echo ok);(echo ok))"'),
    ("echo $'\\'' ; pyt\\\nest", "echo $'\\'' ; ec\\\nho ok"),
    (r"$'py\x74est'", r"$'ec\x68o' ok"),
    (r"$'py\164est'", r"$'ec\150o' ok"),
    (r"$'py\u0074est'", r"$'ec\u0068o' ok"),
    (r"$'py\U00000074est'", r"$'ec\U00000068o' ok"),
    ("py$'test'", "ec$'ho' ok"),
    ("echo \"$($'pytest')\"", "echo \"$($'echo' ok)\""),
    (r"$'pytest\0ignored'", r"$'echo\0ignored' ok"),
]

REPROS = [h for h, _ in ANSI_ARITH_PAIRS] + [
    'if true; then pytest; fi',
    'pip install -rrequirements.txt',
    'case $(pytest) in x) echo ;; esac',
    'case "$(cargo build --release)" in x) echo ;; esac',
    'case `pytest` in x) echo ;; esac',
    'case x in x) echo ;; $(pytest)) echo ;; esac',
    'ssh host -o ProxyCommand=\'pytest\' true',
    'echo $(cargo build)', 'x=$(cargo build --release)', '"$(swift build)"', 'echo `cargo build`',
    'for x in $(xcodebuild build); do true; done', 'echo $(rsync -a a b)',
    'echo $(case x in x) pytest;; esac)',
    'echo "$(case x in x) pytest;; esac)"',
    'echo $(time case x in x) pytest;; esac)',
    'echo $(time -p case x in x) pytest;; esac)',
    'echo $(time ! case x in x) pytest;; esac)',
    'echo $(coproc case x in x) pytest;; esac)',
    'echo $(function f case x in x) pytest;; esac; f)',
    'echo $(time -- case x in x) pytest;; esac)',
    'echo $(coproc a-b case x in x) pytest;; esac)',
    'echo $(function 1f case x in x) pytest;; esac)',
    'echo $(coproc a$(:) case x in x) pytest;; esac)',
    'echo $(coproc a`:` case x in x) pytest;; esac)',
    'echo $(coproc a$(echo ")"|tr -d ")") case x in x) pytest;; esac)',
    'echo $(coproc a$(: \\)) case x in x) pytest;; esac)',
    'echo $(if :; then :; elif coproc a$(: ")") case x in x) pytest;; esac; then :; fi)',
    'echo $(echo $(coproc a${x:-)} case x in x) pytest;; esac))',
    'echo $(elif case x in x) swift build;; esac)',
    'echo $(if false; then :; elif coproc a$(: ")") case x in x) py""test;; esac; then :; fi)',
    'echo $(if false; then :; elif coproc a$(: ")") case x in x) ca\'\'rgo build;; esac; then :; fi)',
    'echo $(if false; then :; elif coproc a$(: ")") case x in x) p\\ytest;; esac; then :; fi)',
    'echo $(if false; then :; elif coproc a$(: ")") case x in x) ./build.sh;; esac; then :; fi)',
    'echo "$(if false; then :; elif function a case x in x) ./build.sh;; esac; then :; fi; a)"',
    'echo "$(echo ${x:-)}; pytest)"',
    'echo "$(echo ${x:-)} ; case x in x) pytest;; esac)"',
    'echo $(echo ${x:-)}; ./build.sh)',
    'ssh hub bash -s <<\'EOF\'\npytest\nEOF',
    # F16/F17 (review 6093835158)
    'echo $(ca\\\nse x in x) pytest;; esac)', 'echo $(ca\\\nse x in x) cargo build;; esac)',
    'echo $(c\\\na\\\nse x in x) pytest;; esac)', 'echo $(nosuchkw ca\\\nse x in x) pytest;; esac)',
    'echo $(if :; then ca\\\nse x in x) pytest;; esac; fi)', 'echo $(nosuchkw case; x in x) pytest;; esac)',
    'echo "$(echo ${x:-${y})}; pytest)"', 'echo "$(echo ${x:-\'}\')}; pytest)"',
    'echo "$(echo ${x:-\\})}; pytest)"', 'x="$(echo ${x:-${y})}; pytest)"',
    'echo $(echo ${x:-${y})}; ./build.sh)',
    # F18 (review 6093966184): backslash-newline splitting the `$(` opener
    'echo "$\\\n(case x in x) pytest;; esac)"', 'x="$\\\n(case x in x) pytest;; esac)"',
    'echo "$\\\n(echo ${x:-)}; pytest)"', 'echo "$\\\n(echo ${x:-${y})}; pytest)"',
    'echo "$\\\n(coproc a case x in x) cargo build;; esac)"', 'echo "$\\\n(pytest)"',
    'cat <<EOF\n$\\\n(case x in x) pytest;; esac)\nEOF',
]

# Deliberate new allows: one syntax error, and the exact benign continuation
# twin previously held only by tailnet's ANSI-C quote/continuation confusion.
NEW_ALLOW_REASONS = {
    'echo $(echo ${x:-}) pytest)': 'bash syntax error (unbalanced `)`); not executable',
    "echo $'\\'' ; ec\\\nho ok": 'bash executes only echo; ANSI-C apostrophe no longer hides continuation joining',
}

PREFIXES = ['', 'time', 'time -p', 'time --', 'time -p --', 'time !', '!', 'coproc', 'coproc w', 'coproc a-b',
            'function f', 'function a.b', 'coproc a$(:)', 'coproc a`:`', 'coproc a$(echo ")")',
            'coproc a$(: \\))', 'function a$(echo ")")', 'elif', 'then', 'do', 'else', 'if', 'while', 'until',
            'nosuchkw', 'time coproc w', '! time -p -- function 1f', 'coproc a${x:-)}', 'coproc a"$(:)"']


def build():
    out = list(REPROS) + [b for _, b in ANSI_ARITH_PAIRS] + ['echo "$((1+2))"'] + list(NEW_ALLOW_REASONS)
    for h in HEAVY:
        out += [h, 'echo $(' + h + ')', 'echo "$(' + h + ')"', 'echo `' + h + '`', 'x=$(' + h + ')',
                'echo "$\\\n(case x in x) ' + h + ';; esac)"', 'echo "$\\\n(' + h + ')"',
                'echo "$\\\n(echo ${x:-)}; ' + h + ')"',
                'if true; then ' + h + '; fi', 'if false; then :; elif true; then ' + h + '; fi',
                'while true; do ' + h + '; done', '{ ' + h + '; }', '( ' + h + ' )', 'f() { ' + h + '; }; f',
                'function f { ' + h + '; }; f', 'time ' + h, 'time -p ' + h, 'coproc ' + h,
                'case x in x) ' + h + ';; esac', 'for i in 1; do ' + h + '; done', 'echo ${x:-)}; ' + h,
                'echo $(echo ${x:-)}; ' + h + ')', 'echo "$(echo ${x:-)}; ' + h + ')"',
                'echo $(ca\\\nse x in x) ' + h + ';; esac)', 'echo "$(echo ${x:-${y})}; ' + h + ')"',
                'echo "$(echo ${x:-\'}\')}; ' + h + ')"', 'echo $(echo ${x:-\\})}; ' + h + ')']
        for p in PREFIXES:
            sp = (p + ' ') if p else ''
            out += ['echo $(' + sp + 'case x in x) ' + h + ';; esac)',
                    'echo "$(' + sp + 'case x in x) ' + h + ';; esac)"',
                    'echo $(if :; then :; elif ' + sp + 'case x in x) ' + h + ';; esac; then :; fi)',
                    'echo $(echo $(' + sp + 'case x in x) ' + h + ';; esac))']
    base = list(dict.fromkeys(out))
    wrapped = []
    for c in base:
        wrapped += ['bash -c ' + shlex.quote(c), 'sh -c ' + shlex.quote(c), 'ssh -o BatchMode=yes host ' + shlex.quote(c)]
    return list(dict.fromkeys(base + wrapped))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verdicts(plugin_dir, commands):
    """{command: [local_held, tailnet_held]} for the guard/tailnet runner under `plugin_dir`'s plugins/ tree."""
    import os
    os.environ.pop('MAC_HEAVY_OK', None)
    plugin_dir = Path(plugin_dir)
    guard = load('dc_guard_' + str(abs(hash(str(plugin_dir)))), plugin_dir / 'guard.py')
    runner = load('dc_runner_' + str(abs(hash(str(plugin_dir)))), plugin_dir.parent / 'tailnet' / 'runner.py')
    result = {}
    for c in commands:
        try:
            local = bool(guard.check_shell(c))
        except Exception:
            local = True
        try:
            tail = bool(runner.workload_reason(c))
        except Exception:
            tail = True
        result[c] = [local, tail]
    return result


if __name__ == '__main__':
    cmds = build()
    Path(sys.argv[2]).write_text(json.dumps(verdicts(sys.argv[1], cmds), indent=0, sort_keys=True))
    print(len(cmds), 'commands')
