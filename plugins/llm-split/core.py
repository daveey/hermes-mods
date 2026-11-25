"""LLM split: show each subscription pool's share of quota next to the router's traffic split, and set the split.

Traffic split = the provider weights in ``model_routing.routes.<class>[].weight`` (``agent/model_routing_state.py``
``_choose`` does weighted round-robin over providers, so a provider's share of new class allocations is its weight
over the sum of the weights in that class). Every class and every Hermes home on this host (the root home and each
``profiles/<name>`` with ``model_routing`` configured) gets the same per-provider weights.

Quota share = weekly headroom: the sum of ``100 - used%`` over each pool's accounts' latest weekly reading
(account-usage's history, read with its ``forecast.read_samples``), else the dispatcher pacer's fresh
``v2.pools[p].ratio`` in ``<root>/llm_pacing.json`` (a pool at reserve counts 0), else unknown.

Modes (``<root>/llm-split/state.json``):
- ``manual``: weights follow the share the user dragged to.
- ``auto``: weights follow the quota share; :func:`tick` re-applies it (the long-lived server loop, every
  :data:`AUTO_INTERVAL_S`) when any provider moved by :data:`AUTO_MIN_DELTA` points or more.

Weights are integers 1..100 (``validate_policy``); a provider never goes below 1, so a dry pool still gets a trickle,
and its own ineligibility (quota walls) keeps routing off it. Only the ``weight`` values change in each config.yaml
(ruamel round-trip, atomic replace, a ``.pre-llm-split`` backup of the first write per day); the result is read back
and validated with core's ``validate_policy`` when core is importable.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PACING_STALE_S = 20 * 60
HISTORY_WINDOW_S = 6 * 3600
AUTO_INTERVAL_S = 300
AUTO_MIN_DELTA = 3
MAX_TOTAL = 100
# Route provider ids that read another pool's quota (the fast tier's ``xai`` route spends the xai-oauth pool).
POOL_OF = {"xai": "xai-oauth"}
LABEL = {"anthropic": "Claude", "openai-codex": "GPT", "xai-oauth": "Grok", "xai": "Grok"}


def hermes_root() -> Path:
    try:
        from hermes_constants import get_default_hermes_root
        return Path(get_default_hermes_root())
    except Exception:  # outside a Hermes runtime (tests pass a root explicitly)
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def pool_of(provider: str) -> str:
    return POOL_OF.get(provider, provider)


# ── quota ────────────────────────────────────────────────────────────────────

def _pacing_quota(root: Path, now: float) -> Optional[Dict[str, Any]]:
    try:
        v2 = json.loads((root / "llm_pacing.json").read_text()).get("v2") or {}
    except (OSError, ValueError, AttributeError):
        return None
    pools = v2.get("pools")
    at = v2.get("computed_at")
    if not isinstance(pools, dict) or not isinstance(at, (int, float)) or now - at > PACING_STALE_S:
        return None
    raw = {}
    for p, x in pools.items():
        r = x.get("ratio") if isinstance(x, dict) else None
        if isinstance(r, (int, float)) and not isinstance(r, bool) and math.isfinite(r) and r >= 0:
            raw[p] = 0.0 if x.get("at_reserve") else float(r)
    total = sum(raw.values())
    if not raw or total <= 0:
        return None
    return {"source": "pacer", "at": at, "share": {p: 100 * v / total for p, v in raw.items()},
            "dry": sorted(p for p, v in raw.items() if v <= 0)}


def _forecast_module(root: Path):
    """account-usage's ``forecast`` (the weekly history reader), loaded by path; None when that plugin is absent."""
    import importlib.util
    import sys
    for base in (root / "plugins" / "account-usage", Path(__file__).resolve().parents[1] / "account-usage"):
        f = base / "forecast.py"
        if f.is_file():
            name = "_llm_split_au_forecast"
            mod = sys.modules.get(name)
            if mod is None:
                spec = importlib.util.spec_from_file_location(name, f)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                sys.modules[name] = mod
            return mod
    return None


