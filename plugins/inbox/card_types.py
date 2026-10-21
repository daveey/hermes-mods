"""Inbox card types: a YAML file declaring a card's fields and its action buttons.
(Named card_types.py, not types.py: a sibling named like the stdlib module shadows it.)

    name: x-post
    label: X post
    fields:
      id:   {required: true}
      text: {required: true, preview: true, max: 280}
    actions:
      - {id: approve, label: Approve, icon: check, style: primary, kind: resolve, row: true}
      - {id: edit, label: Edit, kind: resolve, input: {field: text, multiline: true, max: 280}}
      - {id: rewrite, label: Rewrite…, kind: prompt, input: {placeholder: What to change},
         prompt: "Rewrite {{id}}: {{input}}"}

Action kinds:
  prompt   render ``prompt`` ({{field}}, {{input}}, {{key}}, {{title}}, {{accept_prompt}}; one plain
           substitution pass, no expressions) and run it in the card's chat (= the old "Do it").
  resolve  record outcome=<action id> (+ the input value), close the card, no agent turn.
  task     resolve + queue a Kanban card whose body is the rendered ``prompt`` (options: assignee
           (default ``default``), board, title template, skills). Atomic: if the card can't be
           created the action fails and the inbox card stays open.
Archive is implicit on every type (outcome ``archived``) and may not be redeclared.

Sources, first wins: ``<home>/inbox/types/<name>.yaml`` (ad-hoc, written by ``define``), then
``<skill>/inbox-types/<name>.yaml`` in every skills dir, then the built-in ``default``.
The resolved type is snapshotted onto each card at post time (store.py), so open cards render
without this registry and survive later edits to the type.
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional

# \Z, not $: "$" also matches before a trailing newline ("foo\n" would pass).
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\Z")
ACTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}\Z")
FIELD_RE = re.compile(r"^[a-z_][a-z0-9_]{0,31}\Z")
ICON_RE = re.compile(r"^[a-z0-9-]{1,40}\Z")
KINDS = ("prompt", "resolve", "task")
TASK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
CORE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\Z")
MAX_SKILLS = 8
MAX_TITLE = 200
STYLES = ("primary", "secondary", "danger")
RESERVED_ACTIONS = ("archive",)
RESERVED_FIELDS = ("input", "key", "title", "accept_prompt")
MAX_ACTIONS = 8
MAX_FIELDS = 16
MAX_FIELD_VALUE = 20_000
MAX_PROMPT = 4_000
MAX_SPEC_BYTES = 64_000
MAX_SPEC_NODES = 2_000
MAX_FIELDS_BYTES = 64_000
MAX_RENDERED = 24_000
RESERVED_TYPES = ("default",)

DEFAULT = {
    "name": "default",
    "label": "Proposal",
    "actions": [{"id": "do_it", "label": "Do it", "icon": "play", "style": "primary", "kind": "prompt",
                 "prompt": "{{accept_prompt}}"}],
}

_VAR = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")


class TypeError_(ValueError):
    """A type spec or a post that breaks the type's rules."""


class UnknownType(TypeError_):
    """No source defines this type name."""


def render(template: str, ctx: dict[str, Any]) -> str:
    """Replace ``{{name}}`` with ``ctx[name]`` in ONE pass (values are never re-scanned, so a field
    holding ``{{x}}`` stays literal). Unknown names render empty."""
    def sub(m: re.Match) -> str:
        v = ctx.get(m.group(1))
        return "" if v is None else str(v)
    return _VAR.sub(sub, str(template or ""))


def template_vars(template: str) -> set[str]:
    return set(_VAR.findall(str(template or "")))


# ── parsing ────────────────────────────────────────────────────────────────
_PYYAML_LOADER = None


