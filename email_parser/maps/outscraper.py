"""Outscraper backend (paid, generous free tier)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..models import Restaurant
from .base import MapsProviderError

log = logging.getLogger(__name__)

OUTSCRAPER_API = "https://api.outscraper.com/maps/search-v3"


class OutscraperProvider:
    name = "outscraper"

    def __init__(self, api_key: str, timeout: float = 300):
        if not api_key:
            raise MapsProviderError(
                "OUTSCRAPER_API_KEY is empty. Put it into .env or pass --provider playwright."
            )
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, limit: int) -> list[Restaurant]:
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
            address=(item.get("full_address") or item.get("address") or "").strip(),
            phone=str(item.get("phone") or "").strip(),
            website=(item.get("site") or item.get("website") or "").strip(),
            google_maps_url=(
                item.get("location_link") or item.get("google_maps_url") or ""
            ).strip(),
            category=(item.get("type") or item.get("category") or "").strip(),
            source_query=query,
        )
