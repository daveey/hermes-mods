"""One corpus for runtime-written user turns across independently loaded plugins.

Local predicates deliberately remain plugin-local. This contract checks the real
consumer entry points, not a test-only implementation of the Unicode rule.
"""
import ast
import importlib.util
from pathlib import Path
import re
import sys
import unittest
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
GLYPHS = '⏳⏱🔄👀🛑⛔ℹ✔✖⚠⏸'
# Producer first-line shapes, with identifiers anonymized for the public repo.
NOTICE_LINES = [
    '⏳ [default] @default Kanban t_abcdef: ready 30 min with no worker claiming it.',
    '⏱ [default] @default Kanban t_abcdef timed out',
    '🔄 [default] @default Kanban t_abcdef status changed',
    '👀 [default] @default Kanban t_abcdef in review',
    '🛑 [default] @default Kanban t_abcdef needs triage',
    '⛔ [default] @default Kanban t_abcdef gave up',
    'ℹ [default] @default Kanban t_abcdef update',
    '✔ [default] @default Kanban t_abcdef done',
    '✖ [default] @default Kanban t_abcdef crashed',
    '⚠ [default] @default Kanban t_abcdef failed',
    '⏸ [default] @default Kanban t_abcdef blocked',
    '[kanban] Task t_abcdef is ready with no worker claiming it.',
    '[IMPORTANT: 2 Kanban task updates arrived while you were working.]',
    '[System note: background work completed.]',
    '[chat-closeout] Close out the completed work.',
    '[owner-wake] Continue the owned work.',
]
HUMAN_LINES = ['👍 yes', '✅ done', '⚠ careful, but go', 'yes, restart it',
               'I saw ⏳ [default] in the log', 'Canonical user decision. Proceed.']


def load(root, relative):
    name = '_runtime_contract_' + str(root).replace('/', '_').replace('.', '_') + relative.replace('/', '_').replace('.', '_')
    spec = importlib.util.spec_from_file_location(name, root / 'plugins' / relative)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def consumers(root=ROOT):
    """Entry points also usable against historical trees for RED/replay proof."""
    ce = load(root, 'coach/compute_envelope.py')
    rc = load(root, 'coach/reply_check.py')
    rs = load(root, 'coach/refusal_streak.py')
    rr = load(root, 'coach/relay_repeat.py')
    sh = load(root, 'coach/self_heal.py')
    so = load(root, 'skill-outcomes/outcomes.py')
    cc = load(root, 'chat-closeout/core.py')
    kh = load(root, 'kanban-honest-done/acceptance.py')
    ft = load(root, 'follow-through/core.py')
    us = load(root, 'user-steps/core.py')
    ui = load(root, 'user-steps/__init__.py')
    ag = load(root, 'ask-gate/core.py')
    # Resolve coach from the selected tree, never a deployed sibling.
    rr._rc = lambda: rc
    so._coach_synth = [getattr(sh, 'synthetic', sh.SYNTHETIC_RE.search),
                       getattr(rc, 'notice_turn', rc.NOTICE_RE.search)]
    # Historical outcomes expects regex objects; do not replace its cache there.
    if not hasattr(so, 'runtime_notice'):
        so._coach_synth = [sh.SYNTHETIC_RE, rc.NOTICE_RE]
    af = load(root, 'coach/answer_first.py') if (root / 'plugins/coach/answer_first.py').exists() else None
    return {
        **({'coach.answer_first': lambda t, k=None: bool(af.runtime_notice(t) if hasattr(af, 'runtime_notice') else af._NOTICE.match(t))} if af else {}),
        'coach.compute_envelope': lambda t, k=None: ce.typed_text(t) is None,
        'coach.reply_check': lambda t, k=None: bool(rc.synthetic_user_turn(t) if hasattr(rc, 'synthetic_user_turn') else rc.SYNTHETIC_USER_RE.match(t)),
        'coach.notice_turn': lambda t, k=None: rc.notice_turn(t),
        'coach.refusal_streak': lambda t, k=None: rs.automated(t),
        'coach.relay_repeat': lambda t, k=None: rr._automated(t),
        'coach.self_heal': lambda t, k=None: bool(sh.synthetic(t) if hasattr(sh, 'synthetic') else sh.SYNTHETIC_RE.match(t)),
        'skill-outcomes.local': lambda t, k=None: bool(so.runtime_notice(t) if hasattr(so, 'runtime_notice') else so.SYNTHETIC_RE.match(t)),
        'skill-outcomes': lambda t, k=None: not so.is_real_user_message(t, k),
        'chat-closeout': lambda t, k=None: bool(cc.runtime_notice(t) if hasattr(cc, 'runtime_notice') else cc.NOTICE_RE.match(t)),
        'kanban-honest-done': lambda t, k=None: kh.typed_text(t, k) is None,
        'follow-through': lambda t, k=None: bool(ft.runtime_notice(t) if hasattr(ft, 'runtime_notice') else False),
        'user-steps.text': lambda t, k=None: us._runtime_message(t),
        'user-steps.row': lambda t, k=None: us._runtime_message(ui._turn_message(t, [{'role': 'user', 'content': t, 'display_kind': k}])),
        'ask-gate': lambda t, k=None: ag.runtime_notice(t),
    }


