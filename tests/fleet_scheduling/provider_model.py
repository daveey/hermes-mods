"""Closed fake transport with role attribution independent of model identity."""
import json
import threading
from dataclasses import dataclass, field


@dataclass
class Attempt:
    role: str
    tick: int
    requests: int = 0
    answers: list[str] = field(default_factory=list)


class TwoRoleModel:
    def __init__(self, rate_limited_attempts):
        self.rate_limited_attempts = rate_limited_attempts
        self.attempts = []
        self.tick = 0
        self._lock = threading.Lock()

    def __call__(self, rec):
        from tests.e2e.core.kanban.test_kanban_rate_limit_review import REVIEW_MARK
        from tests.fakes.fake_llm_provider import ToolCall
        messages = rec["body"].get("messages", [])
        role = "review" if REVIEW_MARK in json.dumps(messages) else "impl"
        assert rec["body"].get("model") == "fake-model"
        with self._lock:
            last = self.attempts[-1] if self.attempts else None
            if last is None or (last.tick, last.role) != (self.tick, role):
                self.attempts.append(Attempt(role, self.tick))
            attempt = self.attempts[-1]
            attempt.requests += 1
            response = self._answer(role, messages)
            attempt.answers.append(response.name if isinstance(response, ToolCall) else type(response).__name__)
            return response

    def _answer(self, role, messages):
        from tests.fakes.fake_llm_provider import Error, Text, ToolCall
        if messages and messages[-1].get("role") == "tool":
            return Text("done")
        if role == "review":
            return ToolCall("kanban_complete", {"summary": "review: approved"})
        if len(self.by_role("impl")) <= self.rate_limited_attempts:
            return Error(status=429, message="Rate limit exceeded: quota for this key", retry_after=0)
        return ToolCall("kanban_request_review", {
            "summary": "implementation ready for review", "metadata": {"review_model": "class:deep"}})

    def by_role(self, role):
        return [attempt for attempt in self.attempts if attempt.role == role]