def _pyyaml_loader(yaml):
    """SafeLoader with YAML 1.2 booleans (only true/false), so `id: no` / `on:` stay strings exactly
    as they do under ruamel (YAML 1.2); PyYAML's 1.1 yes/no/on/off booleans broke such specs."""
    global _PYYAML_LOADER
    if _PYYAML_LOADER is None:
        class Loader(yaml.SafeLoader):
            pass
        Loader.yaml_implicit_resolvers = {
            k: [r for r in v if r[0] != "tag:yaml.org,2002:bool"]
            for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
        Loader.add_implicit_resolver("tag:yaml.org,2002:bool",
                                     re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))
        _PYYAML_LOADER = Loader
    return _PYYAML_LOADER


def _check_tree(obj: Any) -> None:
    """Reject YAML aliases (a container reached twice: the 'billion laughs' bomb expands only when
    the tree is later walked or str()'d) and oversized trees."""
    seen: set[int] = set()
    stack, count = [obj], 0
    while stack:
        x = stack.pop()
        count += 1
        if count > MAX_SPEC_NODES:
            raise TypeError_(f"type spec has more than {MAX_SPEC_NODES} nodes")
        if isinstance(x, (dict, list)):
            if id(x) in seen:
                raise TypeError_("type spec uses YAML anchors/aliases, which are not allowed")
            seen.add(id(x))
            stack.extend(list(x.keys()) + list(x.values()) if isinstance(x, dict) else x)


def parse(text: str) -> Any:
    """Safe-load YAML (PyYAML with YAML 1.2 booleans, else ruamel.yaml, which Hermes ships), else JSON.
    Parser errors, aliases and oversized specs raise TypeError_ (a ValueError)."""
    if not isinstance(text, str):
        raise TypeError_("type spec text must be a string")
    if len(text.encode("utf-8", "replace")) > MAX_SPEC_BYTES:
        raise TypeError_(f"type spec is over {MAX_SPEC_BYTES} bytes")
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            out = yaml.load(text, Loader=_pyyaml_loader(yaml))  # noqa: S506 - a SafeLoader subclass
        except yaml.YAMLError as exc:
            raise TypeError_(f"bad YAML: {str(exc)[:300]}") from None
        _check_tree(out)
        return out
    try:
        from ruamel.yaml import YAML  # type: ignore
        from ruamel.yaml.error import YAMLError  # type: ignore
    except ImportError:
        YAML = None
    if YAML is not None:
        try:
            out = YAML(typ="safe", pure=True).load(text)
        except YAMLError as exc:
            raise TypeError_(f"bad YAML: {str(exc)[:300]}") from None
        _check_tree(out)
        return out
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("no YAML parser available (install PyYAML or ruamel.yaml), and the spec is not "
                         f"valid JSON: {exc}") from exc


def _r(v: Any) -> str:
    """Short repr for error messages (never str() an untrusted tree whole)."""
    return repr(v)[:80] if isinstance(v, (str, int, float, bool, type(None))) else f"<{type(v).__name__}>"


# ── validation ─────────────────────────────────────────────────────────────
def _bool(v: Any, what: str) -> bool:
    if v is None:
        return False
    if not isinstance(v, bool):
        raise TypeError_(f"{what} must be true/false")
    return v


def _pos_int(v: Any, what: str) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise TypeError_(f"{what} must be a positive integer")
    return min(v, MAX_FIELD_VALUE)


def _label(v: Any, default: str, what: str) -> str:
    if v is None:
        return default
    if not isinstance(v, str) or not v.strip():
        raise TypeError_(f"{what} must be a non-empty string")
    return " ".join(v.split())[:60]


