"""Free Google Maps scraping through Playwright.

This backend is deliberately conservative: it never logs in, does one query per
run and reads the public place cards. Google changes the DOM often and may show
a consent wall or a captcha, so treat failures as expected and fall back to
Apify/Outscraper for production volumes.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote

from ..models import Restaurant
from ..utils import clean_whitespace, unique
from .base import MapsProviderError

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


class PlaywrightMapsProvider:
    name = "playwright"

    def __init__(self, headless: bool = True, language: str = "en", locale: str = "en-US"):
        self.headless = headless
        self.language = language
        self.locale = locale

    def search(self, query: str, limit: int) -> list[Restaurant]:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        limit = max(1, limit)
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
                place_urls = self._collect_place_urls(page, query, limit)
                log.info("Google Maps: %d place links found", len(place_urls))
                restaurants: list[Restaurant] = []
                detail = context.new_page()
                for index, url in enumerate(place_urls[:limit], start=1):
                    item = self._read_place(detail, url, query)
                    if item and item.title:
                        restaurants.append(item)
                        log.info("[%d/%d] %s", index, len(place_urls[:limit]), item.title)
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
    def _collect_place_urls(self, page, query: str, limit: int) -> list[str]:
        url = (
            "https://www.google.com/maps/search/"
            f"{quote(query)}?hl={self.language}&gl=us"
        )
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
            # A single result opens the place page directly.
            if "/maps/place/" in page.url:
                return [page.url]
            raise MapsProviderError("Could not find the Maps results list.")

        feed = page.locator('div[role="feed"]')
        seen: list[str] = []
        idle_rounds = 0
        for _ in range(60):
            hrefs = feed.locator('a[href*="/maps/place/"]').evaluate_all(
                "els => els.map(e => e.href)"
            )
            before = len(seen)
            seen = unique(seen + list(hrefs))
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
        return seen

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

        return Restaurant(
            title=clean_whitespace(title),
            address=address,
            phone=phone,
            website=website,
            google_maps_url=page.url,
            category=category,
            source_query=query,
        )

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
