"""Opt-in, one-shot owner census: which module global, class attribute, running frame or class holds the
live Python heap. No tracemalloc, so an armed process pays nothing until the one request.

Default off. A process arms when loop-watch registers (at start, and again on a plugin reload) and finds
the arm file ``<HERMES_HOME>/loop-watch-roots/arm``: a regular 0600 file owned by this user, with one
link, holding one JSON object ``{"until": <epoch seconds>, "kinds": ["gateway", ...]}``.

- ``until`` must be in the future and at most 72 h away; otherwise the file is inert.
- ``kinds`` (optional) names the process kinds that may arm: ``gateway`` (``hermes gateway run``),
  ``serve``, ``dashboard``, ``other``. Default: ``gateway`` only.

It is a file, not an env key, on purpose: a ``.env`` key is copied between gateways by the credential
relay and is only read at process start; the file stays in its home and also arms a running process on
its next plugin reload. Delete it when done.

An armed process waits on the same private one-shot trigger as the cache census (census.Probe) with the
command ``roots-census-v1``, until ``until`` or until the arm file is removed (re-read every 30 s; a
rewritten file moves the window). A process consumes at most one request in its lifetime, valid or not;
one that disarmed without a request can arm again.

The scan is a multi-source breadth-first walk, level by level, so an object shared by several owners is
counted once, for the nearest one:

1. module globals and class attributes that are not modules, classes, functions or numbers
   (``module.name``, ``module.Class.attr``). A plain instance found there gets a row per attribute
   (``module.name.attr``, two levels), which splits a singleton into its fields;
2. locals of the other threads' running frames and of suspended generators/coroutines
   (``<frame file.py:qualname>``; values on a running frame's evaluation stack are not visible);
3. what classes and functions hold: class dicts, constants, docstrings, defaults and closures, per
   defining module (``<code module>``);
4. objects nothing above reached, other than plain containers, by class (``<inst module.Class>``; a
   class with at most 8 such instances gets a row per attribute);
5. whatever is left, by type (``<unowned dict>``).

Sizes are ``sys.getsizeof`` (shallow) summed per owner. The report holds names as they appear in code
(module, global, class, attribute and function names, source file basenames), byte sizes and object
counts: never values, dict keys or reprs. A name that is not a short ASCII identifier is shown as ``?``.
ints, floats and None are not counted. The walk holds the GIL in 5 ms slices like any Python thread,
touches every live object once (compressed pages are faulted in) and gives up at ``DEADLINE_S`` with
``truncated: true``: the deadline is checked every 16k objects and before each container of more than
100k items (one such container is finished once begun).

Code of inspected objects that can run: ``__sizeof__`` (as ``sys.getsizeof`` calls it; an exception counts
0 bytes), finalizers of objects whose last reference the scan happened to hold, and a data descriptor
that a metaclass or module subclass defines under the name ``__dict__``, ``__module__`` or
``__qualname__``. Otherwise types are compared by identity and attributes are read through the base
``type``/``object`` slots; an object that raises is skipped.
"""
from __future__ import annotations

import gc
import json
import logging
import math
import os
import re
import stat
import sys
import threading
import time
import types

COMMAND = b"roots-census-v1\n"
MAX_OUTPUT = 65536
TOP = 100
DEADLINE_S = 60.0
NAME_DEPTH = 2  # instance attribute levels that get their own row under a module global
MAX_ATTRS = 64  # an instance with more attributes than this is data-shaped: no per-attribute rows
MAX_LABELS = 20000
BIG_CONTAINER = 100000  # the deadline is also checked before walking into a container this large
SPLIT_INSTANCES = 8  # a class with at most this many unreached instances gets a row per attribute
LABEL_MAX = 160
MAX_WINDOW_S = 259200.0  # 72 h: spans a restart cycle and the re-measure after a fix
MAX_ARM_BYTES = 512
RECHECK_S = 30.0  # an armed process re-reads the arm file this often: removing it disarms
KINDS = ("gateway", "serve", "dashboard", "other")
DEFAULT_KINDS = frozenset({"gateway"})
SUBDIR = "loop-watch-roots"
ARM = "arm"
SENTINEL = "_hermes_loop_watch_roots_census"
log = logging.getLogger("loop-watch")

