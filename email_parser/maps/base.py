"""Provider protocol shared by Apify / Outscraper / Playwright backends."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Restaurant


class MapsProviderError(RuntimeError):
    """Raised when the backend cannot deliver results (bad key, captcha, ...)."""


@runtime_checkable
class MapsProvider(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[Restaurant]:
        """Return up to ``limit`` venues for a natural-language Maps query."""
        ...
