"""Website crawler: homepage -> contact/about pages -> e-mails + social links."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .extractors import (
    emails_from_page,
    collect_links,
    contact_links,
    social_links,
)
from .http_client import FetchResult, HttpClient
from .utils import clean_whitespace, normalize_url, registrable_domain, unique

log = logging.getLogger(__name__)

CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
    "ddos protection by",
    "access denied",
)
COMMON_CONTACT_PATHS = (
    "/contact",
    "/contact-us",
    "/contactus",
    "/contacts",
    "/about",
    "/about-us",
    "/reservations",
    "/impressum",
    "/info",
)


@dataclass(slots=True)
class WebsiteResult:
    website: str
    final_url: str = ""
    emails: list[str] = field(default_factory=list)
    facebook_url: str = ""
    instagram_url: str = ""
    pages_checked: list[str] = field(default_factory=list)
    blocked: bool = False
    error: str = ""

    @property
    def found(self) -> bool:
        return bool(self.emails)


class WebsiteCrawler:
    """Best-effort static (no-JS) e-mail discovery for a single site."""

    def __init__(self, http: HttpClient, max_pages: int = 6):
        self.http = http
        self.max_pages = max(2, max_pages)

    def analyze(self, website: str) -> WebsiteResult:
        start = normalize_url(website)
        result = WebsiteResult(website=start or website)
        if not start:
            result.error = "invalid url"
            return result

        page = self._fetch(start)
        if page is None:
            result.error = "unreachable"
            return result

        fetch, soup = page
        result.final_url = fetch.url
        result.pages_checked.append(fetch.url)
        emails, soup = emails_from_page(fetch.text)
        result.emails = unique(emails)
        result.blocked = _looks_blocked(fetch.text)

        base = fetch.url
        links = collect_links(soup, base)
        socials = social_links(links)
        result.facebook_url = socials.get("facebook", "")
        result.instagram_url = socials.get("instagram", "")

        # Follow contact/about pages: they still may hold the Facebook link.
        candidates = [link.url for link in contact_links(links, base, limit=self.max_pages - 1)]
        if not result.emails:
            candidates += [self._path_link(base, p) for p in COMMON_CONTACT_PATHS]

        seen = {base.rstrip("/")}
        for link in unique(candidates):
            if len(result.pages_checked) >= self.max_pages:
                break
            if link.rstrip("/") in seen:
                continue
            seen.add(link.rstrip("/"))
            sub = self._fetch(link)
            if sub is None:
                continue
            sub_fetch, _ = sub
            result.pages_checked.append(sub_fetch.url)
            sub_emails, sub_soup = emails_from_page(sub_fetch.text)
            result.emails = unique(result.emails + sub_emails)
            if not result.facebook_url:
                result.facebook_url = social_links(collect_links(sub_soup, sub_fetch.url)).get("facebook", "")
            if result.emails:
                break

        return result

    # -- helpers -----------------------------------------------------------
    def _fetch(self, url: str) -> tuple[FetchResult, BeautifulSoup] | None:
        response = self.http.get(url)
        if response is None or not response.text:
            return None
        _, soup = emails_from_page(response.text)
        return response, soup

    @staticmethod
    def _path_link(base: str, path: str) -> str:
        parts = urlsplit(base)
        return f"{parts.scheme}://{parts.netloc}{path}"


def _looks_blocked(html: str) -> bool:
    lowered = (html or "")[:20000].lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


def guess_contact_url(website: str, path: str) -> str:
    """Used by the JS fallback to build absolute contact URLs."""
    base = normalize_url(website)
    if not base:
        return ""
    parts = urlsplit(base)
    domain = registrable_domain(base)
    if not domain:
        return ""
    return f"{parts.scheme}://{parts.netloc}{path}"