def _history_quota(root: Path, now: float, pools: Iterable[str] = ("anthropic", "openai-codex", "xai-oauth")) -> Optional[Dict[str, Any]]:
    """Sum of ``100 - used%`` over each pool's accounts' latest weekly reading in the last HISTORY_WINDOW_S."""
    try:
        fc = _forecast_module(root)
    except Exception:
        fc = None
    if fc is None:
        return None
    headroom, accounts, newest = {}, {}, 0.0
    for p in pools:
        try:
            samples = fc.read_samples(root, p, now - HISTORY_WINDOW_S)
        except Exception:
            continue
        last: Dict[str, tuple] = {}
        for t, acct, used, _ in samples:
            if acct not in last or t >= last[acct][0]:
                last[acct] = (t, used)
        if last:
            headroom[p] = sum(max(0.0, 100.0 - u) for _, u in last.values())
            accounts[p] = {"n": len(last), "out": sum(1 for _, u in last.values() if u >= 100),
                           "headroom": round(headroom[p], 1)}
            newest = max(newest, max(t for t, _ in last.values()))
    total = sum(headroom.values())
    if total <= 0:
        return None
    return {"source": "account-usage", "at": newest, "share": {p: 100 * v / total for p, v in headroom.items()},
            "dry": sorted(p for p, v in headroom.items() if v <= 0), "accounts": accounts}


