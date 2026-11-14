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


# House-style optional offers: "Optional: I'll <default>; say if you'd rather <other>." (live incident 11-14:
# the first sentence minted a For-you approval). The default is stated, so neither path may mint a step.
OPTIONAL_OFFERS = (
    "Optional: I'll let the throttle go ahead as a fix; say if you'd rather not have it.",
    "Optional: I'll keep the old cap; say if you'd like a higher one.",
    "I'll let the sweep run; tell me if you'd rather stop it.",
)


def _offer_reply(sentence):
    return f"Patched the limiter and reran the checks.\n\n{sentence}\n\nNothing else changed."


def test_optional_offer_whole_sentence_span_not_minted():
    for sentence in OPTIONAL_OFFERS:
        for smart in (False, True):
            s = sentence.replace("'", "\u2019") if smart else sentence
            step = {'text': 'Approve the change', 'kind': 'approve', 'verbatim_span': s, 'options': []}
            assert core.validate([step], _offer_reply(s), []) == [], s


def test_optional_offer_default_clause_span_not_minted():
    # The model may cite only the clause before the objection invite; the paragraph still states the default.
    for sentence in OPTIONAL_OFFERS:
        span = sentence.split(";")[0]
        step = {'text': 'Approve the change', 'kind': 'approve', 'verbatim_span': span, 'options': []}
        assert core.validate([step], _offer_reply(sentence), []) == [], sentence


def test_optional_offer_regex_path_not_minted():
    for sentence in OPTIONAL_OFFERS:
        for smart in (False, True):
            s = sentence.replace("'", "\u2019") if smart else sentence
            assert core.regex_steps(_offer_reply(s)) == [], s


def test_silent_default_pattern_matches_offers():
    for sentence in OPTIONAL_OFFERS:
        assert core._SILENT_DEFAULT.search(core._plain(sentence)), sentence


def test_you_d_contraction_treated_like_you_would():
    for s in ("Tell me if you'd rather stop it.", "Tell me if you\u2019d rather stop it.",
              "Tell me if you would rather stop it."):
        assert core._NOT_ASK.search(s), s


def test_blocking_asks_still_mint_on_both_paths():
    # (sentence, LLM span, regex fallback covers it: the fallback only knows "should I"-style asks)
    cases = (
        ("Should I add it? Reply yes or no.", "Should I add it?", True),
        ("**Decision for you:** Should the tool also go on the laptop and the mini?",
         "**Decision for you:** Should the tool also go on the laptop and the mini?", False),
        ("Optional: should I keep the cap? Reply yes or no.", "Optional: should I keep the cap?", True),
    )
    for sentence, span, regex_covers in cases:
        reply = _offer_reply(sentence)
        step = {'text': 'Answer the question', 'kind': 'approve', 'verbatim_span': span, 'options': []}
        assert len(core.validate([step], reply, [])) == 1, sentence
        if regex_covers:
            assert len(core.regex_steps(reply)) >= 1, sentence


def test_optional_offer_with_explicit_question_still_mints():
    sentence = "Optional: I'll let the throttle go ahead; should I also cap retries? Reply yes or no."
    step = {'text': 'Cap retries', 'kind': 'approve', 'verbatim_span': "should I also cap retries?", 'options': []}
    assert len(core.validate([step], _offer_reply(sentence), [])) == 1
