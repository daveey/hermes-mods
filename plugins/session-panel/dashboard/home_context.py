"""Immutable explicit panel home identity. Not an authorization authority.

A future lifecycle controller supplies this context. Constructing it does not
grant permission, mutate process environment, or select a browser profile.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path


class UnsupportedHomeAdapter(Exception):
    """A reader scope has no explicit-home adapter; do not fall back to ambient home."""

    def __init__(self, scope: str, reason: str = ''):
        self.scope = scope
        super().__init__(reason or f'explicit-home adapter unavailable for {scope}')


def _require_generation(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f'{name} generation is required')
    return value


@dataclass(frozen=True)
class PanelHomeContext:
    """Canonical resolved home plus opaque lifecycle and authorization generations."""

    home: Path
    lifecycle_generation: str
    authorization_generation: str

    @classmethod
    def from_authorized_home(
        cls,
        home,
        *,
        lifecycle_generation: str,
        authorization_generation: str,
    ) -> 'PanelHomeContext':
        life = _require_generation('lifecycle', lifecycle_generation)
        auth = _require_generation('authorization', authorization_generation)
        path = Path(home)
        try:
            canonical = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError('panel home canonicalization failed') from exc
        if not canonical.is_dir():
            raise ValueError('panel home canonicalization failed')
        return cls(home=canonical, lifecycle_generation=life, authorization_generation=auth)

    @property
    def identity(self) -> tuple[str, str, str]:
        return (os.path.normcase(str(self.home)), self.lifecycle_generation, self.authorization_generation)

    def namespace(self) -> str:
        return hashlib.sha256('\0'.join(self.identity).encode('utf-8')).hexdigest()