def read_quota(root: Path, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    return _history_quota(root, now) or _pacing_quota(root, now) or {"source": None, "at": None, "share": {}, "dry": []}


# ── homes and weights ─────────────────────────────────────────────────────────

def homes(root: Path, strict: bool = False) -> List[Path]:
    """The root home and every profile home whose config.yaml has a ``model_routing.routes`` mapping."""
    out = []
    for h in [root, *sorted((root / "profiles").glob("*"))]:
        path = h / 'config.yaml'
        if strict and path.is_file():
            import yaml
            try:
                cfg = yaml.safe_load(path.read_bytes())
            except (yaml.YAMLError, UnicodeError) as exc:
                raise ValueError(f"cannot parse config {path}: {exc}") from exc
            if cfg is None:  # empty/comment-only homes have no routing policy
                continue
            if not isinstance(cfg, dict):
                raise ValueError(f"config is not a mapping: {path}")
        else:
            cfg = _load_yaml(path)
        if _routes(cfg):
            out.append(h)
    return out


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
        data = yaml.safe_load(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _routes(cfg: dict) -> dict:
    mr = cfg.get("model_routing")
    routes = mr.get("routes") if isinstance(mr, dict) else None
    return routes if isinstance(routes, dict) and routes else {}


def read_weights(home: Path) -> Dict[str, int]:
    """Provider -> weight (core requires one weight per provider across every class)."""
    out: Dict[str, int] = {}
    for level in _routes(_load_yaml(home / "config.yaml")).values():
        for r in level or []:
            if isinstance(r, dict) and isinstance(r.get("weight"), int):
                out.setdefault(r["provider"], r["weight"])
    return out


def providers(root: Path) -> List[str]:
    seen: List[str] = []
    for h in homes(root):
        for p in read_weights(h):
            if p not in seen:
                seen.append(p)
    return seen


def traffic_share(weights: Dict[str, int], root: Optional[Path] = None) -> Dict[str, float]:
    """Pool -> % of allocations, averaged over the classes of the root home (a class without a provider gives it 0)."""
    total = sum(weights.values())
    out: Dict[str, float] = {}
    for p, w in weights.items():
        out[pool_of(p)] = out.get(pool_of(p), 0) + (100 * w / total if total else 0)
    return out


def weights_for(share: Dict[str, float], provs: Iterable[str]) -> Dict[str, int]:
    """Integer weights 1..100 (sum <= 100) for each route provider, proportional to its pool's share, reduced by gcd.

    Providers that spend one pool (``xai`` and ``xai-oauth``) each get that pool's weight: they never share a class.
    """
    provs = list(provs)
    pools = sorted({pool_of(p) for p in provs})
    s = {p: max(0.0, float(share.get(p, 0) or 0)) for p in pools}
    total = sum(s.values()) or 1.0
    raw = {p: MAX_TOTAL * v / total for p, v in s.items()}
    w = {p: max(1, int(round(v))) for p, v in raw.items()}
    while sum(w.values()) > MAX_TOTAL:  # floors at 1 can push the sum over; take from the largest
        big = max(w, key=lambda k: w[k])
        w[big] -= 1
    g = 0
    for v in w.values():
        g = math.gcd(g, v)
    w = {p: v // (g or 1) for p, v in w.items()}
    return {p: w[pool_of(p)] for p in provs}


def _prepare_weights(home: Path, weights: Dict[str, int]):
    """Prepare original/replacement bytes without writing; raises on invalid output.

    Only the ``weight:`` value tokens change: ruamel finds their line/column, the text is edited in place, so the
    rest of config.yaml (indentation, comments, quoting) is byte-identical.
    """
    import re
    from ruamel.yaml import YAML
    path = home / "config.yaml"
    original = path.read_bytes()
    text = original.decode("utf-8")
    doc = YAML().load(text)
    lines = text.splitlines(keepends=True)
    edits = {}
    for level in ((doc.get("model_routing") or {}).get("routes") or {}).values():
        for r in level or []:
            p = r.get("provider")
            if p not in weights or "weight" not in r or r.get("weight") == weights[p]:
                continue
            ln, col = r.lc.value("weight")
            line = lines[ln]
            m = re.compile(r"-?\d+").match(line, col)
            if not m:
                raise ValueError(f"cannot locate weight value at {path}:{ln + 1}")
            # YAML aliases revisit the same source node: replace its token once.
            key = (ln, m.start(), m.end())
            value = str(int(weights[p]))
            if key in edits and edits[key] != value:
                raise ValueError(f"conflicting aliased weight at {path}:{ln + 1}")
            edits[key] = value
    # Positions refer to original bytes: edit right-to-left even within one flow line.
    for (ln, start, end), value in sorted(edits.items(), reverse=True):
        lines[ln] = lines[ln][:start] + value + lines[ln][end:]
    new = "".join(lines)
    _validate(new)  # unchanged homes must also validate before any home is installed
    return path, original, new.encode("utf-8")


def _replace_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.llm-split.{os.getpid()}")
    try:
        tmp.write_bytes(data)
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _install_weights(plans, now: float, finish=lambda: None):
    """Prepare every home first; restore installed bytes on write/state failure.

    Atomic per-file replacement, not a crash-atomic multi-file transaction.
    Rollback failure is explicit rather than hidden behind the original error.
    """
    changed = [(p, old, new) for p, old, new in plans if old != new]
    installed = []
    try:
        for p, old, new in plans:
            if p.read_bytes() != old:
                raise ValueError(f"config changed during split preparation: {p}")
        for p, old, new in changed:
            backup = p.with_name(f"config.yaml.pre-llm-split-{time.strftime('%Y%m%d', time.gmtime(now))}")
            if not backup.exists():
                backup.write_bytes(old)
            _replace_bytes(p, new)
            installed.append((p, old))
        finish()
    except Exception as exc:
        failed = []
        for p, old in reversed(installed):
            try:
                _replace_bytes(p, old)
            except Exception as rollback:
                failed.append(f"{p}: {rollback}")
        if failed:
            raise RuntimeError(f"split failed: {exc}; ROLLBACK FAILED: {'; '.join(failed)}") from exc
        raise
    return [str(p.parent) for p, _, _ in changed]


def write_weights(home: Path, weights: Dict[str, int], now: Optional[float] = None) -> bool:
    return bool(_install_weights([_prepare_weights(home, weights)], time.time() if now is None else now))


def _validate(text: str) -> None:
    import yaml
    policy = (yaml.safe_load(text) or {}).get("model_routing")
    try:
        from agent.model_routing import validate_policy
    except Exception:  # outside core (tests): the structural checks below still run
        validate_policy = None
    if validate_policy is not None:
        validate_policy(policy)
    for level in (policy or {}).get("routes", {}).values():
        by = {}
        for r in level:
            w = r.get("weight")
            if type(w) is not int or not 1 <= w <= 100:
                raise ValueError(f"weight {w!r} out of range")
            if by.setdefault(r["provider"], w) != w:
                raise ValueError("provider weights differ within a class")


# ── state, snapshot, apply ────────────────────────────────────────────────────

_LOCK = threading.Lock()


def state_path(root: Path) -> Path:
    return root / "llm-split" / "state.json"


def read_state(root: Path) -> Dict[str, Any]:
    try:
        s = json.loads(state_path(root).read_text())
        if isinstance(s, dict) and s.get("mode") in ("auto", "manual"):
            return s
    except (OSError, ValueError):
        pass
    return {"mode": "manual", "share": None, "updated_at": None}


def _write_state(root: Path, state: Dict[str, Any]) -> None:
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".state.{os.getpid()}.json")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, p)


def snapshot(root: Path, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    hs = homes(root)
    weights = read_weights(hs[0]) if hs else {}
    pools = sorted({pool_of(p) for p in providers(root)})
    quota = read_quota(root, now)
    qshare = {p: quota["share"].get(p, 0.0) for p in pools} if quota["share"] else {}
    st = read_state(root)
    mixed = [str(h) for h in hs[1:] if any(weights.get(p, w) != w for p, w in read_weights(h).items())]
    return {
        "api": 1,
        "pools": [{"id": p, "label": LABEL.get(p, p)} for p in pools],
        "quota": {"source": quota["source"], "at": quota["at"], "share": qshare,
                  "dry": [p for p in quota["dry"] if p in pools],
                  "accounts": {p: v for p, v in (quota.get("accounts") or {}).items() if p in pools}},
        "traffic": {"share": traffic_share(weights), "weights": weights},
        "mode": st["mode"],
        "manual_share": st.get("share"),
        "updated_at": st.get("updated_at"),
        "homes": [str(h) for h in hs],
        "mixed_homes": mixed,
        "now": now,
    }


def _normalise(share: Dict[str, Any], pools: List[str]) -> Dict[str, float]:
    clean = {}
    for p in pools:
        v = share.get(p, 0)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0:
            raise ValueError(f"share for {p} must be a number >= 0")
        clean[p] = float(v)
    unknown = sorted(set(share) - set(pools))
    if unknown:
        raise ValueError(f"unknown pools: {', '.join(unknown)}")
    total = sum(clean.values())
    if total <= 0:
        raise ValueError("shares must not all be 0")
    return {p: 100 * v / total for p, v in clean.items()}


def apply(root: Path, mode: str, share: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Set the mode (and, in manual, the share); write the weights to every home. Returns the new snapshot."""
    if mode not in ("auto", "manual"):
        raise ValueError("mode must be auto or manual")
    now = time.time() if now is None else now
    with _LOCK:
        provs = providers(root)
        pools = sorted({pool_of(p) for p in provs})
        if not pools:
            raise ValueError("no model_routing routes on this host")
        if mode == "manual":
            if not isinstance(share, dict):
                raise ValueError("manual needs a share")
            target = _normalise(share, pools)
        else:
            q = read_quota(root, now)
            if not q["share"]:
                raise ValueError("no quota reading on this host: auto has nothing to follow")
            target = _normalise({p: q["share"].get(p, 0.0) for p in pools}, pools)
        weights = weights_for(target, provs)
        plans = [_prepare_weights(h, weights) for h in homes(root, strict=True)]
        written = [str(p.parent) for p, old, new in plans if old != new]
        _install_weights(plans, now, lambda: _write_state(root, {
            "mode": mode, "share": target if mode == "manual" else None, "updated_at": now,
            "weights": weights, "written": written}))
    snap = snapshot(root, now)
    snap["written"] = written
    return snap


def tick(root: Path, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Auto mode: re-apply the quota share when it moved by AUTO_MIN_DELTA points or more. None = nothing to do."""
    now = time.time() if now is None else now
    if read_state(root)["mode"] != "auto":
        return None
    snap = snapshot(root, now)
    q, t = snap["quota"]["share"], snap["traffic"]["share"]
    if not q:
        return None
    target = weights_for(q, providers(root))
    if target == snap["traffic"]["weights"]:
        return None
    want = traffic_share(target)
    if max(abs(want.get(p, 0) - t.get(p, 0)) for p in set(want) | set(t)) < AUTO_MIN_DELTA:
        return None
    return apply(root, "auto", now=now)


# ── the auto loop (one per root, long-lived server processes only) ─────────────

_loop_state: Dict[str, Any] = {}
THREAD_NAME = "llm-split-auto"


def eligible_process(argv: Optional[list] = None, env: Optional[Any] = None) -> bool:
    import sys
    argv = list(sys.argv if argv is None else argv)
    env = os.environ if env is None else env
    if env.get("HERMES_KANBAN_TASK") or env.get("HERMES_KANBAN_WORKER_TASK"):
        return False
    role = next((a for a in argv[1:] if not a.startswith("-")), "")
    return role in ("gateway", "serve", "dashboard")


def _loop(stop: threading.Event, root: Path) -> None:
    import fcntl
    import logging
    log = logging.getLogger("hermes.plugins.llm-split")
    lock = root / "llm-split" / "auto.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        while not stop.is_set():
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                stop.wait(AUTO_INTERVAL_S)
        while not stop.is_set():
            try:
                done = tick(root)
                if done:
                    log.info("llm-split: auto applied %s to %s", done["traffic"]["weights"], done.get("written"))
            except Exception:
                log.exception("llm-split: auto tick failed")
            stop.wait(AUTO_INTERVAL_S)
    finally:
        os.close(fd)


def start(root: Optional[Path] = None) -> None:
    t = _loop_state.get("thread")
    if t is not None and t.is_alive():
        return
    for other in threading.enumerate():  # a plugin reload re-runs this module: stop the old loop
        if other.name == THREAD_NAME and other.is_alive():
            ev = (getattr(other, "_args", None) or (None,))[0]
            if isinstance(ev, threading.Event):
                ev.set()
    stop = threading.Event()
    t = threading.Thread(target=_loop, args=(stop, root or hermes_root()), name=THREAD_NAME, daemon=True)
    _loop_state.update(thread=t, stop=stop)
    t.start()


def stop() -> None:
    ev = _loop_state.get("stop")
    if ev is not None:
        ev.set()
