"""Replay the incident fixtures through the REAL judge (aux model) and print verdicts.

Run under the Hermes runtime interpreter so the auxiliary client and the rotating Claude pool are available:
  RT=$(hermes --print-runtime-command | python3 -c 'import json,sys; print(json.load(sys.stdin)[0])')
  $RT -I -c "import sys,runpy; sys.path.insert(0,'$HOME/.hermes/hermes-agent'); import hermes_bootstrap; runpy.run_path('scripts/replay.py')"
Uses the live capability sheet of this machine (never written to disk) and the fixtures' card states.
"""
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ask_gate_replay", HERE / "__init__.py", submodule_search_locations=[str(HERE)])
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
FIX = json.loads((HERE / "tests/fixtures/session_20261021_103941.json").read_text())

tmp = Path(tempfile.mkdtemp())
gate._LEDGER.update(path=str(tmp / "l.sqlite"), obj=gate.core.Ledger(tmp / "l.sqlite"))
import os
os.environ["ASK_GATE_LEDGER"] = str(tmp / "l.sqlite")


def run(name, entry):
    cards = {cid: {"id": cid, "title": "", "status": st, "completed": None, "result": ""}
             for cid, st in entry.get("cards", {}).items()}
    gate.core.card_status = lambda ids, db_paths=None, _c=cards: {i: _c[i] for i in ids if i in _c}
    gate.LOG.set_users("replay", entry["users"])
    for l in entry.get("ledger", []):
        gate.ledger().add(l["topic"], l["answer"], session="old")
    asks = gate.core.find_asks(entry["reply"])
    tools = [{"tool": t["tool"], "args": t["args"], "result": t["result"], "t": 0} for t in entry["tools"]]
    t0 = time.time()
    v = gate.run_judge("replay", asks, tools, entry["reply"]) if asks else []
    print(f"== {name}  ({len(asks)} asks, {time.time() - t0:.1f}s)")
    for a, x in zip(asks, v):
        print(f"   [{x['verdict']}] {a[:110]!r}\n        why: {x.get('reason')}  how: {x.get('how')}")
    return v

for k in ("I0", "I1_I2", "I3", "I4"):
    run(k, FIX[k])
for k, e in {**FIX["good"], **FIX["borderline"]}.items():
    run("good/" + k, e)
