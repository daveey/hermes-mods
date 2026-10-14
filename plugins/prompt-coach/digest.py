#!/usr/bin/env python3
"""Print the last N days of prompt-coach activity as JSON (stdlib only; used by the weekly digest cron)."""
import importlib.util
import json
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("_pc_core_digest", Path(__file__).resolve().parent / "core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
days = float(sys.argv[1]) if len(sys.argv) > 1 else 7
print(json.dumps(core.stats(since_ts=time.time() - days * 86400), indent=1))
