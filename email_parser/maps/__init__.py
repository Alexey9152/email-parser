"""Google Maps providers (scraper backends).

Every provider implements ``search(query, limit) -> list[Restaurant]``.
"""

from __future__ import annotations

from ..config import Settings
from .base import MapsProvider

__all__ = ["MapsProvider", "build_provider"]


def build_provider(settings: Settings, name: str | None = None) -> MapsProvider:
    """Factory: pick a backend and fail early when credentials are missing."""
    provider = (name or settings.maps_provider or "playwright").lower()

    if provider == "apify":
        from .apify import ApifyProvider

        return ApifyProvider(
            token=settings.apify_token,
            actor=settings.apify_actor,
            timeout=settings.http_timeout * 60,
        )

    if provider == "outscraper":
        from .outscraper import OutscraperProvider

        return OutscraperProvider(api_key=settings.outscraper_key)

    if provider in {"playwright", "browser", "free"}:
        from .playwright_provider import PlaywrightMapsProvider

        return PlaywrightMapsProvider(headless=settings.headless)

    raise ValueError(
        f"Unknown MAPS_PROVIDER={provider!r}. Use one of: playwright, apify, outscraper."
    )