# Never walked into: they lead to every module's globals and code, not to data an owner holds.
_SKIP = (types.ModuleType, type, types.FunctionType, types.BuiltinFunctionType, types.MethodType, types.CodeType,
         types.GetSetDescriptorType, types.MemberDescriptorType, types.WrapperDescriptorType,
         types.MethodDescriptorType, types.MethodWrapperType, staticmethod, classmethod, property,
         int, float, complex, type(None))  # bool is an int
_LEAVES = (str, bytes, bytearray)
# Compared by id(): a set lookup of an arbitrary type would call its metaclass's __hash__/__eq__.
_CONTAINERS = frozenset(map(id, (dict, list, tuple, set, frozenset, types.CellType)))
_SUSPENDED = frozenset(map(id, (types.GeneratorType, types.CoroutineType, types.AsyncGeneratorType)))
# Label parts are code identifiers, ASCII only; anything else is shown as "?".
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,59}\Z")
_PART = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_-]{0,59}|<(?:locals|lambda|genexpr|listcomp|dictcomp|setcomp|module)>)\Z")
_FILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,79}\Z")
_WALK, _IGNORE, _LEAF = 0, 1, 2


def process_kind(argv=None):
    """``gateway`` (hermes gateway run), ``serve``, ``dashboard`` or ``other`` (CLI, chat, cron, workers)."""
    argv = sys.argv if argv is None else argv
    if list(argv[1:3]) == ["gateway", "run"]:
        return "gateway"
    if len(argv) > 1 and argv[1] in ("serve", "dashboard"):
        return argv[1]
    return "other"


def read_arm(home, *, now=None):
    """``(until, kinds)`` from a valid arm file, else ``(0.0, reason)``. Never raises."""
    now = time.time() if now is None else now
    path = os.path.join(str(home), SUBDIR, ARM)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return 0.0, "no arm file"
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600
                or st.st_nlink != 1 or st.st_size > MAX_ARM_BYTES):
            return 0.0, "the arm file is not a private regular 0600 file of this user"
        raw = os.read(fd, MAX_ARM_BYTES + 1)
    except OSError:
        return 0.0, "the arm file is unreadable"
    finally:
        os.close(fd)
    try:
        spec = json.loads(raw.decode("ascii"))
        end = float(spec["until"]) if type(spec) is dict and type(spec.get("until")) in (int, float) else 0.0
    except Exception:  # noqa: BLE001 (bad JSON, a number float() overflows on)
        return 0.0, "the arm file is not a JSON object with a numeric until"
    if not (math.isfinite(end) and now < end <= now + MAX_WINDOW_S):
        return 0.0, f"until is absent, past or more than {MAX_WINDOW_S:g}s away"
    named = spec.get("kinds")
    named = {k for k in named if type(k) is str} & set(KINDS) if type(named) is list else set()
    return end, (frozenset(named) if named else DEFAULT_KINDS)


def _storage(obj, tp):
    """The instance ``__dict__``, read through the type's own slot: never an overridden attribute or property."""
    try:
        if not type.__getattribute__(tp, "__dictoffset__"):
            return None
    except Exception:  # noqa: BLE001
        return None
    for cls in type.__getattribute__(tp, "__mro__"):
        descriptor = type.__getattribute__(cls, "__dict__").get("__dict__")
        if type(descriptor) is types.GetSetDescriptorType:
            try:
                value = descriptor.__get__(obj, tp)
            except Exception:  # noqa: BLE001
                return None
            return value if type(value) is dict else None
        if descriptor is not None:
            return None
    return None


def _ident(name):
    """An attribute or global name as a label part: itself when it is a short ASCII identifier, else ``?``."""
    return name if type(name) is str and _IDENT.match(name) else "?"


def _dotted(name):
    """A module or qualified name as a label part: ``?`` unless every dotted part is identifier-like."""
    if type(name) is not str or len(name) > 120 or not all(_PART.match(part) for part in name.split(".")):
        return "?"
    return name


