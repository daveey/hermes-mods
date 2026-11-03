"""Render the skills index exactly as Hermes core renders it, optionally with description overrides.

Nothing here re-implements the index: a child process running the core checkout's own
``agent.prompt_builder.build_skills_system_prompt()`` produces the text. Variants are made by
copying every skill root's SKILL.md/DESCRIPTION.md into an overlay HERMES_HOME (with chosen
skills' frontmatter rewritten) and rendering again with HERMES_HOME pointed at the overlay.
``render(..., overrides={})`` must equal ``render_live()`` byte for byte (tests assert it).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

DEFAULT_CORE = Path.home() / ".hermes" / "hermes-agent"
DEFAULT_HOME = Path.home() / ".hermes"
# Env flags read only by the pre-landing prototype core worktree (see README). Landed core renders `triggers:`
# by default (config `skills.index_triggers`, default true) and ignores these.
FLAG_ENV = ("HERMES_SKILL_INDEX_DESC_LIMIT", "HERMES_SKILL_INDEX_TRIGGERS")

_CHILD_ROOTS = r"""
import json
from agent.skill_utils import get_skill_search_roots, get_disabled_skill_names
roots = [(t, str(d)) for t, d in get_skill_search_roots(include_project=False)]
print(json.dumps({"roots": roots}))
"""

_CHILD_RENDER = r"""
import json
from agent.prompt_builder import build_skills_system_prompt
print(json.dumps({"text": build_skills_system_prompt()}))
"""


def core_python(core: Path) -> str:
    # A hermes-core-wt worktree has no venv of its own: run it on the installed core's interpreter
    # (PYTHONPATH=<core> makes the worktree's modules win).
    for base in (core, DEFAULT_CORE):
        for cand in (base / ".venv" / "bin" / "python", base / "venv" / "bin" / "python"):
            if cand.is_file():
                return str(cand)
    return sys.executable


def _child_env(core: Path, home: Path, flags: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
    env.update(HOME=str(Path.home()), HERMES_HOME=str(home), PYTHONPATH=str(core),
               HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONDONTWRITEBYTECODE="1")
    for k, v in (flags or {}).items():
        if k not in FLAG_ENV:
            raise ValueError(f"unknown index flag {k}")
        env[k] = str(v)
    return env


def _run_child(core: Path, home: Path, code: str, flags: dict | None = None) -> dict:
    # cwd = the home so no project skills dir (cwd-trusted) leaks into the index.
    proc = subprocess.run([core_python(core), "-c", code], cwd=str(home), env=_child_env(core, home, flags),
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"core child failed ({proc.returncode}): {proc.stderr[-2000:]}")
    last = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if not last:
        raise RuntimeError(f"core child printed no JSON: {proc.stdout[-500:]} {proc.stderr[-1500:]}")
    return json.loads(last[-1])


def skill_roots(core: Path = DEFAULT_CORE, home: Path = DEFAULT_HOME) -> list[tuple[int, Path]]:
    """(tier, dir) for every skill root, in core's own precedence order."""
    return [(int(t), Path(d)) for t, d in _run_child(core, home, _CHILD_ROOTS)["roots"]]


def render_live(core: Path = DEFAULT_CORE, home: Path = DEFAULT_HOME, flags: dict | None = None) -> str:
    return _run_child(core, home, _CHILD_RENDER, flags)["text"]


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """(yaml_text, rest) using core's own fence rule (``---`` then the first ``\\n---\\s*\\n``)."""
    import re
    text = text.removeprefix("\ufeff")
    if not text.startswith("---"):
        return None
    m = re.search(r"\n---\s*\n", text[3:])
    if not m:
        return None
    return text[3:m.start() + 3], text[m.start() + 3:]


def rewrite_frontmatter(text: str, description: str | None = None, triggers: dict | None = None) -> str:
    parts = split_frontmatter(text)
    if parts is None:
        raise ValueError("SKILL.md has no frontmatter")
    fm_text, rest = parts
    fm = yaml.safe_load(fm_text) or {}
    if description is not None:
        fm["description"] = description
    if triggers is not None:
        fm["triggers"] = triggers
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=100000)
    return "---\n" + dumped + rest


