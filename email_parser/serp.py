"""Key-free / cheap web search used for e-mail discovery.

Order of preference:

* **Apify Google Search Scraper** - uses the same Apify token as the Maps stage,
  costs about $0.001 per result and returns real URLs, so it also finds the
  Facebook page that the browser renderer can open afterwards.
* **SerpAPI** - used first when a key is configured.
* **DuckDuckGo HTML** - free fallback; throttles hard, so it is cooled down on
  the first block instead of being hammered.

Every backend returns :class:`SearchHit` rows. Backends that support batching
implement ``search_many`` and are called once for many queries.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

import httpx

from .extractors import soup_from_html
from .http_client import FetchResult, HttpClient
from .utils import clean_whitespace, unique

log = logging.getLogger(__name__)

APIFY_API = "https://api.apify.com/v2"
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


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


# --------------------------------------------------------------- Apify Google
class ApifySearchBackend:
    """Google results through the Apify Google Search Scraper actor."""

    name = "apify-google"

    def __init__(
        self,
        token: str,
        actor: str = "apify/google-search-scraper",
        timeout: float = 420,
        poll_interval: float = 3,
        country: str = "us",
        language: str = "en",
        chunk_size: int = 40,
    ):
        if not token:
            raise BackendBlocked("APIFY_TOKEN is empty")
        self.token = token
        self.actor = actor
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.country = country
        self.language = language
        self.chunk_size = max(1, chunk_size)

    def search(self, query: str, num: int = 10) -> list[SearchHit]:
        return self.search_many([query], num).get(query, [])

    def search_many(self, queries: list[str], num: int = 10) -> dict[str, list[SearchHit]]:
        queries = [clean_whitespace(q) for q in unique(queries) if clean_whitespace(q)]
        results: dict[str, list[SearchHit]] = {query: [] for query in queries}
        if not queries:
            return results

        pages = max(1, math.ceil(max(10, num) / 10))
        for start in range(0, len(queries), self.chunk_size):
            chunk = queries[start : start + self.chunk_size]
            items = self._run(chunk, pages)
            for item in items:
                term = clean_whitespace(
                    str((item.get("searchQuery") or {}).get("term") or "")
                )
                if term not in results:
                    # The actor normalises quotes/spaces; match by prefix as a fallback.
                    term = next(
                        (q for q in chunk if q.lower().strip('"') == term.lower().strip('"')),
                        term,
                    )
                if term not in results:
                    continue
                results[term] = self._parse_item(item, num)

        found = sum(len(hits) for hits in results.values())
        log.info(
            "Google search via Apify: %d quer(ies), %d result(s), ~$%.3f",
            len(queries),
            found,
            found * 0.00105,
        )
        return results

    # -- internals ---------------------------------------------------------
    def _run(self, queries: list[str], pages: int) -> list[dict]:
        actor_path = quote_actor(self.actor)
        payload = {
            "queries": "\n".join(queries),
            "maxPagesPerQuery": pages,
            "resultsPerPage": 10,
            "countryCode": self.country,
            "searchLanguage": self.language,
            "languageCode": self.language,
            "mobileResults": False,
            "includeUnfilteredResults": False,
            "saveHtml": False,
        }
        with httpx.Client(timeout=60) as client:
            response = client.post(
                f"{APIFY_API}/acts/{actor_path}/runs",
                params={"token": self.token},
                json=payload,
            )
            if response.status_code >= 400:
                raise BackendBlocked(
                    f"Apify search start failed ({response.status_code}): "
                    f"{response.text[:200]}"
                )
            run = response.json()["data"]
            run_id = run["id"]
            dataset_id = run["defaultDatasetId"]
            log.info(
                "Apify search run %s started for %d query(ies)...",
                run_id,
                len(queries),
            )
            deadline = time.time() + max(self.timeout, 60 * len(queries))
            while time.time() < deadline:
                status = client.get(
                    f"{APIFY_API}/actor-runs/{run_id}", params={"token": self.token}
                ).json()["data"]["status"]
                if status in TERMINAL_STATUSES:
                    if status != "SUCCEEDED":
                        raise BackendBlocked(f"Apify search run finished with {status}")
                    break
                time.sleep(self.poll_interval)
            else:
                raise BackendBlocked("Apify search run did not finish in time")

            items = client.get(
                f"{APIFY_API}/datasets/{dataset_id}/items",
                params={"token": self.token, "clean": "true", "format": "json"},
            ).json()
        return items or []

    @staticmethod
    def _parse_item(item: dict, num: int) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for result in (item.get("organicResults") or [])[:num]:
            title = clean_whitespace(str(result.get("title") or ""))
            link = (result.get("url") or "").strip()
            snippet = clean_whitespace(str(result.get("description") or ""))
            if title or link or snippet:
                hits.append(SearchHit(title=title, snippet=snippet, link=link))
        return hits


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
    """Ordered backends with throttling, batching and per-run cool-downs."""

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

    def search_many(self, queries: list[str], num: int = 10) -> dict[str, list[SearchHit]]:
        """Run many queries, using a single actor run for batching backends."""
        pending = [q for q in unique(queries) if (q or "").strip()]
        found: dict[str, list[SearchHit]] = {}
        for backend in self.backends:
            if not pending:
                break
            if self._blocked_until.get(backend.name, 0) > time.monotonic():
                continue
            if hasattr(backend, "search_many"):
                self._throttle()
                try:
                    batch = backend.search_many(pending, num)
                except BackendBlocked as exc:
                    self._cool_down(backend.name, str(exc))
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.debug("%s batch failed: %s", backend.name, exc)
                    batch = {}
                for query, hits in (batch or {}).items():
                    if hits:
                        found[query] = hits
                pending = [q for q in pending if q not in found]
                continue

            for query in list(pending):
                self._throttle()
                try:
                    hits = backend.search(query, num)
                except BackendBlocked as exc:
                    self._cool_down(backend.name, str(exc))
                    break
                except Exception as exc:  # noqa: BLE001
                    log.debug("%s backend failed: %s", backend.name, exc)
                    hits = []
                if hits:
                    found[query] = hits
                    pending.remove(query)
        return found

    # -- internals ---------------------------------------------------------
    def _cool_down(self, name: str, reason: str) -> None:
        self._blocked_until[name] = time.monotonic() + self.cooldown
        log.info("Search backend %s paused for %.0fs (%s)", name, self.cooldown, reason)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_call = time.monotonic()


def quote_actor(actor: str) -> str:
    return actor.replace("/", "~")


def build_web_search(settings, http: HttpClient) -> WebSearch:
    """Pick search backends according to SEARCH_BACKEND / available keys."""
    ordered: list[SearchBackend] = []
    preference = (settings.search_backend or "auto").lower()
    if preference in {"auto", "serpapi"} and settings.serpapi_key:
        ordered.append(SerpApiBackend(settings.serpapi_key))
    if preference in {"auto", "apify", "google"} and settings.apify_token:
        ordered.append(
            ApifySearchBackend(
                token=settings.apify_token,
                actor=settings.apify_search_actor,
                timeout=settings.search_timeout,
            )
        )
    if preference in {"auto", "ddg", "duckduckgo"}:
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
