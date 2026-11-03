"""Index rendering goes through core's own build_skills_system_prompt (no re-implementation)."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import render_index as ri  # noqa: E402

CORE = ri.DEFAULT_CORE
needs_core = pytest.mark.skipif(not (CORE / "agent" / "prompt_builder.py").is_file(), reason="no core checkout")


def _skill(root: Path, cat: str, name: str, desc: str, body: str = "# Body\n\ntext\n") -> Path:
    d = root / cat / name
    d.mkdir(parents=True)
    p = d / "SKILL.md"
    p.write_text(f"---\nname: {name}\ndescription: \"{desc}\"\nversion: 1.0.0\n---\n\n{body}")
    return p


@pytest.fixture
def fake_home(tmp_path):
    home = tmp_path / "home"
    (home / "skills").mkdir(parents=True)
    ext = tmp_path / "ext"
    _skill(home / "skills", "devops", "alpha-skill", "Use when alpha things break. Fix them.")
    _skill(ext, "devops", "beta-skill", "Use when beta needs a deploy.")
    _skill(ext, "media", "gamma-skill", "Use when making gamma videos.")
    (home / "config.yaml").write_text(textwrap.dedent(f"""\
        skills:
          external_dirs:
            - {ext}
        """))
    return home


def test_child_calls_core_builder_not_a_copy():
    # The rendering child imports and calls core; the harness never formats rows itself.
    assert "from agent.prompt_builder import build_skills_system_prompt" in ri._CHILD_RENDER
    assert "get_skill_search_roots" in ri._CHILD_ROOTS


@needs_core
def test_core_renders_fake_home(fake_home):
    text = ri.render_live(CORE, fake_home)
    rows = ri.index_rows(text)
    assert rows == {"alpha-skill": "Use when alpha things break. Fix them.",
                    "beta-skill": "Use when beta needs a deploy.",
                    "gamma-skill": "Use when making gamma videos."}
    assert "<available_skills>" in text and "skill_view(name)" in text


@needs_core
def test_overlay_without_overrides_is_byte_identical(fake_home):
    assert ri.render({}, CORE, fake_home) == ri.render_live(CORE, fake_home)


@needs_core
def test_overlay_changes_only_the_overridden_row(fake_home):
    base = ri.index_rows(ri.render_live(CORE, fake_home))
    new = ri.index_rows(ri.render({"beta-skill": {"description": "Use when shipping beta. Not for alpha."}},
                                  CORE, fake_home))
    assert new["beta-skill"] == "Use when shipping beta. Not for alpha."
    assert {k: v for k, v in new.items() if k != "beta-skill"} == {k: v for k, v in base.items() if k != "beta-skill"}


@needs_core
def test_unknown_override_is_an_error(fake_home):
    with pytest.raises(KeyError):
        ri.render({"no-such-skill": {"description": "x"}}, CORE, fake_home)


@needs_core
def test_live_overlay_matches_live_index():
    home = ri.DEFAULT_HOME
    if not (home / "config.yaml").is_file():
        pytest.skip("no live Hermes home")
    assert ri.render({}, CORE, home) == ri.render_live(CORE, home)


def test_rewrite_frontmatter_keeps_body_and_other_keys():
    src = "---\nname: x\ndescription: old\nmetadata:\n  hermes:\n    tags: [a]\n---\n\n# Title\n\n---\nnot fm\n"
    out = ri.rewrite_frontmatter(src, "new desc", {"asks": ["a"], "not_for": "b"})
    fm, rest = ri.split_frontmatter(out)
    import yaml
    data = yaml.safe_load(fm)
    assert data["description"] == "new desc" and data["metadata"] == {"hermes": {"tags": ["a"]}}
    assert data["triggers"] == {"asks": ["a"], "not_for": "b"}
    assert rest.endswith("# Title\n\n---\nnot fm\n")


def test_index_rows_parses_nested_and_top_level_rows():
    text = ("## Skills\n<available_skills>\n  devops:\n    - a: Use when a.\n    - b: Use when b: colon.\n"
            "  - c: top level\n</available_skills>\n")
    assert ri.index_rows(text) == {"a": "Use when a.", "b": "Use when b: colon.", "c": "top level"}