def _type_name(tp):
    try:
        module = type.__getattribute__(tp, "__module__")
        name = type.__getattribute__(tp, "__qualname__")
    except Exception:  # noqa: BLE001
        return "?"
    return _dotted(name) if type(module) is str and module == "builtins" else f"{_dotted(module)}.{_dotted(name)}"


def _frame_label(code):
    base = os.path.basename(code.co_filename)
    return f"<frame {base if _FILE.match(base) else '?'}:{_dotted(code.co_qualname)}>"


class _Walk:
    """Visited bitmap, per-owner totals and the level-by-level walk. Created after ``gc.get_objects()`` so
    nothing it owns is in that list, and referenced only from the scanning frame, which is never a root.
    ``scan`` must stay free of closures: a cell of its frame exists before that list is taken."""

    def __init__(self, clock, deadline):
        self.pages = {}  # 1 MiB of address space -> 8 KiB bitmap (one bit per 16 bytes): ~1% of the heap
        self.kinds = {}  # id(type) -> _WALK / _IGNORE / _LEAF
        self.types = []  # keeps every classified type alive so an id is never reused during the scan
        self.labels, self.sizes, self.counts, self.index = [], [], [], {}
        self.clock, self.deadline = clock, deadline
        self.truncated = False
        self.phases = []

    def late(self):
        """True once the deadline has passed; every long loop asks every few thousand objects."""
        if not self.truncated and self.clock() > self.deadline:
            self.truncated = True
        return self.truncated

    def chunks(self, items, size=0x4000):
        """``items`` in slices, stopping at the deadline: the only way scan() iterates the heap."""
        for start in range(0, len(items), size):
            if self.late():
                return
            yield items[start:start + size]

    def kind(self, tp):
        found = self.kinds.get(id(tp))
        if found is None:
            try:
                found = _IGNORE if issubclass(tp, _SKIP) else _LEAF if issubclass(tp, _LEAVES) else _WALK
            except Exception:  # noqa: BLE001
                found = _IGNORE
            self.kinds[id(tp)] = found
            self.types.append(tp)
        return found

    def mark(self, obj):
        """True the first time ``obj`` is seen."""
        addr = id(obj)
        page = self.pages.get(addr >> 20)
        if page is None:
            page = self.pages[addr >> 20] = bytearray(8192)
        bit = (addr >> 4) & 0xFFFF
        mask = 1 << (bit & 7)
        if page[bit >> 3] & mask:
            return False
        page[bit >> 3] |= mask
        return True

    def seen(self, obj):
        addr = id(obj)
        page = self.pages.get(addr >> 20)
        bit = (addr >> 4) & 0xFFFF
        return bool(page is not None and page[bit >> 3] & (1 << (bit & 7)))

    def label(self, name):
        name = name[:LABEL_MAX]
        found = self.index.get(name)
        if found is None:
            if len(self.labels) >= MAX_LABELS:
                name = "<other owners>"
                found = self.index.get(name)
                if found is not None:
                    return found
            found = self.index[name] = len(self.labels)
            self.labels.append(name)
            self.sizes.append(0)
            self.counts.append(0)
        return found

    def count(self, li, obj):
        try:
            self.sizes[li] += sys.getsizeof(obj)
        except Exception:  # noqa: BLE001 - a broken __sizeof__ counts 0 bytes
            pass
        self.counts[li] += 1

    def add(self, frontier, obj, name, depth=0):
        """Queue ``obj`` under ``name``. A plain instance within ``depth`` gets a row per attribute."""
        tp = type(obj)
        kind = self.kind(tp)
        if kind == _IGNORE or not self.mark(obj):
            return
        li = self.label(name)
        if kind == _LEAF:
            self.count(li, obj)
            return
        attrs = _storage(obj, tp) if depth > 0 and id(tp) not in _CONTAINERS else None
        if attrs is not None and len(attrs) <= MAX_ATTRS:
            try:
                items = list(attrs.items())
            except Exception:  # noqa: BLE001
                items = []
            if self.mark(attrs):
                self.count(li, attrs)
            for key, value in items:
                if type(key) is str and _IDENT.match(key):
                    self.add(frontier, value, f"{name}.{key}", depth - 1)
                else:
                    self.add(frontier, value, name)
        frontier.setdefault(li, []).append(obj)  # its own size, and what it holds beside the attribute dict

    def add_code(self, frontier, code, name):
        """A code object and its nested ones (the walk ignores code objects reached as plain referents)."""
        if not self.mark(code):
            return
        self.count(self.label(name), code)
        for ref in gc.get_referents(code):
            if type(ref) is types.CodeType:
                self.add_code(frontier, ref, name)
            else:
                self.add(frontier, ref, name)

    def run(self, frontier):
        """Walk one phase and record its bytes."""
        self.walk(frontier)
        self.phases.append(sum(self.sizes) - sum(self.phases))

    def walk(self, frontier):
        """``frontier``: {owner index: [marked objects]}. One level per pass over every owner."""
        pages, kinds, sizes, counts = self.pages, self.kinds, self.sizes, self.counts
        getsize, referents, classify, clock = sys.getsizeof, gc.get_referents, self.kind, self.clock
        while frontier and not self.truncated:
            nxt = {}
            for li, objs in frontier.items():
                if clock() > self.deadline:
                    self.truncated = True
                    return
                out = None
                size = leaves = 0
                for done, obj in enumerate(objs):
                    if not done & 0x3FFF and done and clock() > self.deadline:
                        self.truncated = True
                        break
                    try:
                        size += getsize(obj)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        refs = referents(obj)
                    except Exception:  # noqa: BLE001
                        continue
                    if len(refs) > BIG_CONTAINER and clock() > self.deadline:
                        self.truncated = True
                        break
                    for ref in refs:
                        tp = type(ref)
                        kind = kinds.get(id(tp))
                        if kind is None:
                            kind = classify(tp)
                        if kind == _IGNORE:
                            continue
                        addr = id(ref)
                        page = pages.get(addr >> 20)
                        if page is None:
                            page = pages[addr >> 20] = bytearray(8192)
                        bit = (addr >> 4) & 0xFFFF
                        mask = 1 << (bit & 7)
                        if page[bit >> 3] & mask:
                            continue
                        page[bit >> 3] |= mask
                        if kind == _LEAF:
                            try:
                                size += getsize(ref)
                            except Exception:  # noqa: BLE001
                                pass
                            leaves += 1
                        else:
                            if out is None:
                                out = nxt[li] = []
                            out.append(ref)
                sizes[li] += size
                counts[li] += len(objs) + leaves
                if self.truncated:
                    return
            frontier = nxt


