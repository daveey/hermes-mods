"""Deterministic corpus for the main-vs-head differential test (classification only).

Nothing here is executed: commands are only passed to guard.check_shell.
All paths are synthetic (/home/sqlite-guard-fixture); no file is ever touched.
"""
import itertools

HOME_PARENT = "/home/sqlite-guard-fixture"

# Every command a #531 review round reported as a bypass (base-allowed or
# head-allowed), including the compound pipeline-element forms of round 6.
REVIEW_REPROS = [
    "rm -f ~/.hermes/cache/../state.db",
    "rm -rf ~/.hermes",
    "mv -t /unrelated ~/.hermes",
    "mv --target-directory=/unrelated ~/.hermes",
    "mv --ta /unrelated ~/.hermes",
    "mv --target-director=/unrelated ~/.hermes",
    "ssh example.com \"mv --t /unrelated ~/.hermes\"",
    "cd /nonexistent ; rm -rf .hermes",
    "cd /nonexistent || rm -rf .hermes",
    "D=.hermes; cd /nonexistent ; rm -rf \"$D\"",
    "cd /nonexistent && echo done ; rm -rf .hermes",
    "cd /nonexistent && echo done || rm -rf .hermes",
    "cd ~/.hermes || true ; rm -rf kanban.db",
    "cd ~/.hermes || true && rm -rf *",
    "cd .hermes || exit; true; rm -f kanban.db",
    "true | true && cd ~/.hermes; rm -rf *",
    "D=~/.hermes; true | true || cd \"$D\" || exit; rm -rf *",
    "D=~/.hermes; (D=/tmp; ls | head) && rm -rf \"$D\"",
    "cd ~/.hermes; (cd /tmp && ls | head) && rm -rf .hermes",
    "true | (cd /tmp) && rm -rf ~/.hermes",
    "ls | { cd ~/.hermes && rm -rf state.db; }",
    "true | { cd ~/.hermes && rm -rf state.db; } && echo done",
    "ls | { cd ~/.hermes && rm -rf state.db; } | cat",
    "ls | while cd ~/.hermes && rm -rf state.db; do break; done",
    "ls | until cd ~/.hermes && rm -rf state.db; do break; done",
    "ls | if cd ~/.hermes && rm -rf state.db; then :; fi",
    "python3 -c 'from os import unlink; unlink(\"~/.hermes/state.db\")'",
    # Round 7: the last pipeline element's cd survives under zsh, ksh and bash lastpipe.
    "zsh -c 'ls | cd ~/.hermes && rm -rf state.db'",
    "bash -c 'shopt -s lastpipe; ls | cd ~/.hermes && rm -rf state.db'",
    "bash -O lastpipe -c 'ls | cd ~/.hermes && rm -rf state.db'",
    "ssh example.com zsh -c \"'ls | cd ~/.hermes && rm -rf state.db'\"",
    "ssh example.com \"zsh -c 'ls | cd ~/.hermes && rm -rf state.db'\"",
    "ssh example.com 'shopt -s lastpipe; ls | cd ~/.hermes && rm -rf state.db'",
    "ssh example.com \"bash -c 'shopt -s lastpipe; ls | cd ~/.hermes && rm -rf state.db'\"",
    "ssh example.com 'ls | cd ~/.hermes; rm -rf state.db'",
    "zsh -c 'ls | cd ~/.hermes; rm -rf state.db'",
    # Round 8: negated cd and a shadowing cd() function still run a `||` RHS after a cd that took effect.
    "! cd ~/.hermes || rm -rf state.db",
    "! command cd ~/.hermes || rm -rf state.db",
    "time ! cd ~/.hermes || rm -rf state.db",
    "! time cd ~/.hermes || rm -rf state.db",
    "bash -c '! cd ~/.hermes || rm -rf state.db'",
    "sh -c '! cd ~/.hermes || rm -rf state.db'",
    "zsh -c '! cd ~/.hermes || rm -rf state.db'",
    "ssh example.com '! cd ~/.hermes || rm -rf state.db'",
    "zsh -c '! ls | cd ~/.hermes || rm -rf state.db'",
    "cd() { builtin cd \"$@\"; false; }; cd ~/.hermes || rm -rf state.db",
    "function cd { builtin cd \"$@\"; return 1; }; cd ~/.hermes || rm -rf state.db",
    "cd() { command cd \"$@\"; return 1; }; cd ~/.hermes || rm -rf state.db",
    "bash -c 'cd() { builtin cd \"$@\"; false; }; cd ~/.hermes || rm -rf state.db'",
    "ssh example.com 'cd() { builtin cd \"$@\"; false; }; cd ~/.hermes || rm -rf state.db'",
    "! cd /nonexistent && rm -rf .hermes",
    # The plain `||` forms follow from the same rule (no cwd is assumed for a `||` RHS).
    "cd ~/.hermes || rm -rf state.db",
    "bash -c 'cd ~/.hermes || rm -rf state.db'",
    "sh -c 'cd ~/.hermes || rm -rf state.db'",
    "ssh example.com 'cd ~/.hermes || rm -rf state.db'",
]

