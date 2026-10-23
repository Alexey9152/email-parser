"""Plain data containers shared by every stage of the pipeline."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .utils import registrable_domain

# Status values written to the final report.
STATUS_SITE = "Found on site"
STATUS_FB = "Found on FB"
STATUS_SEARCH = "Found via web search"
STATUS_JS = "Found via JS render"
STATUS_OSM = "Found on OpenStreetMap"
STATUS_GUESS = "Guessed (unverified)"
STATUS_NOT_FOUND = "Not found"

# Machine-readable origin of a single e-mail address.  They are stored per
# address (``ContactResult.email_sources``) so the best address can be picked
# and the application letter can say *where* it was found.
SOURCE_SITE = "website"
SOURCE_SITE_JS = "website_js"
SOURCE_FB_PAGE = "facebook_page"
SOURCE_FB_SNIPPET = "facebook_snippet"
SOURCE_SEARCH = "web_search"
SOURCE_OSM = "osm"
SOURCE_GUESS = "guess"

SOURCE_LABELS = {
    SOURCE_SITE: "сайт",
    SOURCE_SITE_JS: "сайт (после рендера)",
    SOURCE_FB_PAGE: "Facebook (страница)",
    SOURCE_FB_SNIPPET: "Facebook (поиск)",
    SOURCE_SEARCH: "поиск в интернете",
    SOURCE_OSM: "OpenStreetMap",
    SOURCE_GUESS: "догадка по MX",
}

SOURCE_STATUS = {
    SOURCE_SITE: STATUS_SITE,
    SOURCE_SITE_JS: STATUS_JS,
    SOURCE_FB_PAGE: STATUS_FB,
    SOURCE_FB_SNIPPET: STATUS_FB,
    SOURCE_SEARCH: STATUS_SEARCH,
    SOURCE_OSM: STATUS_OSM,
    SOURCE_GUESS: STATUS_GUESS,
}

# Where an address came from is useful, but the local part matters more:
# manager@ / hiring@ / hr@ is exactly what a Work & Travel application needs.
SOURCE_BOOST = {
    SOURCE_FB_PAGE: 20,
    SOURCE_FB_SNIPPET: 15,
    SOURCE_OSM: 12,
    SOURCE_SITE: 10,
    SOURCE_SITE_JS: 8,
    SOURCE_SEARCH: 5,
    SOURCE_GUESS: -200,
}

ROLE_SCORES = {
    # Hiring / management: the primary target.
    "hiring": 110,
    "hr": 105,
    "recruiting": 100,
    "recruitment": 100,
    "manager": 100,
    "managers": 100,
    "owner": 95,
    "management": 90,
    "jobs": 100,
    "job": 95,
    "careers": 100,
    "career": 95,
    "employment": 95,
    "apply": 90,
    "application": 85,
    "resume": 85,
    "staffing": 85,
    # General inboxes: acceptable fallback.
    "info": 70,
    "contact": 68,
    "office": 65,
    "admin": 65,
    "hello": 60,
    "chef": 60,
    "hi": 55,
    "general": 55,
    "frontdesk": 50,
    "reception": 50,
    "team": 50,
    "mail": 50,
    "email": 50,
    # Departments that rarely answer a job application.
    "reservations": 30,
    "booking": 30,
    "bookings": 30,
    "events": 30,
    "sales": 20,
    "support": 20,
    "orders": 15,
    "order": 15,
    "marketing": 15,
    "press": 10,
    "media": 10,
}

HIRING_KEYWORDS = frozenset(
    {
        "hiring", "hr", "recruiting", "recruitment", "manager", "managers",
        "management", "jobs", "job", "careers", "career", "employment",
        "owner", "apply", "application", "resume", "staffing",
    }
)

FREE_MAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com",
        "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
        "mail.com", "gmx.com", "gmx.net", "proton.me", "protonmail.com",
        "mail.ru", "yandex.ru", "yandex.com", "bk.ru", "list.ru",
    }
)

# Addresses that technically exist but are useless for a human reply.
LOW_VALUE_LOCALS = frozenset(
    {
        "webmaster", "hostmaster", "postmaster", "abuse", "privacy", "legal",
        "billing", "accounts", "accounting", "wordpress", "wix", "sentry",
        "unsubscribe", "newsletter",
    }
)


def email_domain(email: str) -> str:
    return (email or "").rpartition("@")[2].strip().lower()


def _local(email: str) -> str:
    return (email or "").rpartition("@")[0].strip().lower()


def role_score(email: str) -> int:
    """How likely is this mailbox to reach a manager / hiring person?

    ``manager``, ``hiring``, ``hr``, ``jobs`` and ``careers`` score highest;
    ``info`` / ``contact`` are good fallbacks; ``reservations`` and
    ``marketing`` are deliberately lower.
    """
    local = _local(email)
    if not local:
        return 0
    exact = ROLE_SCORES.get(local)
    if exact is not None:
        return exact + 40

    best = 0
    tokens = [token for token in re.split(r"[^a-z0-9]+", local) if token]
    for token in tokens:
        value = ROLE_SCORES.get(token)
        if value is not None:
            best = max(best, value + 20)
    # Glued forms such as "hrmanager" or "frontdeskoc".
    for keyword, value in ROLE_SCORES.items():
        if len(keyword) >= 3 and keyword in local:
            best = max(best, value + 5)
    return best


def is_hiring_email(email: str) -> bool:
    """True when the address looks like a manager/hiring inbox."""
    local = _local(email)
    if not local:
        return False
    tokens = {token for token in re.split(r"[^a-z0-9]+", local) if token}
    if tokens & HIRING_KEYWORDS:
        return True
    # "hrmanager", "hiringoc" and similar glued local parts.
    return any(
        keyword in local for keyword in HIRING_KEYWORDS if len(keyword) >= 4
    )


def score_email(email: str, source: str = "", website: str = "") -> int:
    """Rank one address for a Work & Travel application."""
    if not email or "@" not in email:
        return -10_000

    score = role_score(email)
    score += SOURCE_BOOST.get(source, 0)

    domain = email_domain(email)
    local = _local(email)
    if website and domain:
        if registrable_domain(f"https://{domain}") == registrable_domain(website):
            score += 14
    if domain in FREE_MAIL_DOMAINS:
        # A personal Gmail on a Facebook page is often the manager's own inbox.
        score += 6
    if local in LOW_VALUE_LOCALS:
        score -= 100
    if source == SOURCE_GUESS:
        score -= 200
    if 0 < len(local) <= 20:
        score += 2
    return score


def rank_emails(
    emails: list[str],
    sources: dict[str, str] | None = None,
    website: str = "",
) -> list[str]:
    """Return unique addresses ordered best-first for a job application."""
    sources = sources or {}
    seen: set[str] = set()
    indexed: list[tuple[int, str]] = []
    for index, raw in enumerate(emails or []):
        email = (raw or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        indexed.append((index, email))

    indexed.sort(
        key=lambda pair: (
            -score_email(pair[1], sources.get(pair[1], ""), website),
            pair[0],
        )
    )
    return [email for _index, email in indexed]


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
    email: str = ""
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
    # email -> SOURCE_* constant; populated as soon as an address is found.
    email_sources: dict[str, str] = field(default_factory=dict)
    # Set when an existing report is loaded again; keeps the address the user
    # already chose in Excel instead of silently re-ranking it.
    primary_email_override: str = ""

    @property
    def ranked_emails(self) -> list[str]:
        return rank_emails(self.emails, self.email_sources, self.website)

    @property
    def primary_email(self) -> str:
        ranked = self.ranked_emails
        override = (self.primary_email_override or "").strip().lower()
        if override and override in ranked:
            return override
        return ranked[0] if ranked else ""

    @property
    def primary_email_source(self) -> str:
        primary = self.primary_email
        return self.email_sources.get(primary, "") if primary else ""

    @property
    def primary_email_source_label(self) -> str:
        return SOURCE_LABELS.get(self.primary_email_source, self.primary_email_source)

    @property
    def primary_email_source_url(self) -> str:
        source = self.primary_email_source
        if source in {SOURCE_FB_PAGE, SOURCE_FB_SNIPPET}:
            return self.facebook_url
        if source in {SOURCE_SITE, SOURCE_SITE_JS}:
            return self.website
        if source == SOURCE_OSM:
            return self.google_maps_url or self.website or self.facebook_url
        if source == SOURCE_SEARCH:
            return self.google_maps_url or self.website or self.facebook_url
        return ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["emails"] = "; ".join(self.emails)
        data["sources"] = "; ".join(self.sources)
        data["primary_email"] = self.primary_email
        data["primary_email_source"] = self.primary_email_source
        data["primary_email_source_label"] = self.primary_email_source_label
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContactResult":
        """Inverse of :meth:`to_dict` for JSON round-trips."""
        allowed = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        payload: dict[str, Any] = {}
        for key in allowed:
            if key not in data:
                continue
            value = data[key]
            if value is None:
                continue
            payload[key] = value
        emails = payload.get("emails")
        if isinstance(emails, str):
            payload["emails"] = [
                item.strip().lower()
                for item in re.split(r"\s*[;,]\s*", emails)
                if item.strip()
            ]
        sources = payload.get("sources")
        if isinstance(sources, str):
            payload["sources"] = [
                item.strip() for item in sources.split(";") if item.strip()
            ]
        if not isinstance(payload.get("email_sources"), dict):
            payload["email_sources"] = {}
        return cls(**payload)
