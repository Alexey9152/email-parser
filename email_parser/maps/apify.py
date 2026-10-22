"""Apify backend (recommended for production volumes)."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

from ..models import Restaurant
from .base import MapsProviderError

log = logging.getLogger(__name__)

APIFY_API = "https://api.apify.com/v2"
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ApifyProvider:
    name = "apify"

    def __init__(
        self,
        token: str,
        actor: str = "compass/crawler-google-places",
        timeout: float = 900,
        poll_interval: float = 5,
    ):
        if not token:
            raise MapsProviderError(
                "APIFY_TOKEN is empty. Put it into .env or pass --provider playwright."
            )
        self.token = token
        self.actor = actor
        self.timeout = timeout
        self.poll_interval = poll_interval

    # -- protocol ----------------------------------------------------------
    def search(self, query: str, limit: int) -> list[Restaurant]:
        dataset_id = self._run_actor(query, max(1, limit))
        items = self._download_dataset(dataset_id)
        log.info("Apify returned %d raw items", len(items))
        return [item for item in (self._to_restaurant(i, query) for i in items) if item]

    # -- internals ---------------------------------------------------------
    def _run_actor(self, query: str, limit: int) -> str:
        actor_path = quote(self.actor.replace("/", "~"), safe="~")
        payload: dict[str, Any] = {
            "searchStringsArray": [query],
            "maxCrawledPlacesPerSearch": limit,
            "language": "en",
            "skipClosedPlaces": False,
            "scrapePlaceDetailPage": False,
            "includeWebResults": False,
        }
        with httpx.Client(timeout=60) as client:
            response = client.post(
                f"{APIFY_API}/acts/{actor_path}/runs",
                params={"token": self.token},
                json=payload,
            )
            if response.status_code >= 400:
                raise MapsProviderError(
                    f"Apify start failed ({response.status_code}): {response.text[:300]}"
                )
            run = response.json()["data"]
            run_id = run["id"]
            dataset_id = run["defaultDatasetId"]
            log.info("Apify run %s started, waiting for results...", run_id)

            deadline = time.time() + self.timeout
            while time.time() < deadline:
                status = client.get(
                    f"{APIFY_API}/actor-runs/{run_id}", params={"token": self.token}
                ).json()["data"]["status"]
                if status in TERMINAL_STATUSES:
                    if status != "SUCCEEDED":
                        raise MapsProviderError(f"Apify run {run_id} finished with {status}")
                    return dataset_id
                time.sleep(self.poll_interval)
        raise MapsProviderError(f"Apify run did not finish in {self.timeout:.0f}s")

    def _download_dataset(self, dataset_id: str) -> list[dict[str, Any]]:
        with httpx.Client(timeout=120) as client:
            response = client.get(
                f"{APIFY_API}/datasets/{dataset_id}/items",
                params={"token": self.token, "clean": "true", "format": "json"},
            )
            if response.status_code >= 400:
                raise MapsProviderError(
                    f"Apify dataset download failed ({response.status_code})"
                )
            return response.json()

    @staticmethod
    def _to_restaurant(item: dict[str, Any], query: str) -> Restaurant | None:
        title = (item.get("title") or item.get("name") or "").strip()
        if not title:
            return None
        website = (item.get("website") or item.get("site") or "").strip()
        maps_url = (item.get("url") or item.get("locationLink") or "").strip()
        if not maps_url:
            place_id = item.get("placeId") or item.get("place_id") or ""
            maps_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else ""
        phone = (
            item.get("phone")
            or item.get("phoneUnformatted")
            or item.get("phone_number")
            or ""
        )
        return Restaurant(
            title=title,
            address=(item.get("address") or item.get("fullAddress") or "").strip(),
            phone=str(phone).strip(),
            website=website,
            google_maps_url=maps_url,
            category=(item.get("categoryName") or item.get("category") or "").strip(),
            source_query=query,
        )
