"""Plain data containers shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Status values written to the final report.
STATUS_SITE = "Found on site"
STATUS_FB = "Found on FB"
STATUS_SEARCH = "Found via web search"
STATUS_JS = "Found via JS render"
STATUS_GUESS = "Guessed (unverified)"
STATUS_NOT_FOUND = "Not found"


@dataclass(slots=True)
class Restaurant:
    """A venue row coming out of Google Maps."""

    title: str
    address: str = ""
    phone: str = ""
    website: str = ""
    google_maps_url: str = ""
    category: str = ""
    source_query: str = ""
    facebook_url: str = ""
    rating: float | None = None
    reviews: int | None = None
    price: str = ""
    latitude: float | None = None
    longitude: float | None = None
    place_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Restaurant":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed and v is not None})


@dataclass(slots=True)
class ContactResult:
    """Final per-venue row."""

    title: str
    website: str = ""
    emails: list[str] = field(default_factory=list)
    facebook_url: str = ""
    phone: str = ""
    address: str = ""
    status: str = STATUS_NOT_FOUND
    sources: list[str] = field(default_factory=list)
    google_maps_url: str = ""
    note: str = ""
    category: str = ""
    rating: float | None = None
    reviews: int | None = None
    price: str = ""
    latitude: float | None = None
    longitude: float | None = None
    place_id: str = ""
    source_query: str = ""

    @property
    def primary_email(self) -> str:
        return self.emails[0] if self.emails else ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["emails"] = "; ".join(self.emails)
        data["sources"] = "; ".join(self.sources)
        return data
