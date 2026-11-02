"""Explicit ingress rejections; environmental/native exceptions remain retryable."""


class RejectedScope(PermissionError):
    """Authenticated command is outside allowed scope or approval policy."""


class RejectedRequest(ValueError):
    """Command cannot become valid by retrying the same input."""


class MissingWorkflow(KeyError):
    """The authoritative ledger has no workflow with this identity."""


class NativeCreateRefused(RuntimeError):
    """Native kanban_create returned a structured refusal AND a fresh
    archived-inclusive lookup proved no card exists for the idempotency key.

    Any weaker evidence (no code, a non-dict result, a failed lookup) stays an
    ambiguous plain RuntimeError.
    """

    def __init__(self, code, error, idempotency_key):
        super().__init__(f'Native kanban_create refused ({code}): {error}')
        self.code, self.error, self.idempotency_key = code, error, idempotency_key