def _dict_of(obj):
    """``obj.__dict__`` of a module or class through the base slot (a lazy module's own lookup imports it)."""
    try:
        found = (type if issubclass(type(obj), type) else object).__getattribute__(obj, "__dict__")
    except Exception:  # noqa: BLE001
        return {}
    return found if type(found) is dict or type(found) is types.MappingProxyType else {}


def _code_label(obj):
    """``<code module>`` for a class or function: the module's code, constants, docstrings and closures."""
    try:
        module = _dict_of(obj).get("__module__") if issubclass(type(obj), type) else obj.__module__
    except Exception:  # noqa: BLE001
        module = None
    return f"<code {_dotted(module)}>"


def scan(*, clock=time.monotonic, top=TOP, deadline_s=DEADLINE_S, modules=None):
    """Walk the heap from its owners. ``modules`` (tests) replaces ``sys.modules``.

    Every per-object step is fenced: one object that raises is skipped, it never ends the scan.
    """
    started = clock()
    everything = gc.get_objects()  # first: nothing the census creates below is in it
    w = _Walk(clock, started + deadline_s)
    frame = sys._getframe()
    while frame is not None:  # this thread's stack holds the census itself: never a root, never walked
        w.mark(frame)
        frame = frame.f_back
    source = sys.modules if modules is None else modules
    mods = sorted((n, m) for n, m in list(source.items())
                  if type(n) is str and issubclass(type(m), types.ModuleType))
    for _, module in mods:  # module dicts are owners' containers, never data: nothing walks into one
        w.mark(_dict_of(module))

    # 1. module globals and class attributes
    frontier = {}
    for chunk in w.chunks(mods, 16):
        for name, module in chunk:
            name = _dotted(name)
            for key, value in list(_dict_of(module).items()):
                try:
                    if type(key) is not str or (key.startswith("__") and key.endswith("__")):
                        continue
                    if issubclass(type(value), type):
                        members = _dict_of(value)
                        home = members.get("__module__")
                        if type(home) is not str or _dotted(home) != name:
                            continue  # imported class: listed under its own module
                        owner = f"{name}.{_dotted(type.__getattribute__(value, '__qualname__'))}"
                        for attr, member in list(members.items()):
                            if type(attr) is str and not attr.startswith("__"):
                                w.add(frontier, member, f"{owner}.{_ident(attr)}")
                        continue
                    w.add(frontier, value, f"{name}.{_ident(key)}", NAME_DEPTH)
                except Exception:  # noqa: BLE001
                    continue
    roots = sum(len(v) for v in frontier.values())
    w.run(frontier)

    # 2. frames: the other threads' stacks, then suspended generators and coroutines
    frontier = {}
    current = threading.get_ident()
    for ident, frame in list(sys._current_frames().items()):
        depth = 0
        while frame is not None and ident != current and depth < 500:
            name = _frame_label(frame.f_code)
            w.add(frontier, frame, name)
            # A running frame's locals live on its thread's stack: gc.get_referents(frame) does not list them.
            try:
                held = frame.f_locals
                held = [held] if type(held) is dict else list(held.values())  # a module frame: its marked dict
            except Exception:  # noqa: BLE001
                held = ()
            for value in held:
                w.add(frontier, value, name)
            frame = frame.f_back
            depth += 1
    frame = held = value = None  # this thread's own frame in a local of itself would outlive the scan
    for chunk in w.chunks(everything):
        for obj in chunk:
            if id(type(obj)) in _SUSPENDED:
                code = getattr(obj, "gi_code", None) or getattr(obj, "cr_code", None) or getattr(obj, "ag_code", None)
                if code is not None:
                    w.add(frontier, obj, _frame_label(code))
    w.run(frontier)

    # 3. code: what classes and functions hold (class dicts, constants, docstrings, defaults, closures)
    frontier = {}
    labels = {}
    for chunk in w.chunks(everything):
        for obj in chunk:
            tp = type(obj)
            if tp is not types.FunctionType and not issubclass(tp, type):
                continue
            try:
                label = _code_label(obj)
                label = labels.setdefault(label, label)  # one string per module, not one per function
                for ref in gc.get_referents(obj):
                    if type(ref) is types.CodeType:
                        w.add_code(frontier, ref, label)
                    else:
                        w.add(frontier, ref, label)
            except Exception:  # noqa: BLE001
                continue
    w.run(frontier)

    # 4. what nothing reached, other than plain containers, by class; a class with few instances gets a
    #    row per attribute (reading __dict__ materializes it, so never for a class with many instances)
    frontier = {}
    names, population = {}, {}
    for chunk in w.chunks(everything):
        for obj in chunk:
            key = id(type(obj))
            if key not in _CONTAINERS and not w.seen(obj):
                population[key] = population.get(key, 0) + 1
    for chunk in w.chunks(everything):
        for obj in chunk:
            tp = type(obj)
            key = id(tp)
            if key in _CONTAINERS or w.seen(obj):
                continue
            try:
                name = names.get(key)
                if name is None:
                    name = names[key] = f"<inst {_type_name(tp)}>"
                    w.kind(tp)  # keeps tp alive for the id-keyed caches
                w.add(frontier, obj, name, 1 if population.get(key, 0) <= SPLIT_INSTANCES else 0)
            except Exception:  # noqa: BLE001
                continue
    w.run(frontier)

    # 5. the rest, by type
    frontier = {}
    for chunk in w.chunks(everything):
        for obj in chunk:
            if w.seen(obj):
                continue
            try:
                tp = type(obj)
                name = names.get(-id(tp))
                if name is None:
                    name = names[-id(tp)] = f"<unowned {_type_name(tp)}>"
                    w.kind(tp)
                w.add(frontier, obj, name)
            except Exception:  # noqa: BLE001
                continue
    w.run(frontier)
    total = sum(w.sizes)
    gc_objects = len(everything)
    # Dropping the list can release the last reference to objects other threads let go of mid-scan: their
    # finalizers then run here, on the census thread.
    del everything, frontier
    chunk = obj = None
    order = sorted(range(len(w.labels)), key=w.sizes.__getitem__, reverse=True)[:top]
    return dict(schema=1, kind="roots_census", status="ok", truncated=w.truncated, bytes=total,
                module_bytes=w.phases[0], frame_bytes=w.phases[1], code_bytes=w.phases[2],
                instance_bytes=w.phases[3], unowned_bytes=w.phases[4], objects=sum(w.counts),
                gc_objects=gc_objects, roots=roots, owners=len(w.labels), bitmap_bytes=len(w.pages) * 8192,
                top=[[w.labels[i], w.sizes[i], w.counts[i]] for i in order], seconds=round(clock() - started, 3))


