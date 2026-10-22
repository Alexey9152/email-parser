"""Small helpers: URL normalisation, domain parsing, geo maths."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import tldextract

# Bundled public-suffix snapshot: no network call at import time.
_extract_domain = tldextract.TLDExtract(suffix_list_urls=())


def clean_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def normalize_url(url: str) -> str:
    """Return an absolute http(s) URL, or '' when the input is unusable."""
    url = clean_whitespace(url)
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    # Google sometimes ships tracking wrappers as the "website" field.
    if "google.com/url" in parts.netloc + parts.path:
        return ""
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        pass  # keep www: some sites redirect poorly without it
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def registrable_domain(url: str) -> str:
    """example.co.uk for https://menu.example.co.uk/contacts."""
    url = normalize_url(url)
    if not url:
        return ""
    parts = urlsplit(url)
    ext = _extract_domain(parts.netloc)
    if not ext.domain or not ext.suffix:
        return parts.netloc.lower()
    return f"{ext.domain}.{ext.suffix}".lower()


def same_site(a: str, b: str) -> bool:
    da, db = registrable_domain(a), registrable_domain(b)
    return bool(da) and da == db


def city_from_query(query: str) -> str:
    """'restaurants in Ocean City, MD' -> 'Ocean City, MD'."""
    match = re.search(r"\b(?:in|near|around)\s+(.+)$", query or "", re.IGNORECASE)
    if match:
        return clean_whitespace(match.group(1))
    parts = [p.strip() for p in (query or "").split(",") if p.strip()]
    return parts[-1] if len(parts) > 1 else ""


def unique(items) -> list:
    seen: set = set()
    out = []
    for item in items:
        key = item if isinstance(item, (str, int, float, tuple)) else repr(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def slugify(value: str, max_length: int = 60) -> str:
    value = re.sub(r"[^\w\s-]", "", (value or "").lower(), flags=re.UNICODE)
    value = re.sub(r"[\s_-]+", "-", value).strip("-")
    return (value[:max_length].strip("-") or "output")


def ensure_parent(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# ------------------------------------------------------------------- geography
@dataclass(frozen=True, slots=True)
class Area:
    """Rectangular search area in WGS84 degrees."""

    south: float
    west: float
    north: float
    east: float
    label: str = ""

    def __post_init__(self) -> None:
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError("bbox latitudes must satisfy -90 <= south < north <= 90")
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError("bbox longitudes must satisfy -180 <= west < east <= 180")

    @property
    def center(self) -> tuple[float, float]:
        return (self.south + self.north) / 2, (self.west + self.east) / 2

    def contains(self, latitude: float, longitude: float) -> bool:
        return self.south <= latitude <= self.north and self.west <= longitude <= self.east

    def polygon(self) -> list[list[float]]:
        """Closed GeoJSON ring in [longitude, latitude] order (Apify requirement)."""
        return [
            [self.west, self.south],
            [self.east, self.south],
            [self.east, self.north],
            [self.west, self.north],
            [self.west, self.south],
        ]

    def geojson(self) -> dict:
        return {"type": "Polygon", "coordinates": [self.polygon()]}

    def as_text(self) -> str:
        return f"{self.south:.6f},{self.west:.6f},{self.north:.6f},{self.east:.6f}"

    def approximate_radius_km(self) -> float:
        lat_km = (self.north - self.south) * 111.0
        lng_km = (self.east - self.west) * 111.0 * math.cos(math.radians(self.center[0]))
        return round(max(lat_km, lng_km) / 2, 1)


def parse_bbox(value: str) -> Area | None:
    """Parse 'south,west,north,east' (min_lat,min_lng,max_lat,max_lng)."""
    if not value:
        return None
    parts = [p.strip() for p in value.replace(";", ",").split(",") if p.strip()]
    if len(parts) != 4:
        raise ValueError(
            "bbox must be four numbers: south,west,north,east "
            "(min_lat,min_lng,max_lat,max_lng)"
        )
    try:
        south, west, north, east = (float(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"bbox is not numeric: {value!r}") from exc
    return Area(south=south, west=west, north=north, east=east)


_COORD_PATTERNS = (
    re.compile(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)"),
    re.compile(r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)"),
)


def coords_from_maps_url(url: str) -> tuple[float, float] | None:
    """Google Maps links carry the point as !3d<lat>!4d<lng> or @<lat>,<lng>."""
    for pattern in _COORD_PATTERNS:
        match = pattern.search(url or "")
        if match:
            try:
                return float(match.group(1)), float(match.group(2))
            except ValueError:
                continue
    return None