def validate(spec: Any, name: Optional[str] = None) -> dict[str, Any]:
    """Normalize a type spec or raise TypeError_ (a ValueError) naming the problem."""
    if not isinstance(spec, dict):
        raise TypeError_("type spec must be a mapping")
    unknown = set(spec) - {"name", "label", "description", "fields", "actions"}
    if unknown:
        raise TypeError_(f"unknown type keys: {[_r(k) for k in unknown]}")
    tname = spec.get("name", name)
    if not isinstance(tname, str) or not NAME_RE.match(tname):
        raise TypeError_(f"type name {_r(tname)} must match {NAME_RE.pattern}")
    if name is not None and tname != name:
        raise TypeError_(f"type name {_r(tname)} does not match {_r(name)}")
    out: dict[str, Any] = {"name": tname, "label": _label(spec.get("label"), tname, "label")}
    if spec.get("description") is not None:
        if not isinstance(spec["description"], str):
            raise TypeError_("description must be a string")
        out["description"] = spec["description"][:500]

    fields_in = spec.get("fields")
    fields: Optional[dict[str, dict]] = None
    if fields_in is not None:
        if not isinstance(fields_in, dict):
            raise TypeError_("fields must be a mapping of field name -> options")
        if len(fields_in) > MAX_FIELDS:
            raise TypeError_(f"at most {MAX_FIELDS} fields")
        fields = {}
        for fname, fopt in fields_in.items():
            if not isinstance(fname, str) or not FIELD_RE.match(fname) or fname in RESERVED_FIELDS:
                raise TypeError_(f"bad field name {_r(fname)} (must match {FIELD_RE.pattern}, not "
                                 f"{', '.join(RESERVED_FIELDS)})")
            fopt = {} if fopt is None else fopt
            if not isinstance(fopt, dict):
                raise TypeError_(f"field {fname}: options must be a mapping")
            bad = set(fopt) - {"required", "preview", "max", "label"}
            if bad:
                raise TypeError_(f"field {fname}: unknown options {[_r(k) for k in bad]}")
            f: dict[str, Any] = {"required": _bool(fopt.get("required"), f"field {fname}.required"),
                 "preview": _bool(fopt.get("preview"), f"field {fname}.preview")}
            mx = _pos_int(fopt.get("max"), f"field {fname}.max")
            if mx:
                f["max"] = mx
            if fopt.get("label") is not None:
                f["label"] = _label(fopt["label"], fname, f"field {fname}.label")
            fields[fname] = f
        out["fields"] = fields

    acts = spec.get("actions")
    if not isinstance(acts, list) or not acts:
        raise TypeError_("actions must be a non-empty list")
    if len(acts) > MAX_ACTIONS:
        raise TypeError_(f"at most {MAX_ACTIONS} actions")
    seen: set[str] = set()
    actions = []
    for i, a in enumerate(acts):
        if not isinstance(a, dict):
            raise TypeError_(f"action #{i + 1} must be a mapping")
        aid = a.get("id")
        if not isinstance(aid, str) or not ACTION_ID_RE.match(aid):
            hint = " (quote it: YAML reads bare yes/no/on/off/numbers as non-strings)" if isinstance(aid, (bool, int)) else ""
            raise TypeError_(f"action #{i + 1}: id {_r(aid)} must match {ACTION_ID_RE.pattern}{hint}")
        if aid in RESERVED_ACTIONS:
            raise TypeError_(f"action id {aid!r} is reserved (Archive is always present)")
        if aid in seen:
            raise TypeError_(f"duplicate action id {aid!r}")
        seen.add(aid)
        bad = set(a) - {"id", "label", "kind", "prompt", "input", "confirm", "style", "icon", "row",
                        "assignee", "board", "title", "skills"}
        if bad:
            raise TypeError_(f"action {aid}: unknown options {[_r(k) for k in bad]}")
        kind = a.get("kind")
        if kind not in KINDS:
            raise TypeError_(f"action {aid}: unknown kind {_r(kind)} (supported: {', '.join(KINDS)})")
        act: dict[str, Any] = {"id": aid, "label": _label(a.get("label"), aid.replace("_", " ").title(),
                                                           f"action {aid}.label"), "kind": kind}
        if kind in ("prompt", "task"):
            p = a.get("prompt")
            if not isinstance(p, str) or not p.strip():
                raise TypeError_(f"action {aid}: kind {kind} needs a prompt template")
            act["prompt"] = p.strip()[:MAX_PROMPT]
            unknown_vars = template_vars(p) - set(fields or {}) - set(RESERVED_FIELDS)
            if fields is not None and unknown_vars:
                raise TypeError_(f"action {aid}: prompt uses undeclared fields {sorted(unknown_vars)}")
        elif a.get("prompt") is not None:
            raise TypeError_(f"action {aid}: only kinds prompt and task take a prompt")
        if kind == "task":
            for opt in ("assignee", "board"):
                v = a.get(opt)
                if v is not None:
                    # Kanban lowercases assignees and its profile/board slugs have no dots.
                    if (not isinstance(v, str) or not TASK_NAME_RE.match(v)
                            or not CORE_SLUG_RE.match(v.lower())):
                        raise TypeError_(f"action {aid}.{opt} must match {TASK_NAME_RE.pattern} "
                                         f"(no dots: Kanban profile and board names are [a-z0-9_-])")
                    act[opt] = v
            act.setdefault("assignee", "default")
            t = a.get("title")
            if t is not None:
                if not isinstance(t, str) or not t.strip():
                    raise TypeError_(f"action {aid}.title must be a non-empty template string")
                act["title"] = t.strip()[:MAX_TITLE]
                tv = template_vars(t) - set(fields or {}) - set(RESERVED_FIELDS)
                if fields is not None and tv:
                    raise TypeError_(f"action {aid}: title uses undeclared fields {sorted(tv)}")
            sk = a.get("skills")
            if sk is not None:
                if (not isinstance(sk, list) or len(sk) > MAX_SKILLS
                        or not all(isinstance(x, str) and TASK_NAME_RE.match(x) for x in sk)):
                    raise TypeError_(f"action {aid}.skills must be a list (max {MAX_SKILLS}) of names "
                                     f"matching {TASK_NAME_RE.pattern}")
                act["skills"] = list(dict.fromkeys(sk))
        elif any(a.get(o) is not None for o in ("assignee", "board", "title", "skills")):
            raise TypeError_(f"action {aid}: assignee/board/title/skills are only for kind task")
        inp = a.get("input")
        if inp is not None:
            if inp is True:
                inp = {}
            if not isinstance(inp, dict):
                raise TypeError_(f"action {aid}: input must be a mapping")
            bad = set(inp) - {"field", "multiline", "max", "placeholder", "required"}
            if bad:
                raise TypeError_(f"action {aid}.input: unknown options {[_r(k) for k in bad]}")
            i_out: dict[str, Any] = {"multiline": _bool(inp.get("multiline"), f"action {aid}.input.multiline")}
            fld = inp.get("field")
            if fld is not None:
                if (not isinstance(fld, str) or (fields is not None and fld not in fields) or not FIELD_RE.match(fld)
                        or fld in RESERVED_FIELDS):
                    raise TypeError_(f"action {aid}.input.field {_r(fld)} is not a declared field")
                i_out["field"] = fld
            mx = _pos_int(inp.get("max"), f"action {aid}.input.max")
            if mx is None and fld and fields and fields[fld].get("max"):
                mx = fields[fld]["max"]
            if mx:
                i_out["max"] = mx
            if inp.get("placeholder") is not None:
                if not isinstance(inp["placeholder"], str):
                    raise TypeError_(f"action {aid}.input.placeholder must be a string")
                i_out["placeholder"] = inp["placeholder"][:120]
            i_out["required"] = True if inp.get("required") is None else _bool(inp["required"],
                                                                              f"action {aid}.input.required")
            act["input"] = i_out
        if a.get("confirm") not in (None, False):
            c = a["confirm"]
            if c is True:
                c = f"{act['label']}?"
            if not isinstance(c, str):
                raise TypeError_(f"action {aid}.confirm must be true or a message")
            act["confirm"] = c[:200]
        style = a.get("style")
        if style is not None:
            if style not in STYLES:
                raise TypeError_(f"action {aid}.style must be one of {', '.join(STYLES)}")
            act["style"] = style
        icon = a.get("icon")
        if icon is not None:
            if not isinstance(icon, str) or not ICON_RE.match(icon):
                raise TypeError_(f"action {aid}.icon must be a codicon name")
            act["icon"] = icon
        if _bool(a.get("row"), f"action {aid}.row"):
            if kind not in ("resolve", "task") or "input" in act:
                raise TypeError_(f"action {aid}: row buttons are for resolve/task actions without input")
            act["row"] = True
        actions.append(act)
    out["actions"] = actions
    return out