# Setup (S), neutral (N) and kill (R) fragments. S establishes a dangerous
# cwd/alias; N is state-changing noise that does not matter; R is the kill.
_S_R = [
    ("cd ~/.hermes", "rm -rf state.db"),
    ("cd ~/.hermes", "rm -rf *"),
    ("D=~/.hermes", 'rm -rf "$D"'),
    ("pushd ~/.hermes", "rm -rf state.db"),
    ("export D=~/.hermes", 'rm -rf "$D"'),
]
_N = ["ls", "cd /tmp", "D=/tmp"]

_SHAPES = [
    "{S} && {R}", "{S} || {R}", "{S}; {R}", "{S}\n{R}", "{S} & {R}",
    "{S} || {N}; {R}", "{S} && {N}; {R}", "{S} || {N} && {R}",
    "({S}) && {R}", "({S}; {R})", "({S}); {R}", "( ({S}) ) && {R}",
    "{S}; ({N}; {N} | cat) && {R}",
    "{S}; ({N} | cat) && {R}",
    "{S}; {N} | cat && {R}",
    "{S}; {N} | cat; {R}",
    "{S}; $({N} | cat) && {R}",
    "{S}; cat <({N} | cat) && {R}",
    "{S}; `{N} | cat` && {R}",
    "{N} | {S}; {R}",
    "{N} | {S} && {R}",
    "{N} | {{ {S} && {R}; }}",
    "{N} | {{ {S}; {R}; }}",
    "{N} | {{ {S}; {R}; }} && echo ok",
    "{N} | {{ {S}; {R}; }} | cat",
    "{{ {S}; }} | cat; {R}",
    "{{ {S}; {N} | cat; }} && {R}",
    "{N} | while {S} && {R}; do break; done",
    "{N} | until {S} && {R}; do break; done",
    "{N} | if {S} && {R}; then :; fi",
    "{N} | for i in 1; do {S} && {R}; done",
    "{N} | case x in x) {S}; {R};; esac",
    "if {S}; then {R}; fi",
    "while {S}; do {R}; break; done",
    "for i in 1; do {S}; {R}; done",
    "{S} && {N} | cat && {R}",
    "{N} | {N} && {S} && {R}",
    "{N} | {N} && {S} || exit; {R}",
    "cd /nonexistent && {N}; {S}; {R}",
    "{S}; {N}; {R}",
    "true | true && {S} || exit; {R}",
]

_WRAPPERS = ["{c}", "bash -c '{c}'", "sh -c '{c}'", "ssh example.com '{c}'"]


def corpus():
    """Return the ordered, de-duplicated list of command strings."""
    seen, out = set(), []

    def add(command):
        if command not in seen:
            seen.add(command)
            out.append(command)

    for command in REVIEW_REPROS:
        add(command)
    for shape, (s, r), n, wrapper in itertools.product(_SHAPES, _S_R, _N, _WRAPPERS):
        command = shape.format(S=s, R=r, N=n)
        if wrapper != "{c}" and "'" in command:
            continue
        add(wrapper.format(c=command))
    return out
