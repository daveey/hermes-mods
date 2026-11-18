"""Private stdlib-only subprocess entrypoint for bounded slots database reads."""
import importlib.util
import json
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    name = "_throughput_slots_worker"
    spec = importlib.util.spec_from_file_location(name, root / "__init__.py",
                                                 submodule_search_locations=[str(root)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    from importlib import import_module
    slots = import_module(name + ".slots")
    print(json.dumps(slots.collect(json.load(sys.stdin)), allow_nan=False))


if __name__ == "__main__":
    main()