def check_fields(tdef: dict[str, Any], fields: Any) -> dict[str, str]:
    """Validate a post's field values against the type: required present, max lengths, and (for a
    type that declares fields) no undeclared ones. Returns the normalized {name: str}."""
    if fields is None:
        fields = {}
    if not isinstance(fields, dict):
        raise TypeError_("fields must be a mapping of name -> value")
    declared = tdef.get("fields")
    if len(fields) > MAX_FIELDS:
        raise TypeError_(f"at most {MAX_FIELDS} fields")
    out: dict[str, str] = {}
    for k, v in fields.items():
        if not isinstance(k, str) or not FIELD_RE.match(k) or k in RESERVED_FIELDS:
            raise TypeError_(f"bad field name {_r(k)}")
        if declared is not None and k not in declared:
            raise TypeError_(f"type {tdef['name']} has no field {k!r} (fields: {', '.join(declared)})")
        if isinstance(v, (dict, list)):
            raise TypeError_(f"field {k} must be a string")
        s = "" if v is None else str(v)
        mx = (declared or {}).get(k, {}).get("max") or MAX_FIELD_VALUE
        if len(s) > mx:
            raise TypeError_(f"field {k} is {len(s)} chars, over its max of {mx}")
        out[k] = s
    if sum(len(v.encode("utf-8", "replace")) for v in out.values()) > MAX_FIELDS_BYTES:
        raise TypeError_(f"fields total over {MAX_FIELDS_BYTES} bytes")
    for k, f in (declared or {}).items():
        if f.get("required") and not out.get(k, "").strip():
            raise TypeError_(f"type {tdef['name']} requires field {k!r}")
    return out


