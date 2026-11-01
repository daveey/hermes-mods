"""Isolated Kanban CLI model: create dedup includes done, show and comment readback."""
import json
from types import SimpleNamespace


class BoardRunner:
    def __init__(self):
        self.calls, self.tasks, self.keys, self.comments = [], {}, {}, {}
        self.drop_comments = False

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        verb = argv[2]
        if verb == "list":
            out = list(self.tasks.values())
        elif verb == "create":
            key = argv[argv.index("--idempotency-key") + 1]
            tid = self.keys.get(key)
            if not tid or self.tasks[tid]["status"] == "archived":
                tid = f"t_{len(self.tasks) + 1}"
                self.keys[key] = tid
                self.tasks[tid] = dict(id=tid, status="ready", body=kw["input"], idempotency_key=key)
                self.comments[tid] = []
            out = self.tasks[tid]
        elif verb == "show":
            tid = argv[3]
            out = dict(task=self.tasks[tid], comments=self.comments[tid])
        elif verb == "comment":
            if not self.drop_comments:
                self.comments[argv[3]].append(dict(body=argv[4]))
            out = {}
        else:
            raise AssertionError(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")
