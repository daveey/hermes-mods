"""Small maintenance bursts repay the collector's existing durable pacing debt.

This does not cache metadata or authorization. Admission is once per credential
per cycle, as for collector pagination, with a second debt bound on every hop.
"""
from .github_actions import GitHubActions, _Budget, _Failure


class MaintenanceGitHub(GitHubActions):
    def __init__(self, **kwargs):
        super().__init__(**dict(kwargs, max_cycle_requests=32, pace_requests=True,
                              quota_reserve=2500, max_hourly_requests=300,
                              precharge_pacing=True))
        self._admitted = set()

    def begin_cycle(self):
        super().begin_cycle()
        self._admitted.clear()

    def _request(self, credential, path, **kwargs):
        now = self.clock()
        budget = self._budgets.get(credential.key, _Budget())
        if credential.key not in self._admitted:
            if budget.next_at > now + 30:
                raise _Failure('pacing')
            self._admitted.add(credential.key)
        # Account for the imminent decrement in remaining when projecting debt.
        projected = _Budget(**vars(budget))
        if projected.remaining is not None:
            projected.remaining -= 1
        if max(now, budget.next_at) + self._request_spacing(projected, now) > now + 384:
            raise _Failure('pacing')
        return super()._request(credential, path, **kwargs)