def needs_accept_prompt(tdef: dict[str, Any]) -> bool:
    return any(a["kind"] in ("prompt", "task") and "accept_prompt" in template_vars(a.get("prompt", ""))
               for a in tdef["actions"])


# ── sources ────────────────────────────────────────────────────────────────
def _process_home() -> Optional[Path]:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home()).resolve()
    except Exception:
        env = os.environ.get("HERMES_HOME")
        return Path(env).resolve() if env else None


def _default_skill_dirs(home: Path) -> list[Path]:
    """This process's skill dirs (core resolver) when *home* is the process's own home; for another
    profile's home, that profile's skills dir + its config's external_dirs."""
    if _process_home() == Path(home).resolve():
        try:
            from agent.skill_utils import get_all_skills_dirs  # type: ignore
            dirs = [Path(d) for d in get_all_skills_dirs()]
            if dirs:
                return dirs
        except Exception:
            pass
    dirs = [home / "skills"]
    try:
        cfg = parse((home / "config.yaml").read_text())
        ext = ((cfg or {}).get("skills") or {}).get("external_dirs") or []
        for e in [ext] if isinstance(ext, str) else ext:
            p = Path(os.path.expandvars(os.path.expanduser(str(e))))
            dirs.append(p if p.is_absolute() else home / p)
    except Exception:
        pass
    return dirs


