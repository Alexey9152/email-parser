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
from .models import FREE_MAIL_DOMAINS
from .serp import SearchHit, WebSearch
from .utils import clean_whitespace, registrable_domain, unique

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

# Words that describe the city/area rather than the venue.  A Facebook page
# called "Ocean City MD" must not be accepted for "Ocean City Fish Company".
PLACE_TOKENS = {
    "ocean", "city", "beach", "boardwalk", "downtown", "uptown", "coast",
    "coastal", "shore", "bay", "bayside", "beachside", "seaside", "lakeside",
    "riverside", "oceanside", "harborside", "island", "harbor", "harbour",
    "marina", "county", "town", "village", "east", "west", "north", "south",
    "upper", "lower", "maryland", "delaware", "virginia", "pennsylvania", "new",
    "jersey", "carolina", "florida", "georgia", "texas", "california",
    "md", "de", "nj", "pa", "va", "dc", "ny", "nc", "sc", "ga", "fl", "tx",
    "ca", "oc",
}
GENERIC_VENUE_TOKENS = {
    "house", "company", "dining", "eatery", "bistro", "pub", "tavern",
    "lounge", "market", "deli", "bakery", "coffee", "ice", "cream", "shop",
    "food", "seafood", "steakhouse", "sports", "family", "american",
}
GENERIC_EMAIL_LOCALS = {
    "info", "contact", "hello", "office", "team", "admin", "manager",
    "hiring", "hr", "jobs", "careers", "mail", "email", "general",
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
    "/best-", "/listing", "/yellow", "/groups/",
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


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _title_variants(title: str) -> set[str]:
    compact = _compact(title)
    variants = {compact} if compact else set()
    # Only strip a real leading article ("The Bonfire"), not the first three
    # letters of a word such as "Theater".
    if re.match(r"^the(?:[^a-z]|$)", (title or "").strip().lower()):
        variants.add(compact[3:])
    return {value for value in variants if len(value) >= 4}


def _venue_tokens(title: str, city: str = "") -> tuple[list[str], list[str]]:
    """Return (strong, weak) title tokens.

    Strong tokens identify the venue itself.  City words and generic venue
    nouns are weak: they cannot prove that a Facebook page belongs to a
    particular restaurant.
    """
    city_tokens = set(re.findall(r"[a-z0-9']{3,}", (city or "").lower()))
    tokens = [
        token
        for token in re.findall(r"[a-z0-9']{3,}", (title or "").lower())
        if token not in TITLE_STOPWORDS
    ]
    strong: list[str] = []
    weak: list[str] = []
    for token in tokens:
        compact = _compact(token)
        if len(compact) < 3:
            continue
        if token in city_tokens or token in PLACE_TOKENS:
            weak.append(token)
        elif token in GENERIC_VENUE_TOKENS:
            weak.append(token)
        else:
            strong.append(token)
    return strong, weak


def _slug_relevant(slug: str, page_title: str, title: str, city: str = "") -> bool:
    if (slug or "").isdigit():
        return True
    haystack = _compact(f"{slug} {page_title}")
    variants = _title_variants(title)
    if any(variant in haystack for variant in variants):
        return True
    strong, _weak = _venue_tokens(title, city)
    if not strong:
        return False
    # Shortened page slugs often keep only the distinctive first word:
    # ``facebook.com/Mackys`` for "Macky's Bayside Bar and Grill".
    first = _compact(strong[0])
    if len(first) >= 5 and first in haystack:
        return True
    matched = sum(1 for token in strong if _compact(token) in haystack)
    return matched >= max(1, math.ceil(len(strong) * 0.6))


def _facebook_slug(link: str) -> tuple[str, bool]:
    """Return the page slug and whether the URL is an id-only profile.

    ``clean_facebook_url`` reduces posts/sub-tabs and canonicalises legacy
    ``/pg/Name`` links, so the identity check always looks at the real page.
    """
    cleaned = clean_facebook_url(link) or (link or "")
    segments = [segment for segment in urlsplit(cleaned).path.split("/") if segment]
    if not segments:
        return "", False
    slug = segments[0]
    if slug.lower() == "profile.php" and "id=" in cleaned.lower():
        return slug, True
    if slug.lower() in {"pages", "people", "public"} and len(segments) >= 3:
        return segments[1], False
    return slug, False


def _facebook_page_relevant(hit: SearchHit, title: str, city: str = "") -> bool:
    """Reject city/category pages that merely contain the location name.

    The search-result title is deliberately ignored: an engine can prepend the
    venue name to a city page (``Ocean City Fish Company - Ocean City, MD |
    Facebook``) even though the URL is ``facebook.com/OceanCityMD``.  Only the
    URL identifies the page.
    """
    slug, id_only = _facebook_slug(hit.link or "")
    if id_only:
        return True
    return _slug_relevant(slug, "", title, city)


def facebook_page_relevant(
    page_url: str, page_title: str, title: str, city: str = ""
) -> bool:
    """Public wrapper for the browser identity check."""
    slug, id_only = _facebook_slug(page_url or "")
    if id_only:
        return True
    return _slug_relevant(slug, page_title or "", title, city)


def facebook_email_belongs_to_venue(
    email: str,
    title: str,
    city: str = "",
    *,
    website: str = "",
    page_relevant: bool = True,
) -> bool:
    """Public wrapper for the mailbox identity check."""
    return _email_matches_venue(
        email,
        title,
        city,
        website=website,
        page_relevant=page_relevant,
    )


def _email_matches_venue(
    email: str,
    title: str,
    city: str,
    *,
    website: str = "",
    page_relevant: bool = True,
) -> bool:
    """The snippet address must be tied to this venue, not to the city page."""
    if "@" not in email:
        return False
    local, _, domain = email.lower().partition("@")
    domain = domain.strip().strip(".")
    if not local or not domain:
        return False

    site_domain = registrable_domain(website)
    email_domain = registrable_domain("https://" + domain)
    if site_domain and email_domain == site_domain:
        return True
    if not page_relevant:
        return False

    strong, _weak = _venue_tokens(title, city)
    variants = _title_variants(title)
    haystack = _compact(f"{local} {domain}")
    domain_compact = _compact(domain)
    local_compact = _compact(local)

    if any(variant in haystack for variant in variants):
        if domain in FREE_MAIL_DOMAINS:
            return True
        # On a corporate domain a title in the local part alone is not enough:
        # thebonfire@siriusxm.com belongs to a radio show, not to the venue.
        return any(variant in domain_compact for variant in variants)

    if domain in FREE_MAIL_DOMAINS:
        if local_compact in GENERIC_EMAIL_LOCALS:
            return True
        if any(_compact(token) in haystack for token in strong):
            return True
        # A city mailbox such as menuocity@gmail.com has no venue token, only
        # the place name.  It may belong to the tourist office, not the venue.
        if any(
            len(_compact(token)) >= 3 and _compact(token) in local_compact
            for token in PLACE_TOKENS
        ):
            return False
        return True

    return any(_compact(token) in domain_compact for token in strong)


def _same_facebook_page(hit: SearchHit, page_url: str) -> bool:
    hit_page = clean_facebook_url(hit.link)
    known = clean_facebook_url(page_url)
    return bool(hit_page and known and hit_page == known)


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
        city_text = clean_whitespace(city)
        options: list[tuple[str, str]] = []
        if facebook_url:
            # The page is already known: ask the engine to show its contact block.
            options.append(
                ("facebook", f'"{canonical}" email')
            )
        options.extend(
            [
                # A broad, unquoted query works much better on free engines than
                # an exact phrase with the word "email": it still returns the
                # Facebook profile, which the browser stage can open.  Keep the
                # precise site: query later for paid Google backends.
                ("facebook", f"{title} {city_text} facebook".strip()),
                ("facebook", f'"{title}"{location} facebook email'),
                ("web", f'"{title}"{location} email'),
                ("facebook", f'site:facebook.com "{title}"{location} email'),
            ]
        )
        if phone:
            options.append(("web", f'"{clean_whitespace(phone)}" email'))
        options.append(("web", f'"{title}"{location} contact'))
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
        evaluated = self.evaluate(
            title,
            plan,
            hits_by_query,
            backend,
            result.facebook_url,
            city=city,
        )
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
        city: str = "",
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
                # Prefer profile links over aggregate/category pages; a city
                # page must never become the venue's Facebook link.
                profile_hits = [
                    hit
                    for hit in hits
                    if not _is_aggregate(hit)
                    and _facebook_page_relevant(hit, title, city)
                ]
                result.facebook_url = self._facebook_from_hits(profile_hits)
            if kind == "facebook":
                # A snippet address is accepted only when both the page and the
                # mailbox can be tied to this venue.  This blocks city/tourism
                # pages such as menuocity@gmail.com and unrelated corporate
                # mailboxes that happen to mention the venue's name.
                accepted: list[SearchHit] = []
                for hit in email_hits:
                    if _is_aggregate(hit):
                        continue
                    known_page = _same_facebook_page(hit, result.facebook_url)
                    page_relevant = known_page or _facebook_page_relevant(
                        hit, title, city
                    )
                    emails = self._emails([hit])
                    if any(
                        _email_matches_venue(
                            email,
                            title,
                            city,
                            website=restaurant_website,
                            page_relevant=page_relevant,
                        )
                        for email in emails
                    ):
                        accepted.append(hit)
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
