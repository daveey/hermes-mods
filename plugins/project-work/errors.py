"""Explicit ingress rejections; environmental/native exceptions remain retryable."""


class RejectedScope(PermissionError):
    """Authenticated command is outside allowed scope or approval policy."""


class RejectedRequest(ValueError):
    """Command cannot become valid by retrying the same input."""


class MissingWorkflow(KeyError):
    """The authoritative ledger has no workflow with this identity."""
