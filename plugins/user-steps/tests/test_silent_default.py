"""An optional offer with a silent default must not mint a For-you request (anonymized live incident).

The model picked the "Decision for you" sentence as the span; the "unless you want..." clause sat two
sentences later in the same paragraph, so the span-only _NOT_ASK filter never saw it and the chat went red.
"""
import importlib.util
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('silent_default_core', ROOT / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

SPAN = "**Decision for you:** Decide whether the tool should also be added to the laptop and the mini."
REPLY = (
    "Installed the tool on the hub and verified it runs.\n\n"
    + SPAN + " It is only needed on the hub today. "
    "I'll leave it there unless you want it on the other machines too.\n\n"
    "Nothing else changed."
)
STEP = {'text': 'Decide whether the tool should also be added to the laptop and the mini',
        'kind': 'input', 'verbatim_span': SPAN, 'options': []}


def test_historical_paragraph_is_suppressed_with_telemetry(caplog):
    dropped = []
    with caplog.at_level(logging.INFO, logger='user-steps'):
        assert core.validate([dict(STEP)], REPLY, [], dropped) == []
    assert any('silent_default_suppressed' in r.getMessage() for r in caplog.records)
    assert dropped and dropped[0].startswith('silent_default')


def test_other_default_wordings_suppressed():
    for tail in ("If you say nothing, it stays hub-only.", "Otherwise I'll keep it as is.",
                 "The default is hub-only."):
        reply = f"{SPAN} {tail}"
        assert core.validate([dict(STEP)], reply, []) == [], tail


def test_direct_question_still_mints():
    span = "Should I add it to the Macs? Reply yes or no."
    reply = f"Installed on the hub.\n\n{span} I'll leave it hub-only unless you say yes."
    step = {'text': 'Add it to the Macs', 'kind': 'approve', 'verbatim_span': "Should I add it to the Macs?", 'options': []}
    assert len(core.validate([step], reply, [])) == 1


def test_required_choice_without_default_still_mints():
    span = "**Decision for you:** Should the tool also go on the laptop and the mini?"
    reply = f"Installed on the hub.\n\n{span} It is only needed on the hub today."
    step = {'text': 'Choose where the tool goes', 'kind': 'input', 'verbatim_span': span, 'options': []}
    assert len(core.validate([step], reply, [])) == 1