# Legacy unbracket receipts are intentionally retained only in corpus-covered
# predicates. No other function/module may grow a private closed glyph class.
APPROVED = {
    ('plugins/coach/answer_first.py', 'runtime_notice'),
    ('plugins/coach/reply_check.py', 'synthetic_user_turn'),
    ('plugins/coach/self_heal.py', 'synthetic'),
    ('plugins/coach/relay_repeat.py', '_automated'),
    ('plugins/skill-outcomes/outcomes.py', 'runtime_notice'),
    ('plugins/kanban-honest-done/acceptance.py', 'runtime_notice'),
}
WATCHED = frozenset('✔⏸⏳✖⚠')


def private_glyph_lists(source, relative):
    """Parse string values (including raw/escaped Unicode), track function scope."""
    tree = ast.parse(source)
    failures = []

    class Scan(ast.NodeVisitor):
        function = None

        def visit_FunctionDef(self, node):
            previous = self.function
            self.function = node.name
            self.generic_visit(node)
            self.function = previous

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Constant(self, node):
            if not isinstance(node.value, str) or (relative, self.function) in APPROVED:
                return
            # Raw regex strings retain backslashes; Python string literals may
            # already have decoded them. Decode only Unicode escapes, not \b.
            value = re.sub(r'\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})',
                           lambda m: chr(int(m.group(1) or m.group(2), 16)), node.value)
            def named_escape(match):
                try:
                    return unicodedata.lookup(match.group(1))
                except KeyError:
                    return match.group()  # Not a valid regex Unicode name.
            value = re.sub(r'\\N\{([^}]+)\}', named_escape, value)
            for match in re.finditer(r'(?<!\\)\[(?:\\.|[^\]\\])*\]', value):
                if len(WATCHED.intersection(match.group())) >= 2:
                    failures.append((relative, node.lineno, self.function, match.group()))
    Scan().visit(tree)
    return failures


class RuntimeNoticeContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.predicates = consumers()

    def test_shared_notice_corpus(self):
        self.assertEqual(set(GLYPHS), {line[0] for line in NOTICE_LINES if not line.startswith('[')})
        for name, predicate in self.predicates.items():
            for text in NOTICE_LINES:
                with self.subTest(consumer=name, line=text):
                    self.assertTrue(predicate(text), f'{name}: {text}')

    def test_future_pictographs_and_variation_selector(self):
        for name, predicate in self.predicates.items():
            for glyph in GLYPHS + '☃🧪🜁':
                for suffix in (' [new] notice', '\ufe0f [new] notice', '\t[new] notice'):
                    with self.subTest(consumer=name, line=glyph + suffix):
                        self.assertTrue(predicate(glyph + suffix))

    def test_human_replies(self):
        for name, predicate in self.predicates.items():
            # Outcomes intentionally excludes button decisions from skill feedback;
            # this is not the ask/step decision-authorization consumer.
            for text in HUMAN_LINES:
                if name.startswith('skill-outcomes') and text.startswith('Canonical user decision.'):
                    continue
                with self.subTest(consumer=name, line=text):
                    self.assertFalse(predicate(text), f'{name}: {text}')

    def test_user_steps_kinds_and_canonical_exception(self):
        predicate = self.predicates['user-steps.row']
        for kind in ('internal_notification', 'process_complete', 'async_delegation_complete',
                     'loop_tick', 'kanban_notice', 'auto_continue'):
            with self.subTest(kind=kind):
                self.assertTrue(predicate('unwrapped runtime text', kind))
        self.assertFalse(predicate('Canonical user decision. Proceed.', 'internal_notification'))
        self.assertFalse(predicate('yes', 'steer'))
        self.assertFalse(predicate('[OUT-OF-BAND USER MESSAGE]\nyes\n[/OUT-OF-BAND USER MESSAGE]', 'steer'))

    def test_no_private_glyph_lists(self):
        failures = []
        for path in sorted((ROOT / 'plugins').rglob('*.py')):
            if 'tests' in path.parts or path.name.startswith(('test_', 'tests_')):
                continue
            failures.extend(private_glyph_lists(path.read_text(), path.relative_to(ROOT).as_posix()))
        self.assertEqual([], failures)

    def test_lint_named_unicode_escapes(self):
        pattern = r'[\N{HEAVY CHECK MARK}\N{HOURGLASS WITH FLOWING SAND}]'
        self.assertIsNotNone(re.fullmatch(pattern, '✔'))
        self.assertIsNotNone(re.fullmatch(pattern, '⏳'))
        source = 'PRIVATE = ' + repr(pattern)
        self.assertTrue(private_glyph_lists(source, 'plugins/new/core.py'))

    def test_lint_literal_and_escaped_mutants(self):
        for source in ('PRIVATE = r"[✔⏳]"', 'PRIVATE = r"[\\u2714\\u23f3]"',
                       'def new_private():\n    return r"[✔⏸]"'):
            self.assertTrue(private_glyph_lists(source, 'plugins/new/core.py'))
        self.assertFalse(private_glyph_lists('PATTERN = r"[a-z]"', 'plugins/new/core.py'))


if __name__ == '__main__':
    unittest.main()
