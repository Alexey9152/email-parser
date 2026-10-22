"""Key-free web search used for e-mail discovery.

DuckDuckGo is the only engine that reliably answers a plain HTTP client without
an API key (Bing/Google/Startpage now serve JS challenges). It throttles hard,
so the chain:

* talks to the HTML endpoint with a POST (more tolerant than GET),
* keeps a global pause between queries,
* recognises a block (HTTP 202 / "anomaly" page) and cools the backend down
  instead of hammering it,
* falls back to the lite endpoint once.

SerpAPI is used first when a key is configured.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

import httpx

from .extractors import soup_from_html
from .http_client import FetchResult, HttpClient
from .utils import clean_whitespace

log = logging.getLogger(__name__)


class BackendBlocked(RuntimeError):
    """The engine answered with a captcha/rate-limit page."""


@dataclass(slots=True)
class SearchHit:
    title: str = ""
    snippet: str = ""
    link: str = ""

    @property
    def text(self) -> str:
        return f"{self.title} {self.snippet}"


class SearchBackend(Protocol):
    name: str

    def search(self, query: str, num: int = 20) -> list[SearchHit]:
        ...


# --------------------------------------------------------------------- DDG
class DuckDuckGoBackend:
    name = "duckduckgo"

    POST_URL = "https://html.duckduckgo.com/html/"
    LITE_URL = "https://lite.duckduckgo.com/lite/?q={query}"
    BLOCK_MARKERS = ("anomaly", "unusual traffic", "are you a robot", "/challenge")

    def __init__(self, http: HttpClient):
        self.http = http

    def search(self, query: str, num: int = 20) -> list[SearchHit]:
        response = self.http.post_form(self.POST_URL, {"q": query, "kl": "us-en"})
        if response is None:
            raise BackendBlocked("no response from duckduckgo")
        if self._blocked(response):
            raise BackendBlocked("duckduckgo rate limit (HTTP 202)")

        hits = self._parse(response.text)
        if hits:
            return hits[:num]

        lite = self.http.get(self.LITE_URL.format(query=quote_plus(query)))
        if lite is None:
            return []
        if self._blocked(lite):
            raise BackendBlocked("duckduckgo lite rate limit")
        return self._parse(lite.text)[:num]

    def _blocked(self, response: FetchResult) -> bool:
        if response.status_code == 202:
            return True
        lowered = response.text[:5000].lower()
        return any(marker in lowered for marker in self.BLOCK_MARKERS)

    @staticmethod
    def _parse(html: str) -> list[SearchHit]:
        soup = soup_from_html(html)
        hits: list[SearchHit] = []

        for block in soup.select("div.result"):
            anchor = block.select_one("a.result__a")
            if anchor is None:
                continue
            snippet = block.select_one(".result__snippet")
            hits.append(
                SearchHit(
                    title=clean_whitespace(anchor.get_text(" ")),
                    snippet=clean_whitespace(snippet.get_text(" ")) if snippet else "",
                    link=unwrap_ddg_link(anchor.get("href") or ""),
                )
            )
        if hits:
            return hits

        # Lite layout: results are table rows.
        links = soup.select("a.result-link")
        snippets = soup.select("td.result-snippet")
        for index, anchor in enumerate(links):
            snippet = snippets[index].get_text(" ") if index < len(snippets) else ""
            hits.append(
                SearchHit(
                    title=clean_whitespace(anchor.get_text(" ")),
                    snippet=clean_whitespace(snippet),
                    link=unwrap_ddg_link(anchor.get("href") or ""),
                )
            )
        return hits


# ----------------------------------------------------------------- SerpAPI
class SerpApiBackend:
    name = "serpapi"

    URL = "https://serpapi.com/search.json"

    def __init__(self, api_key: str, timeout: float = 25.0, gl: str = "us", hl: str = "en"):
        self.api_key = api_key
        self.timeout = timeout
        self.gl = gl
        self.hl = hl

    def search(self, query: str, num: int = 20) -> list[SearchHit]:
        try:
            response = httpx.get(
                self.URL,
                params={
                    "engine": "google",
                    "q": query,
                    "num": num,
                    "gl": self.gl,
                    "hl": self.hl,
                    "api_key": self.api_key,
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            log.debug("SerpAPI transport error: %s", exc)
            return []
        if response.status_code >= 400:
            log.warning("SerpAPI HTTP %s: %s", response.status_code, response.text[:200])
            return []
        payload = response.json()
        if payload.get("error"):
            log.warning("SerpAPI error: %s", payload["error"])
            return []
        return [
            SearchHit(
                title=str(item.get("title") or ""),
                snippet=str(item.get("snippet") or ""),
                link=str(item.get("link") or ""),
            )
            for item in (payload.get("organic_results") or [])
        ][:num]


# ------------------------------------------------------------------- chain
class WebSearch:
    """Ordered backends with throttling and per-run cool-downs."""

    def __init__(self, backends: list[SearchBackend], delay: float = 4.0, cooldown: float = 120.0):
        self.backends = backends
        self.delay = max(0.0, delay)
        self.cooldown = cooldown
        self._blocked_until: dict[str, float] = {}
        self._empties: dict[str, int] = {}
        self._last_call = 0.0

    @property
    def available(self) -> bool:
        now = time.monotonic()
        return any(self._blocked_until.get(b.name, 0) <= now for b in self.backends)

    @property
    def enabled(self) -> bool:
        return self.available

    def search(self, query: str, num: int = 20) -> tuple[list[SearchHit], str]:
        for backend in self.backends:
            if self._blocked_until.get(backend.name, 0) > time.monotonic():
                continue
            self._throttle()
            try:
                hits = backend.search(query, num)
            except BackendBlocked as exc:
                self._cool_down(backend.name, str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - a backend must never kill the run
                log.debug("%s backend failed: %s", backend.name, exc)
                hits = []
            if hits:
                self._empties[backend.name] = 0
                return hits, backend.name

            self._empties[backend.name] = self._empties.get(backend.name, 0) + 1
            if self._empties[backend.name] >= 2:
                self._cool_down(backend.name, "two empty answers in a row")
        return [], ""

    # -- internals ---------------------------------------------------------
    def _cool_down(self, name: str, reason: str) -> None:
        self._blocked_until[name] = time.monotonic() + self.cooldown
        log.info("Search backend %s paused for %.0fs (%s)", name, self.cooldown, reason)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_call = time.monotonic()


def build_web_search(settings, http: HttpClient) -> WebSearch:
    """SerpAPI first when a key exists, DuckDuckGo otherwise."""
    ordered: list[SearchBackend] = []
    if settings.serpapi_key:
        ordered.append(SerpApiBackend(settings.serpapi_key))
    preference = (settings.search_backend or "auto").lower()
    if preference != "serpapi":
        ordered.append(DuckDuckGoBackend(http))
    return WebSearch(ordered, delay=settings.search_delay, cooldown=settings.search_cooldown)


def unwrap_ddg_link(href: str) -> str:
    href = (href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    if "uddg=" in href:
        query = parse_qs(urlsplit(href).query)
        if query.get("uddg"):
            return unquote(query["uddg"][0])
    return href
