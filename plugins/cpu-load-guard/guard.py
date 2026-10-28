"""Detection logic for cpu-load-guard (pure functions; no Hermes imports)."""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

DEFAULT_PROTECTED = ["David-MBP", "MacBook-Pro-177"]

# Expressions that evaluate to "number of cores".
NCPU_RE = re.compile(
    r"\bnproc\b|hw\.(?:logical|physical)?cpu\b|hw\.ncpu|_NPROCESSORS_(?:ONLN|CONF)|"
    r"\bcpu_count\s*\(|\bos\.sched_getaffinity\b|\bnum_cpus\b|\bavailableProcessors\b",
    re.I,
)
STRESS_RE = re.compile(r"(?<![\w./-])stress(?:-ng)?\b")
DEVNULL_SRC = r"/dev/(?:zero|urandom)"
# Shell burners: any of these, fanned out to ~one per core, saturates the machine.
SHELL_BURNER_RE = re.compile(
    r"(?<![\w./-])(?:"
    r"yes\b[^;&|\n)]*?(?:>\s*/dev/null|&>\s*/dev/null|&(?!&))"
    r"|dd\s+if=" + DEVNULL_SRC + r"\s+of=/dev/null"
    r"|cat\s+" + DEVNULL_SRC + r"\s*>\s*/dev/null"
    r"|(?:sha\d*sum|md5sum|gzip|bzip2|xz)\s+(?:-\w+\s+)*" + DEVNULL_SRC +
    r"|(?:sysbench|cpuburn|burnP6|burnK7|lookbusy)\b"
    r"|while\s*(?::|true|\[\s*1\s*\])\s*;?\s*do\s*(?::|true)?\s*;?\s*done"
    r")",
    re.I,
)
# Backgrounded burners, counted literally (no loop needed to reach the threshold).
BG_BURNER_RE = re.compile(
    r"(?<![\w./-])(?:yes\b[^;&|\n)]*?(?:>\s*/dev/null|&>\s*/dev/null)\s*&(?!&)"
    r"|dd\s+if=" + DEVNULL_SRC + r"\s+of=/dev/null[^;&|\n]*?&(?!&)"
    r"|cat\s+" + DEVNULL_SRC + r"\s*>\s*/dev/null[^;&|\n]*?&(?!&)"
    r"|while\s*(?::|true|\[\s*1\s*\])\s*;?\s*do\s*(?::|true)?\s*;?\s*done\s*&(?!&))",
    re.I,
)
PY_BURNER_RE = re.compile(
    r"while\s+(?:True|1)\s*:\s*(?:pass|continue)\b|while\s+(?:True|1)\s*:\s*\n\s+(?:pass|continue)\b|"
    r"[\"'](?:yes|stress|stress-ng|sha256sum|md5sum|lookbusy)[\"']",
    re.I,
)


def _hostnames() -> set[str]:
    names = set()
    try:
        full = socket.gethostname()
        names.update({full.lower(), full.split(".")[0].lower()})
    except Exception:
        pass
    return names


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def protected_hosts() -> list[str]:
    env = os.environ.get("CPU_LOAD_GUARD_HOSTS")
    if env is not None:
        return [h.strip() for h in env.split(",") if h.strip()]
    cfg = _home() / "cpu-load-guard.yaml"
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(cfg.read_text()) or {}
        hosts = data.get("protected_hosts")
        if isinstance(hosts, list):
            return [str(h) for h in hosts]
    except Exception:  # missing file, no yaml, bad yaml: fall back to the default
        pass
    return list(DEFAULT_PROTECTED)


def enabled() -> bool:
    if os.environ.get("CPU_LOAD_GUARD") == "off":
        return False
    mine = _hostnames()
    for h in protected_hosts():
        h = h.lower()
        if h in mine or any(m.startswith(h) for m in mine):
            return True
    return False


def ncpu() -> int:
    try:
        return int(os.environ.get("CPU_LOAD_GUARD_NCPU") or os.cpu_count() or 8)
    except ValueError:
        return os.cpu_count() or 8


