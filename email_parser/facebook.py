"""E-mail discovery through search engines (Facebook pages + general web).

Facebook requires login for profile pages, so search snippets are used to find
the venue's Facebook page URL and any address mentioned there. The pipeline then
opens the found page in a real browser (see ``playwright_site``) which often
reveals the address from the "About" block even behind the login wall.

Queries can be planned in advance and evaluated from a batch result, which lets
the Apify Google Search backend handle dozens of venues in a single actor run.
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


def filter_email_list(emails: list[str]) -> list[str]:
    """Drop directory/robot addresses, keep the venue's own inboxes."""
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
    """Plan search queries, then extract addresses and Facebook URLs from hits."""

    def __init__(self, web_search: WebSearch | None):
        self.engine = web_search

    @property
    def enabled(self) -> bool:
        return bool(self.engine and self.engine.enabled)

    # -- query planning ----------------------------------------------------
    @staticmethod
    def plan(
        title: str,
        city: str = "",
        phone: str = "",
        facebook_url: str = "",
        max_queries: int = 3,
    ) -> list[tuple[str, str]]:
        """Ordered (kind, query) pairs for one venue. Cheap queries go first."""
        title = clean_whitespace(title)
        if not title or max_queries <= 0:
            return []
        location = f' "{clean_whitespace(city)}"' if city else ""
        options: list[tuple[str, str]] = [
            ("facebook", f'site:facebook.com "{title}"{location}'),
            ("facebook", f'site:facebook.com "{title}"{location} email'),
            ("web", f'"{title}"{location} email'),
            ("web", f'"{title}"{location} contact'),
        ]
        if phone:
            options.append(("web", f'"{clean_whitespace(phone)}" email'))
        return options[:max_queries]

    # -- one-by-one (kept for the CLI / tests) -----------------------------
    def find(
        self,
        title: str,
        city: str = "",
        facebook_url: str = "",
        max_queries: int = 3,
        phone: str = "",
    ) -> SocialResult:
        result = SocialResult(facebook_url=clean_facebook_url(facebook_url))
        if not self.enabled:
            result.error = "no search backend available"
            return result
        plan = self.plan(title, city, phone, facebook_url, max_queries)
        if not plan:
            result.error = "empty title or no query budget"
            return result
        hits_by_query: dict[str, list[SearchHit]] = {}
        backend = ""
        for _kind, query in plan:
            if not self.enabled:
                break
            hits, backend = self.engine.search(query)  # type: ignore[union-attr]
            result.queries.append(query)
            if hits:
                hits_by_query[query] = hits
        evaluated = self.evaluate(title, plan, hits_by_query, backend, result.facebook_url)
        evaluated.queries = result.queries
        return evaluated

    # -- batch evaluation --------------------------------------------------
    def evaluate(
        self,
        title: str,
        plan: list[tuple[str, str]],
        hits_by_query: dict[str, list[SearchHit]],
        backend: str = "google",
        facebook_url: str = "",
    ) -> SocialResult:
        result = SocialResult(backend=backend)
        result.facebook_url = clean_facebook_url(facebook_url)
        for kind, query in plan:
            hits = hits_by_query.get(query) or []
            if not hits:
                continue
            result.queries.append(query)
            if not result.facebook_url:
                result.facebook_url = self._facebook_from_hits(hits)
            relevant = hits if kind == "facebook" else [h for h in hits if self._relevant(h, title)]
            emails = self._emails(relevant)
            if emails:
                result.emails = emails
                result.source = kind
                return result
        return result

    # -- filtering ---------------------------------------------------------
    @staticmethod
    def _emails(hits: list[SearchHit]) -> list[str]:
        emails = extract_emails(*[hit.text for hit in hits])
        return filter_email_list(emails)

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
        candidates: list[tuple[int, str]] = []
        for hit in hits:
            candidate = clean_facebook_url(hit.link)
            if not candidate:
                continue
            lowered = candidate.lower()
            # Profile pages load much better than posts/groups/reels.
            penalty = 2 if any(
                part in lowered
                for part in ("/posts/", "/groups/", "/photo", "/photos/", "/watch", "/reel", "/videos/")
            ) else 0
            candidates.append((penalty, candidate))
        if not candidates:
            return ""
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    @staticmethod
    def city_from_text(city: str) -> str:
        return clean_whitespace(city)