def safe_scan():
    """Never let the census thread die without a result: any failure reports ``unknown``."""
    try:
        return scan()
    except Exception as exc:  # noqa: BLE001 (MemoryError, a container resized by another thread)
        return dict(schema=1, kind="roots_census", status="unknown", error=type(exc).__name__)


def _probe_class():
    try:
        from . import census
    except ImportError:
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("loop_watch_census", Path(__file__).with_name("census.py"))
        if spec is None or spec.loader is None:
            raise
        census = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(census)
    return census.Probe


def _release(token):
    if vars(sys).get(SENTINEL) is token:
        vars(sys).pop(SENTINEL, None)


def start(home, *, probe_factory=None, sleep=time.sleep, argv=None, wall=time.time, recheck_s=RECHECK_S):
    """Arm when the home's arm file names this process kind inside its window. One scan per process."""
    if hasattr(sys, SENTINEL):
        return None  # armed and waiting, or already scanned (a plugin reload registers again)
    end, named = read_arm(home, now=wall())
    if not end:
        if named != "no arm file":
            log.warning("loop-watch: roots census not armed in pid %d: %s", os.getpid(), named)
        return None
    kind = process_kind(argv)
    if kind not in named:
        log.warning("loop-watch: roots census not armed in pid %d: process kind %r is not in the arm file's "
                    "kinds (default: gateway)", os.getpid(), kind)
        return None
    token = object()
    if vars(sys).setdefault(SENTINEL, token) is not token:
        return None  # another thread registered at the same moment: it owns the one probe

    def run():
        until, checked = end, wall()
        try:
            while not probe.used and wall() < until:
                sleep(1)
                probe.tick()
                if not probe.used and wall() - checked >= recheck_s:
                    checked = wall()
                    until, kinds_now = read_arm(home, now=checked)  # a rewritten file moves the window
                    if not until or kind not in kinds_now:
                        break  # removed, invalid, or no longer naming this process kind: disarm
            if not probe.used:
                _release(token)  # no scan happened: a later valid arm file may arm this process again
                probe.used = True  # this generation's opportunity is spent
                log.warning("loop-watch: roots census disarmed in pid %d with no request", os.getpid())
        finally:
            probe.close()

    try:
        probe = (probe_factory or _probe_class())(home, command=COMMAND, scanner=safe_scan, max_output=MAX_OUTPUT,
                                                  subdir=SUBDIR)
    except Exception:  # noqa: BLE001
        _release(token)
        log.warning("loop-watch: roots census could not arm its request directory")
        return None
    thread = threading.Thread(target=run, name="loop-watch-roots-census", daemon=True)
    try:
        thread.start()
    except Exception:  # noqa: BLE001 (no thread to wait: do not hold the claim)
        _release(token)
        probe.close()
        log.warning("loop-watch: roots census could not start its waiting thread")
        return None
    log.warning("loop-watch: roots census armed in pid %d (%s): waits for one roots-census-v1 request until "
                "epoch %d or until the arm file is removed", os.getpid(), kind, int(end))
    return thread
