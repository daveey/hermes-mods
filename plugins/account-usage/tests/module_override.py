"""Replace one module without rolling back unrelated lazy imports."""
from contextlib import contextmanager
import sys


@contextmanager
def module_override(name, replacement):
    previous = {name: sys.modules[name]} if name in sys.modules else {}
    sys.modules[name] = replacement
    try:
        yield
    finally:
        sys.modules.pop(name, None)
        sys.modules.update(previous)
