"""E-mail discovery through search engines (Facebook pages + general web).

Facebook requires login for profile pages, so the practical approach is reading
indexed snippets: restaurants often publish "write us at ...@..." in posts or in
the page intro, and search engines keep a copy.

Backends are key-free by default (DuckDuckGo -> Bing); SerpAPI is used first
only when a key is configured.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

from .extractors import clean_facebook_url, extract_emails
from .serp import SearchHit, WebSearch
from .utils import clean_whitespace, unique

log = logging.getLogger(__name__)

# Addresses hosted on directories/aggregators are never the venue's own inbox.
BLOCKED_EMAIL_DOMAINS = (
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "yelp.com", "tripadvisor.com", "google.com", "bing.com",
    "duckduckgo.com", "yellowpages.com", "doordash.com", "ubereats.com",
    "grubhub.com", "restaurantji.com", "allmenus.com", "menupix.com",
    "opentable.com", "mapquest.com", "zomato.com",
)
USELESS_LOCAL_PARTS = {"noreply", "no-reply", "donotreply", "do-not-reply", "postmaster"}
TITLE_STOPWORDS = {
    "the", "and", "for", "inc", "llc", "ltd", "co", "restaurant", "restaurants",
    "cafe", "bar", "grill", "kitchen", "pizzeria", "pizza", "of", "at",
}


@dataclass(slots=True)
class SocialResult:
    emails: list[str] = field(default_factory=list)
    facebook_url: str = ""
    source: str = ""  # "facebook" | "web"
    backend: str = ""
    queries: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def found(self) -> bool:
        return bool(self.emails)


class SocialEmailFinder:
    """Find a venue's address in Facebook snippets, then in the open web."""

    def __init__(self, web_search: WebSearch | None):
        self.engine = web_search

    @property
    def enabled(self) -> bool:
        return bool(self.engine and self.engine.enabled)

    def find(
        self,
        title: str,
        city: str = "",
        facebook_url: str = "",
        max_queries: int = 3,
    ) -> SocialResult:
        result = SocialResult(facebook_url=clean_facebook_url(facebook_url))
        if not self.enabled:
            result.error = "no search backend available"
            return result
        if not title or max_queries <= 0:
            result.error = "empty title or no query budget"
            return result

        for kind, query in self._queries(title, city):
            if len(result.queries) >= max_queries or not self.enabled:
                break
            result.queries.append(query)
            hits, backend = self.engine.search(query)
            if not hits:
                continue
            result.backend = backend
            if not result.facebook_url:
                result.facebook_url = self._facebook_from_hits(hits)

            relevant = hits if kind == "facebook" else [h for h in hits if self._relevant(h, title)]
            emails = self._emails(relevant)
            if emails:
                result.emails = emails
                result.source = kind
                return result
        return result

    # -- queries -----------------------------------------------------------
    @staticmethod
    def _queries(title: str, city: str) -> list[tuple[str, str]]:
        location = f' "{city}"' if city else ""
        return [
            ("facebook", f'site:facebook.com "{title}"{location}'),
            ("facebook", f'site:facebook.com "{title}"{location} email'),
            ("web", f'"{title}"{location} email restaurant'),
        ]

    # -- filtering ---------------------------------------------------------
    @staticmethod
    def _emails(hits: list[SearchHit]) -> list[str]:
        emails = extract_emails(*[hit.text for hit in hits])
        filtered: list[str] = []
        for email in emails:
            domain = email.rpartition("@")[2]
            local = email.rpartition("@")[0]
            if any(domain == d or domain.endswith("." + d) for d in BLOCKED_EMAIL_DOMAINS):
                continue
            if local in USELESS_LOCAL_PARTS:
                continue
            filtered.append(email)
        return unique(filtered)

    @staticmethod
    def _relevant(hit: SearchHit, title: str) -> bool:
        tokens = [
            token
            for token in re.findall(r"[a-z0-9']{3,}", title.lower())
            if token not in TITLE_STOPWORDS
        ]
        if not tokens:
            return False
        haystack = f"{hit.title} {hit.snippet} {hit.link}".lower()
        matched = sum(1 for token in tokens if token in haystack)
        return matched >= max(1, math.ceil(len(tokens) * 0.6))

    @staticmethod
    def _facebook_from_hits(hits: list[SearchHit]) -> str:
        for hit in hits:
            candidate = clean_facebook_url(hit.link)
            if candidate:
                return candidate
        return ""

    @staticmethod
    def city_from_text(city: str) -> str:
        return clean_whitespace(city)
