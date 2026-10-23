"""Free venue discovery through OpenStreetMap (Overpass + Nominatim).

OpenStreetMap needs no API key and often contains the venue's website, phone,
Facebook page and even an e-mail address.  Coverage is smaller than Google
Maps, but it is a reliable zero-cost replacement for Apify/Outscraper.

The provider runs two Overpass queries per area tile (food/drink amenities and
food/drink shops), retries transient Overpass errors and falls back to a second
public Overpass instance.  A city name without ``--bbox`` is geocoded through
Nominatim first.
"""

from __future__ import annotations

import logging
import math
import time
from urllib.parse import quote_plus

import httpx

from ..categories import resolve_location
from ..extractors import clean_facebook_url, extract_emails
from ..models import Restaurant
from ..utils import Area, clean_whitespace, normalize_url, unique
from .base import MapsProviderBase, MapsProviderError

log = logging.getLogger(__name__)

USER_AGENT = (
    "email-parser/1.0 (Work & Travel venue research; "
    "contact: loslesha0@gmail.com)"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
)

AMENITY_REGEX = (
    "restaurant|cafe|bar|pub|fast_food|food_court|ice_cream|biergarten"
)
SHOP_REGEX = (
    "bakery|confectionery|deli|seafood|coffee|tea|wine|alcohol|chocolate|pastry"
)
AMENITY_LABELS = {
    "restaurant": "restaurant",
    "cafe": "cafe",
    "bar": "bar",
    "pub": "pub",
    "fast_food": "fast food",
    "food_court": "food court",
    "ice_cream": "ice cream",
    "biergarten": "beer garden",
}
SHOP_LABELS = {
    "bakery": "bakery",
    "confectionery": "confectionery",
    "deli": "deli",
    "seafood": "seafood shop",
    "coffee": "coffee shop",
    "tea": "tea shop",
    "wine": "wine shop",
    "alcohol": "alcohol shop",
    "chocolate": "chocolate shop",
    "pastry": "pastry shop",
}

# Overpass can handle a city, but large rectangles time out.  Split big areas
# into at most 3x3 tiles; each tile is still smaller than this span when possible.
MAX_TILE_SPAN = 0.35
MAX_TILES_PER_AXIS = 3