def threshold() -> int:
    return max(2, ncpu() // 2)


def _num(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


def _multipliers(text: str) -> list[int]:
    """Literal fan-out widths found in loops / xargs / parallel / seq / brace ranges."""
    out = []
    for m in re.finditer(r"\bseq\s+(?:-\w+\s+)*(\d+)(?:\s+(\d+))?(?:\s+(\d+))?", text):
        a, b, c = m.groups()
        if c:  # seq first step last
            out.append((_num(c) - _num(a)) // max(1, _num(b)) + 1)
        elif b:
            out.append(_num(b) - _num(a) + 1)
        else:
            out.append(_num(a))
    for m in re.finditer(r"\{(\d+)\.\.(\d+)\}", text):
        out.append(abs(_num(m.group(2)) - _num(m.group(1))) + 1)
    for m in re.finditer(r"<\s*=?\s*(\d+)\s*;", text):
        out.append(_num(m.group(1)))
    for m in re.finditer(r"\bxargs\b[^|;\n]*?(?:-P\s*|--max-procs[= ])(\d+)", text):
        out.append(_num(m.group(1)))
    for m in re.finditer(r"\bparallel\b[^|;\n]*?(?:-j\s*|--jobs[= ])(\d+)", text):
        out.append(_num(m.group(1)))
    for m in re.finditer(r"\brange\(\s*(?:\d+\s*,\s*)?(\d+)\s*\)", text):
        out.append(_num(m.group(1)))
    return out


def _fan_out_wide(text: str) -> bool:
    """Is there a loop/pool whose width is a per-core expression or >= threshold?"""
    if NCPU_RE.search(text):
        return True
    t = threshold()
    return any(n >= t for n in _multipliers(text))


def _count_flag(text: str, flags: str) -> list[str]:
    return re.findall(r"(?:" + flags + r")[= ]?\s*(\$\(\s*[^)]*\)|`[^`]*`|\$\{?\w+\}?|\d+)", text)


def _wide_arg(arg: str) -> bool:
    arg = arg.strip()
    if arg.isdigit():
        n = int(arg)
        return n == 0 or n >= threshold()  # stress-ng --cpu 0 = all cores
    return True  # $(nproc), $N, `...`: unknown count, treat as per-core


def check_shell(text: str) -> str | None:
    if not text or not text.strip():
        return None
    t = threshold()
    if STRESS_RE.search(text):
        for arg in _count_flag(text, r"--cpu|-c"):
            if _wide_arg(arg):
                return f"stress/stress-ng with {arg.strip()} CPU workers (>= {t} of {ncpu()} cores)"
    if re.search(r"\bopenssl\s+speed\b", text):
        for arg in _count_flag(text, r"-multi"):
            if _wide_arg(arg):
                return f"openssl speed -multi {arg.strip()} (>= {t} of {ncpu()} cores)"
    n_bg = len(BG_BURNER_RE.findall(text))
    if n_bg >= t:
        return f"{n_bg} backgrounded busy loops (>= {t} of {ncpu()} cores)"
    if SHELL_BURNER_RE.search(text) and _fan_out_wide(text):
        return f"a CPU burner fanned out to about one per core (>= {t} of {ncpu()} cores)"
    return None


def check_python(code: str) -> str | None:
    if not code or not code.strip():
        return None
    why = check_shell(code)  # terminal("..."), subprocess.run("stress-ng ..."), os.system
    if why:
        return why
    if PY_BURNER_RE.search(code) and _fan_out_wide(code):
        return f"a Python/subprocess CPU burner fanned out to about one per core (>= {threshold()} of {ncpu()} cores)"
    return None


def message(why: str) -> str:
    return (
        f"cpu-load-guard: refused on this protected host: {why}. Hermes automation must not saturate David's "
        "laptop. Generate load on hermes-hub instead (stress-ng --cpu $(nproc) there), or make the race "
        "deterministic (inject the delay/ordering with an Event, mock or fake clock) instead of relying on host load. "
        "A bounded test run such as `pytest -n 2` is fine."
    )
