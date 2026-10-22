"""Provider base class shared by Apify / Outscraper / Playwright backends."""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from ..models import Restaurant
from ..utils import Area


class MapsProviderError(RuntimeError):
    """Raised when the backend cannot deliver results (bad key, captcha, ...)."""


@runtime_checkable
class MapsProvider(Protocol):
    name: str

    def search(
        self, query: str, limit: int, area: Area | None = None
    ) -> list[Restaurant]:
        """Return up to ``limit`` venues for one Maps query."""
        ...

    def search_many(
        self, queries: list[str], limit: int, area: Area | None = None
    ) -> list[Restaurant]:
        """Return up to ``limit`` venues in total for several queries."""
        ...


class MapsProviderBase:
    """Default behaviour: run the queries one by one."""

    name = "base"

    def search(
        self, query: str, limit: int, area: Area | None = None
    ) -> list[Restaurant]:  # pragma: no cover - abstract
        raise NotImplementedError

    def search_many(
        self, queries: list[str], limit: int, area: Area | None = None
    ) -> list[Restaurant]:
        queries = [q for q in queries if q.strip()] or [""]
        per_query = max(5, math.ceil(limit / len(queries)))
        collected: list[Restaurant] = []
        for query in queries:
            if len(collected) >= limit:
                break
            collected.extend(self.search(query, per_query, area))
        return collected[:limit]
