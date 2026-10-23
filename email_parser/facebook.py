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
from urllib.parse import urlsplit

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


# Aggregate pages such as Facebook's own "Food & beverage" category list the
# addresses of *many* unrelated venues, so their snippets must never be trusted.
AGGREGATE_PATH_HINTS = (
    "category", "/pages/category", "/directory", "/directories", "/cities",
    "/city/", "/places", "/businesses", "/biz", "/local", "/nearby", "/top-",
    "/best-", "/listing", "/yellow",
)


def _title_tokens(title: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9']{3,}", (title or "").lower())
        if token not in TITLE_STOPWORDS
    ]


def _host(url: str) -> str:
    try:
        return urlsplit(url or "").netloc.lower()
    except ValueError:  # pragma: no cover - urlsplit rarely raises
        return ""


def _on_venue_page(hit: SearchHit, venue_url: str) -> bool:
    """True when the hit URL really belongs to the venue's own page."""
    if not venue_url:
        return False
    venue_host = _host(venue_url)
    hit_host = _host(hit.link)
    if not venue_host or not hit_host:
        return False
    if not (hit_host == venue_host or hit_host.endswith("." + venue_host)):
        return False
    slug = urlsplit(venue_url).path.strip("/").split("/")[0].lower()
    hit_path = (urlsplit(hit.link).path or "/").lower()
    if not slug or slug.isdigit():
        # Numeric page ids (facebook.com/409248996222954) are unambiguous.
        return True
    if slug in hit_path:
        return True
    title_tokens = _title_tokens(hit.title)
    if title_tokens and sum(1 for token in title_tokens if token in hit_path) >= max(
        1, math.ceil(len(title_tokens) * 0.5)
    ):
        return True
    return False


def _is_aggregate(hit: SearchHit) -> bool:
    link = (hit.link or "").lower()
    if any(hint in link for hint in AGGREGATE_PATH_HINTS):
        return True
    return any(hint in (hit.title or "").lower() for hint in AGGREGATE_PATH_HINTS)


@dataclass(slots=True)
class SocialResult:
    emails: list[str] = field(default_factory=list)
    facebook_url: str = ""
    source: str = ""  # "facebook" | "web"
    backend: str = ""
    queries: list[str] = field(default_factory=list)
    error: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.emails)

    @property
    def unreliable(self) -> bool:
        """Addresses that were found only on aggregate / unrelated pages."""
        return not self.emails and bool(self.notes)


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
        canonical = clean_facebook_url(facebook_url)
        location = f' "{clean_whitespace(city)}"' if city else ""
        options: list[tuple[str, str]] = []
        if facebook_url:
            # The page is already known: ask the engine to show its contact block.
            options.append(
                ("facebook", f'"{canonical}" email')
            )
        options.extend(
            [
                # Free engines (DDGS) often ignore the ``site:`` operator, but a
                # normal phrase query still surfaces the Facebook page and its
                # contact snippet.  Keep the more precise site: query as a later
                # option for paid Google backends.
                ("facebook", f'"{title}"{location} facebook email'),
                ("web", f'"{title}"{location} email'),
                ("facebook", f'site:facebook.com "{title}"{location} email'),
                ("web", f'"{title}"{location} contact'),
            ]
        )
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
        restaurant_website: str = "",
    ) -> SocialResult:
        result = SocialResult(backend=backend)
        result.facebook_url = clean_facebook_url(facebook_url)
        # Keep the state that produced the best address, plus any notice about
        # addresses that were rejected because they belong to other venues.
        best: list[str] = []
        best_kind = ""
        for kind, query in plan:
            hits = hits_by_query.get(query) or []
            if not hits:
                continue
            result.queries.append(query)
            email_hits = [hit for hit in hits if self._emails([hit])]
            if not result.facebook_url:
                # Prefer profile links over aggregate/category pages.
                profile_hits = [hit for hit in hits if not _is_aggregate(hit)]
                result.facebook_url = self._facebook_from_hits(profile_hits or hits)
            if kind == "facebook":
                # `site:facebook.com "<venue>"` snippets usually contain the
                # address from the page's contact block. Facebook never shows
                # these snippets for aggregate category pages, so they are safe;
                # still drop obvious listing pages and hits with no address.
                accepted = [hit for hit in email_hits if not _is_aggregate(hit)]
            else:
                targets = [result.facebook_url] if result.facebook_url else []
                targets += [restaurant_website] if restaurant_website else []
                accepted = [hit for hit in email_hits if self._relevant(hit, title)]
                if targets:
                    accepted = [
                        hit
                        for hit in accepted
                        if any(_on_venue_page(hit, target) for target in targets)
                    ]
                # Without a page or site to compare against, generic web hits
                # are too easy to confuse with another venue's address.
                if not targets:
                    inspected = accepted
                    accepted = []
                    if inspected:
                        result.notes.append(
                            "web hits without a page/website were ignored"
                        )
            if accepted:
                emails = self._emails(accepted)
                if emails and not best:
                    best = emails
                    best_kind = kind
                continue
            if email_hits and not accepted:
                result.notes.append(
                    f"addresses on unrelated/aggregate pages ignored ({kind})"
                )
        if best:
            result.emails = best
            result.source = best_kind
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