class OsmProvider(MapsProviderBase):
    """Collect food/drink venues from OpenStreetMap within a bbox."""

    name = "osm"

    def __init__(self, timeout: float = 120.0):
        self.timeout = max(30.0, float(timeout))

    # -- protocol ----------------------------------------------------------
    def search(
        self, query: str, limit: int, area: Area | None = None
    ) -> list[Restaurant]:
        return self.search_many([query], limit, area)

    def search_many(
        self, queries: list[str], limit: int, area: Area | None = None
    ) -> list[Restaurant]:
        limit = max(1, int(limit))
        queries = [clean_whitespace(q) for q in queries if clean_whitespace(q)]
        if area is None:
            # Every category query ends with the same "… in <location>" suffix.
            area = self._geocode(queries[0] if queries else "")
        if area is None:  # pragma: no cover - _geocode raises instead
            raise MapsProviderError("OpenStreetMap needs a location or a map area")

        out_limit = max(200, min(2500, limit * 5))
        elements: list[dict] = []
        seen: set[tuple[str, object]] = set()
        failed = 0

        for tile in self._tiles(area):
            for query in (
                self._query("amenity", AMENITY_REGEX, tile, out_limit),
                self._query("shop", SHOP_REGEX, tile, out_limit),
            ):
                try:
                    batch = self._overpass(query)
                except MapsProviderError as exc:
                    failed += 1
                    log.warning("Overpass query failed for %s: %s", tile.as_text(), exc)
                    continue
                for element in batch:
                    key = (str(element.get("type") or ""), element.get("id"))
                    if not key[1] or key in seen:
                        continue
                    seen.add(key)
                    elements.append(element)

        if not elements and failed:
            raise MapsProviderError(
                "OpenStreetMap/Overpass is unavailable right now. "
                "Try again later, choose --provider playwright, or use --bbox."
            )

        # The provider scans a bbox, not a text query; a category name such as
        # "restaurant" would be misleading in the report's Query column.
        source_query = "OpenStreetMap"
        items: list[Restaurant] = []
        for element in elements:
            item = self._to_restaurant(element, source_query)
            if item is not None:
                items.append(item)

        # If the area contains more venues than the requested limit, keep the
        # ones nearest the centre instead of an arbitrary Overpass order.
        center = area.center
        items.sort(key=lambda item: (self._distance(item, center), item.title.lower()))
        log.info(
            "OpenStreetMap: %d venue(s) from %d Overpass element(s)",
            min(len(items), limit),
            len(elements),
        )
        return items[:limit]

    # -- Overpass ----------------------------------------------------------
    @staticmethod
    def _query(tag: str, regex: str, area: Area, out_limit: int) -> str:
        bbox = (
            f"{area.south:.6f},{area.west:.6f},"
            f"{area.north:.6f},{area.east:.6f}"
        )
        return (
            f'[out:json][timeout:90];'
            f'nwr["{tag}"~"^({regex})$"]({bbox});'
            f"out center tags {out_limit};"
        )

    def _overpass(self, query: str) -> list[dict]:
        last_error = ""
        for endpoint in OVERPASS_ENDPOINTS:
            for attempt in range(1, 3):
                try:
                    response = httpx.post(
                        endpoint,
                        data={"data": query},
                        headers=HEADERS,
                        timeout=self.timeout,
                    )
                except httpx.HTTPError as exc:
                    last_error = f"{endpoint}: {exc}"
                    log.debug("Overpass transport error (%s): %s", endpoint, exc)
                    time.sleep(min(8, 2 * attempt))
                    continue

                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        last_error = f"{endpoint}: invalid JSON"
                        log.debug("Overpass invalid JSON from %s: %s", endpoint, exc)
                        continue
                    elements = payload.get("elements") or []
                    log.debug("Overpass %s returned %d elements", endpoint, len(elements))
                    return elements

                last_error = (
                    f"{endpoint}: HTTP {response.status_code} "
                    f"{response.text[:120]}"
                )
                log.debug("Overpass %s -> HTTP %s", endpoint, response.status_code)
                if response.status_code in {429, 500, 502, 503, 504}:
                    time.sleep(min(8, 2 * attempt))
                    continue
                break

        raise MapsProviderError(f"Overpass request failed ({last_error})")

    # -- Nominatim ---------------------------------------------------------
    def _geocode(self, query: str) -> Area:
        place = resolve_location(query)
        if not place:
            raise MapsProviderError(
                "OpenStreetMap needs a city/location or a map rectangle "
                "(--bbox / the UI area selection)."
            )

        last_error = ""
        for attempt in range(1, 3):
            try:
                response = httpx.get(
                    NOMINATIM_URL,
                    params={
                        "q": place,
                        "format": "jsonv2",
                        "limit": 1,
                        "addressdetails": 0,
                    },
                    headers=HEADERS,
                    timeout=30,
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                log.debug("Nominatim transport error: %s", exc)
                time.sleep(2 * attempt)
                continue

            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                time.sleep(2 * attempt)
                continue

            try:
                data = response.json()
            except ValueError:
                last_error = "invalid JSON"
                time.sleep(2 * attempt)
                continue

            if not data:
                break
            bbox = data[0].get("boundingbox") or []
            if len(bbox) != 4:
                break
            try:
                south, north, west, east = (float(value) for value in bbox)
            except (TypeError, ValueError):
                break
            label = clean_whitespace(
                str(data[0].get("display_name") or place)
            )
            log.info("Geocoded %r -> %s", place, label)
            return Area(
                south=south,
                west=west,
                north=north,
                east=east,
                label=label,
            )

        raise MapsProviderError(
            f"Could not geocode {place!r} with Nominatim "
            f"({last_error or 'no result'}). Pass a map area with --bbox "
            "or draw one in the UI."
        )

    # -- tiles / parsing ---------------------------------------------------
    def _tiles(self, area: Area) -> list[Area]:
        span = max(area.north - area.south, area.east - area.west)
        parts = max(
            1,
            min(MAX_TILES_PER_AXIS, math.ceil(span / MAX_TILE_SPAN)),
        )
        lat_step = (area.north - area.south) / parts
        lng_step = (area.east - area.west) / parts
        tiles: list[Area] = []
        for row in range(parts):
            for column in range(parts):
                tiles.append(
                    Area(
                        south=area.south + row * lat_step,
                        west=area.west + column * lng_step,
                        north=area.south + (row + 1) * lat_step,
                        east=area.west + (column + 1) * lng_step,
                        label=area.label,
                    )
                )
        return tiles

    def _to_restaurant(self, element: dict, source_query: str) -> Restaurant | None:
        tags = element.get("tags") or {}
        name = clean_whitespace(
            str(
                tags.get("name:en")
                or tags.get("name")
                or tags.get("brand")
                or tags.get("operator")
                or ""
            )
        )
        if not name:
            return None

        center = element.get("center") or {}
        latitude = element.get("lat", center.get("lat"))
        longitude = element.get("lon", center.get("lon"))
        try:
            latitude = float(latitude) if latitude is not None else None
            longitude = float(longitude) if longitude is not None else None
        except (TypeError, ValueError):
            latitude = longitude = None

        website = normalize_url(
            self._first(tags, "website", "contact:website", "url", "brand:website")
        )
        facebook = self._first(
            tags, "contact:facebook", "facebook", "brand:facebook", "operator:facebook"
        )
        facebook = clean_facebook_url(facebook)
        if not facebook and website:
            facebook = clean_facebook_url(website)

        address = self._address(tags)
        phone = self._first(
            tags, "phone", "contact:phone", "contact:mobile", "mobile"
        ).replace(";", ", ")
        emails = extract_emails(
            str(tags.get("email") or ""),
            str(tags.get("contact:email") or ""),
            str(tags.get("operator:email") or ""),
        )
        category = self._category(tags)
        maps_query = clean_whitespace(f"{name} {address}") or name
        google_maps_url = (
            "https://www.google.com/maps/search/?api=1&query="
            + quote_plus(maps_query)
        )

        return Restaurant(
            title=name,
            address=address,
            phone=phone,
            website=website,
            google_maps_url=google_maps_url,
            category=category,
            source_query=source_query or "OpenStreetMap",
            facebook_url=facebook,
            email="; ".join(emails),
            latitude=latitude,
            longitude=longitude,
            place_id=f"{element.get('type') or ''}/{element.get('id') or ''}",
        )

    @staticmethod
    def _first(tags: dict, *keys: str) -> str:
        for key in keys:
            value = clean_whitespace(str(tags.get(key) or ""))
            if value:
                return value
        return ""

    @staticmethod
    def _address(tags: dict) -> str:
        full = clean_whitespace(str(tags.get("addr:full") or ""))
        if full:
            return full
        street = clean_whitespace(
            " ".join(
                part
                for part in (
                    clean_whitespace(str(tags.get("addr:housenumber") or "")),
                    clean_whitespace(str(tags.get("addr:street") or "")),
                )
                if part
            )
        )
        parts = [
            street,
            clean_whitespace(str(tags.get("addr:place") or tags.get("addr:suburb") or "")),
            clean_whitespace(str(tags.get("addr:city") or "")),
            clean_whitespace(str(tags.get("addr:state") or "")),
            clean_whitespace(str(tags.get("addr:postcode") or "")),
        ]
        return ", ".join(part for part in parts if part)

    @staticmethod
    def _category(tags: dict) -> str:
        labels: list[str] = []
        amenity = str(tags.get("amenity") or "").strip()
        shop = str(tags.get("shop") or "").strip()
        if amenity:
            labels.append(AMENITY_LABELS.get(amenity, amenity.replace("_", " ")))
        if shop:
            labels.append(SHOP_LABELS.get(shop, shop.replace("_", " ")))
        cuisine = clean_whitespace(str(tags.get("cuisine") or ""))
        if cuisine:
            labels.append(cuisine.replace(";", ", ").replace("_", " "))
        return ", ".join(unique(labels))

    @staticmethod
    def _distance(item: Restaurant, center: tuple[float, float]) -> float:
        if item.latitude is None or item.longitude is None:
            return 1e9
        return (item.latitude - center[0]) ** 2 + (item.longitude - center[1]) ** 2
