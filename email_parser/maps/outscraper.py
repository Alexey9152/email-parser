"""Outscraper backend (paid, generous free tier)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..models import Restaurant
from ..utils import Area, clean_whitespace
from .base import MapsProviderBase, MapsProviderError
from .social import facebook_from_payload

log = logging.getLogger(__name__)

OUTSCRAPER_API = "https://api.outscraper.com/maps/search-v3"


class OutscraperProvider(MapsProviderBase):
    name = "outscraper"

    def __init__(self, api_key: str, timeout: float = 300):
        if not api_key:
            raise MapsProviderError(
                "OUTSCRAPER_API_KEY is empty. Put it into .env or pass --provider playwright."
            )
        self.api_key = api_key
        self.timeout = timeout

    def search(
        self, query: str, limit: int, area: Area | None = None
    ) -> list[Restaurant]:
        if area is not None:
            log.warning("Outscraper backend ignores the map area (provider limitation)")
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                OUTSCRAPER_API,
                params={
                    "query": query,
                    "limit": max(1, limit),
                    "language": "en",
                    "async": "false",
                },
                headers={"X-API-KEY": self.api_key},
            )
        if response.status_code >= 400:
            raise MapsProviderError(
                f"Outscraper failed ({response.status_code}): {response.text[:300]}"
            )
        payload = response.json()
        if str(payload.get("status", "")).lower() not in {"success", "ok"}:
            raise MapsProviderError(f"Outscraper status: {payload.get('status')!r}")
        batches = payload.get("data") or []
        items: list[dict[str, Any]] = [item for batch in batches for item in (batch or [])]
        log.info("Outscraper returned %d raw items", len(items))
        return [item for item in (self._to_restaurant(i, query) for i in items) if item]

    @staticmethod
    def _to_restaurant(item: dict[str, Any], query: str) -> Restaurant | None:
        title = (item.get("name") or item.get("title") or "").strip()
        if not title:
            return None
        return Restaurant(
            title=title,
            address=clean_whitespace(
                str(item.get("full_address") or item.get("address") or "")
            ),
            phone=str(item.get("phone") or "").strip(),
            website=(item.get("site") or item.get("website") or "").strip(),
            facebook_url=facebook_from_payload(item),
            google_maps_url=(
                item.get("location_link") or item.get("google_maps_url") or ""
            ).strip(),
            category=clean_whitespace(str(item.get("type") or item.get("category") or "")),
            source_query=query,
            rating=_to_float(item.get("rating")),
            reviews=_to_int(item.get("reviews") or item.get("reviews_count")),
            price=clean_whitespace(str(item.get("price") or "")),
            latitude=_to_float(item.get("latitude")),
            longitude=_to_float(item.get("longitude")),
            place_id=str(item.get("place_id") or ""),
        )


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None
