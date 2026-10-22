"""HTTP layer: browser-like headers, UA rotation, retries, https->http fallback."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Mobile/15E148 Safari/604.1",
)

HTML_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml")
MAX_BYTES = 2_500_000
RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass(slots=True)
class FetchResult:
    url: str
    status_code: int
    text: str
    content_type: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400


class HttpClient:
    """Thread-safe-ish wrapper around httpx.Client (one per worker thread is best)."""

    def __init__(self, timeout: float = 15.0, retries: int = 2, fixed_user_agent: str = ""):
        self.timeout = timeout
        self.retries = max(1, retries)
        self._fixed_ua = fixed_user_agent
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            http2=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- public API --------------------------------------------------------
    def get(self, url: str, *, allow_http_fallback: bool = True) -> FetchResult | None:
        urls = [url]
        if allow_http_fallback and url.startswith("https://"):
            urls.append("http://" + url[len("https://"):])
        elif allow_http_fallback and url.startswith("http://"):
            urls.append("https://" + url[len("http://"):])

        last_error = ""
        for candidate in urls:
            result = self._request(candidate)
            if result is not None and result.ok:
                return result
            if result is not None:
                last_error = f"HTTP {result.status_code}"
                log.debug("GET %s -> HTTP %s", candidate, result.status_code)
            else:
                last_error = last_error or "request failed"
        if last_error:
            log.debug("GET %s failed: %s", url, last_error)
        return None

    def post_form(self, url: str, data: dict[str, str]) -> FetchResult | None:
        """POST an application/x-www-form-urlencoded body (DDG HTML endpoint)."""
        return self._request(url, data=data)

    # -- internals ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._fixed_ua or random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }

    def _request(self, url: str, data: dict[str, str] | None = None) -> FetchResult | None:
        delay = 0.6
        for attempt in range(1, self.retries + 1):
            try:
                if data is None:
                    response = self._client.get(url, headers=self._headers())
                else:
                    response = self._client.post(url, data=data, headers=self._headers())
            except httpx.HTTPError as exc:
                log.debug("transport error %s: %s", url, exc)
                if attempt == self.retries:
                    return None
                time.sleep(delay)
                delay *= 2
                continue

            if response.status_code in RETRY_STATUS and attempt < self.retries:
                time.sleep(delay)
                delay *= 2
                continue

            content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if content_type and not any(t in content_type for t in HTML_TYPES):
                return None
            if len(response.content) > MAX_BYTES:
                return None

            if not response.encoding or response.encoding.lower() == "iso-8859-1":
                response.encoding = response.charset_encoding or "utf-8"
            return FetchResult(
                url=str(response.url),
                status_code=response.status_code,
                text=response.text,
                content_type=content_type,
            )
        return None
