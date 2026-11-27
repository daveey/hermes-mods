"""Coach code must come only from the reviewed sibling tree, never HERMES_HOME."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def source(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "isolated_outcomes", Path(__file__).resolve().parents[1] / "outcomes.py")
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    reviewed = tmp_path / "reviewed" / "skill-outcomes"
    reviewed.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(core, "HERE", reviewed)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    return core, reviewed.parent / "coach", home


@pytest.mark.parametrize("module", ["lessons", "self_heal", "reply_check"])
@pytest.mark.parametrize("reviewed_state", ["missing", "broken"])
def test_no_environment_coach_execution(source, module, reviewed_state):
    core, reviewed, home = source
    marker = home / "executed"
    other = home / "plugins" / "coach"
    other.mkdir(parents=True)
    (other / f"{module}.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('unreviewed')\n")
    if reviewed_state == "broken":
        reviewed.mkdir()
        (reviewed / f"{module}.py").write_text("raise RuntimeError('unavailable')\n")
    if module == "lessons":
        assert core.keyword_correction("no, that is wrong") is True
        assert core.keyword_correction("thank you") is False
    else:
        core._coach_synthetic_res()
    assert not marker.exists(), "environment-supplied coach module executed"


def test_reviewed_keyword_module_is_used(source):
    core, reviewed, _ = source
    reviewed.mkdir()
    (reviewed / "lessons.py").write_text("def keyword_hit(text): return text == 'reviewed'\n")
    assert core.keyword_correction("reviewed") is True
    assert core.keyword_correction("no, wrong") is False


@pytest.mark.parametrize("module,attr,predicate", [
    ("self_heal", "SYNTHETIC_RE", "synthetic"),
    ("reply_check", "NOTICE_RE", "notice_turn"),
])
@pytest.mark.parametrize("category_aware", [True, False])
def test_reviewed_notice_module_is_used(source, module, attr, predicate, category_aware):
    core, reviewed, _ = source
    reviewed.mkdir()
    code = f"import re\n{attr} = re.compile('reviewed-notice')\n"
    if category_aware:
        code += f"def {predicate}(text): return text == 'category-notice'\n"
    (reviewed / f"{module}.py").write_text(code)
    text = "category-notice" if category_aware else "reviewed-notice"
    assert core.is_real_user_message(text) is False
    assert core.is_real_user_message("ordinary user message") is True
