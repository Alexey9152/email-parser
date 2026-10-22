"""Apify backend (recommended for production volumes).

One actor run handles many category searches at once, and the search area can
be pinned with a GeoJSON polygon (`customGeolocation`), which is how the map
rectangle from the UI reaches Google Maps.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

from ..categories import per_query_limit
from ..models import Restaurant
from ..utils import Area, clean_whitespace
from .base import MapsProviderBase, MapsProviderError

log = logging.getLogger(__name__)

APIFY_API = "https://api.apify.com/v2"
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ApifyProvider(MapsProviderBase):
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
    def search(
        self, query: str, limit: int, area: Area | None = None
    ) -> list[Restaurant]:
        return self.search_many([query], limit, area)

    def search_many(
        self, queries: list[str], limit: int, area: Area | None = None
    ) -> list[Restaurant]:
        queries = [clean_whitespace(q) for q in queries if clean_whitespace(q)]
        if not queries or limit <= 0:
            return []
        per_query = per_query_limit(limit, len(queries))
        dataset_id = self._run_actor(queries, per_query, area)
        items = self._download_dataset(dataset_id)
        log.info(
            "Apify returned %d raw items for %d query(ies), %d per query",
            len(items),
            len(queries),
            per_query,
        )
        restaurants: list[Restaurant] = []
        for item in items:
            query = clean_whitespace(str(item.get("searchString") or "")) or queries[0]
            restaurant = self._to_restaurant(item, query)
            if restaurant:
                restaurants.append(restaurant)
        return restaurants

    # -- internals ---------------------------------------------------------
    def _run_actor(self, queries: list[str], per_query: int, area: Area | None) -> str:
        actor_path = quote(self.actor.replace("/", "~"), safe="~")
        payload: dict[str, Any] = {
            "searchStringsArray": queries,
            "maxCrawledPlacesPerSearch": max(1, per_query),
            "language": "en",
            "skipClosedPlaces": True,
            "scrapePlaceDetailPage": False,
            "includeWebResults": False,
        }
        if area is not None:
            payload["customGeolocation"] = area.geojson()
            log.info("Apify search area: %s", area.as_text())

        timeout = max(self.timeout, 90 * len(queries))
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
            log.info(
                "Apify run %s started (%d categories), waiting for results...",
                run_id,
                len(queries),
            )

            deadline = time.time() + timeout
            while time.time() < deadline:
                status = client.get(
                    f"{APIFY_API}/actor-runs/{run_id}", params={"token": self.token}
                ).json()["data"]["status"]
                if status in TERMINAL_STATUSES:
                    if status != "SUCCEEDED":
                        raise MapsProviderError(f"Apify run {run_id} finished with {status}")
                    return dataset_id
                time.sleep(self.poll_interval)
        raise MapsProviderError(f"Apify run did not finish in {timeout:.0f}s")

    def _download_dataset(self, dataset_id: str) -> list[dict[str, Any]]:
        with httpx.Client(timeout=180) as client:
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
        place_id = str(item.get("placeId") or item.get("place_id") or "").strip()
        if not maps_url:
            maps_url = (
                f"https://www.google.com/maps/place/?q=place_id:{place_id}"
                if place_id
                else ""
            )
        phone = (
            item.get("phone")
            or item.get("phoneUnformatted")
            or item.get("phone_number")
            or ""
        )
        location = item.get("location") or item.get("coordinates") or {}
        latitude = _as_float(
            location.get("lat") if isinstance(location, dict) else None,
            item.get("latitude"),
            item.get("lat"),
        )
        longitude = _as_float(
            location.get("lng") if isinstance(location, dict) else None,
            item.get("longitude"),
            item.get("lng"),
        )
        category = clean_whitespace(
            str(
                item.get("categoryName")
                or (item.get("categories") or [""])[0]
                or item.get("category")
                or ""
            )
        )
        return Restaurant(
            title=title,
            address=clean_whitespace(
                str(item.get("address") or item.get("fullAddress") or "")
            ),
            phone=str(phone).strip(),
            website=website,
            google_maps_url=maps_url,
            category=category,
            source_query=query,
            rating=_as_float(
                item.get("totalScore"),
                item.get("reviewsRating"),
                item.get("rating"),
                item.get("stars"),
            ),
            reviews=_as_int(
                item.get("reviewsCount"),
                item.get("userRatingsTotal"),
                item.get("reviews"),
            ),
            price=clean_whitespace(str(item.get("price") or item.get("priceLevel") or "")),
            latitude=latitude,
            longitude=longitude,
            place_id=place_id,
        )


def _as_float(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _as_int(*values: Any) -> int | None:
    number = _as_float(*values)
    return int(number) if number is not None else None