def iter_skill_files(root: Path):
    """SKILL.md / DESCRIPTION.md under *root*, skipping hidden dirs and skills' support dirs (as core does)."""
    for dirpath, dirs, files in os.walk(root, followlinks=True):
        has_skill = "SKILL.md" in files
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")
                   and not (has_skill and d in ("references", "templates", "scripts", "assets"))]
        for f in ("SKILL.md", "DESCRIPTION.md"):
            if f in files:
                yield Path(dirpath) / f


def skill_name_of(skill_md: Path) -> str:
    try:
        parts = split_frontmatter(skill_md.read_text(encoding="utf-8-sig"))
        fm = yaml.safe_load(parts[0]) if parts else {}
        return str((fm or {}).get("name") or skill_md.parent.name)
    except Exception:
        return skill_md.parent.name


def skill_paths(core: Path = DEFAULT_CORE, home: Path = DEFAULT_HOME) -> dict[str, Path]:
    """name -> SKILL.md path, first root wins (core's precedence)."""
    out: dict[str, Path] = {}
    for _, root in skill_roots(core, home):
        if root.is_dir():
            for f in iter_skill_files(root):
                if f.name == "SKILL.md":
                    out.setdefault(skill_name_of(f), f)
    return out


def build_overlay(home: Path, roots: list[tuple[int, Path]], overrides: dict, out: Path) -> Path:
    """Copy every root's index files into *out*/home, rewriting overridden skills. Returns the overlay home."""
    ohome = out / "home"
    (ohome / "skills").mkdir(parents=True, exist_ok=True)
    local = home / "skills"
    ext_map: list[str] = []
    seen: set[str] = set()
    for i, (tier, root) in enumerate(roots):
        if not root.is_dir():
            continue
        dest_root = ohome / "skills" if root.resolve() == local.resolve() else out / f"root{i}"
        if dest_root != ohome / "skills":
            ext_map.append(str(dest_root))
        for f in iter_skill_files(root):
            dest = dest_root / f.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            text = f.read_text(encoding="utf-8-sig") if f.name == "SKILL.md" else None
            name = skill_name_of(f) if text is not None else None
            if name is not None and name in overrides and name not in seen:
                seen.add(name)
                ov = overrides[name]
                dest.write_text(rewrite_frontmatter(text, ov.get("description"), ov.get("triggers")), encoding="utf-8")
            else:
                shutil.copyfile(f, dest)
    cfg_path = home / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.is_file() else {}
    cfg = cfg or {}
    skills_cfg = dict(cfg.get("skills") or {})
    skills_cfg["external_dirs"] = ext_map
    skills_cfg.pop("create_dir", None)  # the create_dir root (if any) is already copied as an external root
    # The whole config is kept (toolsets decide e.g. the ``environments: [kanban]`` gate); only the skill roots
    # change. The overlay has no plugins dir, so plugin settings in it are inert.
    cfg["skills"] = skills_cfg
    (ohome / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    missing = sorted(set(overrides) - seen)
    if missing:
        raise KeyError(f"overrides for skills not found in any root: {missing}")
    return ohome


def render(overrides: dict | None = None, core: Path = DEFAULT_CORE, home: Path = DEFAULT_HOME,
           flags: dict | None = None, keep: Path | None = None) -> str:
    """Index text with *overrides* ({name: {description?, triggers?}}) applied, rendered by *core*."""
    roots = skill_roots(DEFAULT_CORE if core is None else core, home)
    tmp = Path(tempfile.mkdtemp(prefix="ste-overlay-", dir=keep))
    try:
        ohome = build_overlay(home, roots, overrides or {}, tmp)
        return _run_child(core, ohome, _CHILD_RENDER, flags)["text"]
    finally:
        if keep is None:
            shutil.rmtree(tmp, ignore_errors=True)


def index_rows(text: str) -> dict[str, str]:
    """{name: description} parsed from a rendered index (for reports only, never for prompting)."""
    rows = {}
    inside = False
    for ln in text.splitlines():
        if ln.strip() == "<available_skills>":
            inside = True
            continue
        if ln.strip() == "</available_skills>":
            break
        s = ln.strip()
        if inside and s.startswith("- "):
            name, _, desc = s[2:].partition(": ")
            rows[name.strip()] = desc.strip()
    return rows


if __name__ == "__main__":
    print(render_live())
