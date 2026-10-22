"""Free Google Maps scraping through Playwright.

This backend is deliberately conservative: it never logs in, reads the public
place cards and reuses one browser for all category queries. Google changes the
DOM often and may show a consent wall or a captcha, so treat failures as
expected and fall back to Apify/Outscraper for production volumes.
"""

from __future__ import annotations

import logging
import math
import re
import time
from urllib.parse import quote, urlsplit, urlunsplit

from ..models import Restaurant
from ..utils import Area, clean_whitespace, coords_from_maps_url, unique
from .base import MapsProviderBase, MapsProviderError

log = logging.getLogger(__name__)

CONSENT_BUTTONS = (
    'button:has-text("Accept all")',
    'button:has-text("I agree")',
    'button:has-text("Alle akzeptieren")',
    'button:has-text("Принять все")',
    'form[action*="consent"] button',
)
DETAIL_SELECTORS = {
    "address": 'button[data-item-id="address"]',
    "phone": 'button[data-item-id^="phone:"]',
    "website": 'a[data-item-id="authority"]',
}


class PlaywrightMapsProvider(MapsProviderBase):
    name = "playwright"

    def __init__(self, headless: bool = True, language: str = "en", locale: str = "en-US"):
        self.headless = headless
        self.language = language
        self.locale = locale

    # -- protocol ----------------------------------------------------------
    def search(
        self, query: str, limit: int, area: Area | None = None
    ) -> list[Restaurant]:
        return self.search_many([query], limit, area)

    def search_many(
        self, queries: list[str], limit: int, area: Area | None = None
    ) -> list[Restaurant]:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        queries = [q for q in queries if q.strip()]
        limit = max(1, limit)
        if not queries:
            return []
        per_query = max(5, math.ceil(limit / len(queries)))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = browser.new_context(
                locale=self.locale,
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.new_page()
            try:
                place_urls: list[str] = []
                query_by_url: dict[str, str] = {}
                for index, query in enumerate(queries, start=1):
                    if len(place_urls) >= limit:
                        break
                    log.info(
                        "Maps query %d/%d: %s", index, len(queries), query
                    )
                    found = self._collect_place_urls(
                        page, query, per_query, area
                    )
                    log.info("  %d place link(s)", len(found))
                    for url in found:
                        query_by_url.setdefault(url, query)
                    place_urls = unique(place_urls + found)

                place_urls = place_urls[:limit]
                log.info("Google Maps: %d unique places to open", len(place_urls))
                restaurants: list[Restaurant] = []
                detail = context.new_page()
                for index, url in enumerate(place_urls, start=1):
                    item = self._read_place(
                        detail, url, query_by_url.get(url, queries[0])
                    )
                    if item and item.title:
                        if area is not None and item.latitude is not None and item.longitude is not None:
                            if not area.contains(item.latitude, item.longitude):
                                continue
                        restaurants.append(item)
                        log.info("[%d/%d] %s", index, len(place_urls), item.title)
                    time.sleep(0.2)
                return restaurants
            except PlaywrightTimeout as exc:
                raise MapsProviderError(f"Google Maps page timed out: {exc}") from exc
            except PlaywrightError as exc:
                raise MapsProviderError(f"Playwright error: {exc}") from exc
            finally:
                context.close()
                browser.close()

    # -- list page ---------------------------------------------------------
    def _collect_place_urls(
        self, page, query: str, limit: int, area: Area | None
    ) -> list[str]:
        url = self._search_url(query, area)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        self._dismiss_consent(page)

        if "consent.google.com" in page.url:
            raise MapsProviderError(
                "Google shows a consent wall. Run once with --headed and accept it, "
                "or switch MAPS_PROVIDER to apify/outscraper."
            )
        try:
            page.wait_for_selector('div[role="feed"]', timeout=25_000)
        except Exception:
            if self._is_blocked(page):
                raise MapsProviderError(
                    "Google Maps blocked the request (captcha/unusual traffic). "
                    "Switch provider or run with --headed."
                )
            if "/maps/place/" in page.url:
                return [page.url]
            raise MapsProviderError("Could not find the Maps results list.")

        feed = page.locator('div[role="feed"]')
        seen: list[str] = []
        idle_rounds = 0
        for _ in range(40):
            hrefs = feed.locator('a[href*="/maps/place/"]').evaluate_all(
                "els => els.map(e => e.href)"
            )
            before = len(seen)
            seen = unique(seen + [self._canonical(href) for href in hrefs])
            seen = [href for href in seen if self._in_area(href, area)]
            if len(seen) >= limit:
                break
            if len(seen) == before:
                idle_rounds += 1
            else:
                idle_rounds = 0
            if idle_rounds >= 4:
                break
            feed.evaluate("el => el.scrollBy(0, el.scrollHeight)")
            page.wait_for_timeout(1200)
        return seen[:limit]

    def _search_url(self, query: str, area: Area | None) -> str:
        base = f"https://www.google.com/maps/search/{quote(query)}"
        if area is not None:
            lat, lng = area.center
            base += f"/@{lat:.6f},{lng:.6f},{self._zoom(area)}z"
        return f"{base}?hl={self.language}&gl=us"

    @staticmethod
    def _zoom(area: Area) -> int:
        radius = max(area.approximate_radius_km(), 0.2)
        zoom = int(round(15 - math.log2(radius)))
        return max(10, min(17, zoom))

    @staticmethod
    def _canonical(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _in_area(url: str, area: Area | None) -> bool:
        if area is None:
            return True
        coords = coords_from_maps_url(url)
        if coords is None:
            return True
        return area.contains(*coords)

    # -- detail page -------------------------------------------------------
    def _read_place(self, page, url: str, query: str) -> Restaurant | None:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_selector("h1", timeout=20_000)
        except Exception as exc:
            log.debug("place %s failed: %s", url, exc)
            return None

        title = self._text(page, "h1")
        if not title:
            return None

        address = self._aria(page, DETAIL_SELECTORS["address"], ("Address: ", "Адрес: "))
        phone = self._aria(page, DETAIL_SELECTORS["phone"], ("Phone: ", "Телефон: "))
        if not phone:
            raw = page.locator(DETAIL_SELECTORS["phone"])
            if raw.count():
                item_id = raw.first.get_attribute("data-item-id") or ""
                if item_id.startswith("phone:"):
                    phone = item_id.split("phone:", 1)[1].strip()

        website = ""
        link = page.locator(DETAIL_SELECTORS["website"])
        if link.count():
            website = (link.first.get_attribute("href") or "").strip()

        category = ""
        try:
            button = page.locator('button[jsaction*="category"]').first
            if button.count():
                category = clean_whitespace(button.inner_text())
        except Exception:
            pass

        rating, reviews = self._rating(page)
        coords = coords_from_maps_url(page.url) or coords_from_maps_url(url)
        return Restaurant(
            title=clean_whitespace(title),
            address=address,
            phone=phone,
            website=website,
            google_maps_url=page.url,
            category=category,
            source_query=query,
            rating=rating,
            reviews=reviews,
            latitude=coords[0] if coords else None,
            longitude=coords[1] if coords else None,
            place_id=self._place_id(url),
        )

    @staticmethod
    def _rating(page) -> tuple[float | None, int | None]:
        rating: float | None = None
        reviews: int | None = None
        try:
            text = page.locator("div.F7nice").first.inner_text(timeout=2_000)
        except Exception:
            text = ""
        if not text:
            try:
                text = page.locator('div[role="img"][aria-label*="star"]').first.get_attribute(
                    "aria-label"
                ) or ""
            except Exception:
                text = ""
        match = re.search(r"(\d(?:[.,]\d)?)", text)
        if match:
            try:
                rating = float(match.group(1).replace(",", "."))
            except ValueError:
                rating = None
        reviews_match = re.search(r"\(([\d.,\s]+)\)", text)
        if reviews_match:
            digits = re.sub(r"\D", "", reviews_match.group(1))
            reviews = int(digits) if digits else None
        return rating, reviews

    @staticmethod
    def _place_id(url: str) -> str:
        match = re.search(r"!19s([\w-]+)", url or "")
        if match:
            return match.group(1)
        match = re.search(r"place_id:([\w-]+)", url or "")
        return match.group(1) if match else ""

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _text(page, selector: str) -> str:
        try:
            locator = page.locator(selector).first
            if locator.count():
                return clean_whitespace(locator.inner_text())
        except Exception:
            pass
        return ""

    @staticmethod
    def _aria(page, selector: str, prefixes: tuple[str, ...]) -> str:
        try:
            locator = page.locator(selector).first
            if not locator.count():
                return ""
            label = locator.get_attribute("aria-label") or ""
            for prefix in prefixes:
                if label.startswith(prefix):
                    return clean_whitespace(label[len(prefix):])
            return clean_whitespace(label)
        except Exception:
            return ""

    def _dismiss_consent(self, page) -> None:
        for selector in CONSENT_BUTTONS:
            try:
                button = page.locator(selector).first
                if button.count():
                    button.click(timeout=2_500)
                    page.wait_for_timeout(800)
                    return
            except Exception:
                continue

    @staticmethod
    def _is_blocked(page) -> bool:
        try:
            text = (page.content() or "")[:20_000].lower()
        except Exception:
            return False
        return any(
            marker in text
            for marker in (
                "unusual traffic",
                "not a robot",
                "captcha",
                "before you continue",
            )
        )
