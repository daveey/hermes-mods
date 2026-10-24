"""Injected read-only provider boundary; no ambient credentials or HTTP client.

Transport must honor timeout and allow_redirects=False, return decoded JSON in
HTTPResponse.body, and must not log Authorization. Credential.key is the stable
budget identity (e.g. installation), shared by aliases; token is never rendered.
ProviderResult partial rows are coverage, NOT evidence of a complete enumeration.
Only complete inventory results may participate in disappearance detection.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol


@dataclass(frozen=True)
class Credential:
    key: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class ProviderResult:
    rows: tuple[dict, ...]
    complete: bool
    error: str | None
    observed_at: float
    total_count: int | None


class Transport(Protocol):
    def __call__(self, method: str, url: str, *, headers: dict[str, str],
                 timeout: float, allow_redirects: bool) -> HTTPResponse: ...


class Provider(Protocol):
    def enumerate(self, scope: dict) -> ProviderResult: ...


CredentialResolver = Callable[[str], Credential]