class Registry:
    """Resolve type names across the three sources. ``skill_dirs`` is injectable for tests."""

    def __init__(self, home: Path, skill_dirs: Optional[Iterable[Path]] = None):
        self.home = Path(home)
        self.local_dir = self.home / "inbox" / "types"
        self._skill_dirs = list(skill_dirs) if skill_dirs is not None else None

    def skill_dirs(self) -> list[Path]:
        return self._skill_dirs if self._skill_dirs is not None else _default_skill_dirs(self.home)

    def _skill_files(self) -> list[Path]:
        out: list[Path] = []
        for root in self.skill_dirs():
            root = Path(root)
            if not root.is_dir():
                continue
            for pat in ("*/inbox-types/*.yaml", "*/*/inbox-types/*.yaml", "*/*/*/inbox-types/*.yaml",
                        "*/inbox-types/*.yml", "*/*/inbox-types/*.yml"):
                out += sorted(root.glob(pat))
        return out

    def _candidates(self) -> list[tuple[str, str, Path]]:
        """(name, source, path) in precedence order (local first)."""
        out = []
        if self.local_dir.is_dir():
            for p in sorted(self.local_dir.glob("*.yaml")):
                out.append((p.stem, "local", p))
        for p in self._skill_files():
            out.append((p.stem, f"skill:{p.parent.parent.name}", p))
        return out

    def get(self, name: Optional[str]) -> dict[str, Any]:
        """The validated type (with ``source``), or raise TypeError_ for unknown / broken types."""
        name = name or "default"
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise TypeError_(f"bad type name {_r(name)} (must match {NAME_RE.pattern})")
        for cand, source, path in self._candidates():
            if cand != name:
                continue
            spec = validate(parse(path.read_text()), name=name)
            spec["source"] = source
            return spec
        if name == "default":
            return dict(validate(copy.deepcopy(DEFAULT)), source="builtin")
        raise UnknownType(f"unknown inbox card type {name!r}; define it with inbox_types(action='define') "
                         f"or a skill's inbox-types/{name}.yaml")

    def list(self) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for name, source, path in self._candidates():
            if name in seen or not NAME_RE.match(name):
                continue
            try:
                seen[name] = dict(validate(parse(path.read_text()), name=name), source=source)
            except Exception as exc:
                seen[name] = {"name": name, "source": source, "error": str(exc)}
        if "default" not in seen:
            seen["default"] = dict(validate(copy.deepcopy(DEFAULT)), source="builtin")
        return [seen[k] for k in sorted(seen)]

    def define(self, name: str, spec: Any) -> dict[str, Any]:
        """Validate and write an ad-hoc local type. ``spec`` is a mapping or YAML/JSON text."""
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise TypeError_(f"bad type name {_r(name)} (must match {NAME_RE.pattern})")
        if name in RESERVED_TYPES:
            raise TypeError_(f"type {name!r} is built in and cannot be redefined")
        owner = next((src for cand, src, _ in self._candidates() if cand == name and src != "local"), None)
        if owner:
            raise TypeError_(f"type {name!r} is owned by {owner}; edit it there instead of shadowing it")
        if isinstance(spec, str):
            spec = parse(spec)
        if isinstance(spec, dict) and "name" not in spec:
            spec = dict(spec, name=name)
        norm = validate(spec, name=name)
        self.local_dir.mkdir(parents=True, exist_ok=True)
        path = self.local_dir / f"{name}.yaml"
        tmp = path.with_name(f".{name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(norm, indent=2) + "\n")  # JSON is valid YAML
        os.replace(tmp, path)
        return dict(norm, source="local")


def is_builtin_default(snap: dict[str, Any]) -> bool:
    """True when a snapshot is exactly the built-in default type (renders the legacy card text)."""
    return snapshot(snap) == snapshot(validate(copy.deepcopy(DEFAULT)))


def snapshot(tdef: dict[str, Any]) -> dict[str, Any]:
    """What a card stores: everything a renderer needs, nothing about where it came from."""
    return {k: copy.deepcopy(tdef[k]) for k in ("name", "label", "fields", "actions") if k in tdef}
