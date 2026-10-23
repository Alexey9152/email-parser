"""Headless-browser fallback for heavy-JS / anti-bot websites.

Static HTTP parsing misses React/Vue sites and pages that only reveal an e-mail
after a cookie banner or a "show contacts" click. This renderer loads the page
in Chromium, optionally opens the contact page and re-extracts everything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin

from .extractors import emails_from_page, facebook_from_html
from .utils import normalize_url, unique

log = logging.getLogger(__name__)

CONTACT_HREF_HINTS = ("contact", "about", "kontakt", "reach", "impressum")


@dataclass(slots=True)
class RenderResult:
    url: str
    emails: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    blocked: bool = False
    error: str = ""
    # A Facebook page to open in the browser afterwards.  Populated from the
    # rendered DOM for ordinary sites and from the input URL for Facebook pages.
    facebook_url: str = ""


class PlaywrightSiteRenderer:
    """Reuse one Chromium instance for many sites (sync API, main thread only)."""

    def __init__(self, headless: bool = True, timeout_ms: int = 30_000):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None
        self._context = None

    def __enter__(self) -> "PlaywrightSiteRenderer":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._context = self._browser.new_context(
            locale="en-US",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        return self

    def __exit__(self, *exc_info) -> None:
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()
            self._context = self._browser = self._pw = None

    # -- public ------------------------------------------------------------
    def analyze(self, website: str, *, follow_contact: bool = True) -> RenderResult:
        start = normalize_url(website)
        result = RenderResult(url=start or website)
        if not start or not self._context:
            result.error = "invalid url or renderer not started"
            return result

        is_facebook = any(
            marker in start.lower()
            for marker in ("facebook.com", "fb.me", "fb.com")
        )
        page = self._context.new_page()
        try:
            result.emails = self._visit(page, start, result.pages)
            if is_facebook:
                # The address is usually in "Intro / About" behind "See more".
                result.facebook_url = start
                result.emails = unique(
                    result.emails + self._facebook_expand(page)
                )
            else:
                # Many small venues only expose their Facebook link in the DOM
                # or inside a JSON-LD block; remember it for the Facebook stage.
                result.facebook_url = self._facebook_from_page(page)
                if not result.emails and follow_contact:
                    for link in self._contact_hrefs(page, limit=2):
                        if len(result.pages) >= 3:
                            break
                        emails = self._visit(page, link, result.pages)
                        if not result.facebook_url:
                            result.facebook_url = self._facebook_from_page(page)
                        if emails:
                            result.emails = unique(result.emails + emails)
                            break
            result.blocked = self._is_blocked(page)
        except Exception as exc:  # noqa: BLE001 - browser errors are expected
            result.error = str(exc)[:200]
            log.debug("render failed for %s: %s", start, exc)
        finally:
            page.close()
        result.emails = unique(result.emails)
        return result

    # -- internals ---------------------------------------------------------
    def _visit(self, page, url: str, pages: list[str]) -> list[str]:
        page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        page.wait_for_timeout(1200)
        pages.append(page.url)

        emails: list[str] = []
        for frame in page.frames:
            try:
                found, _ = emails_from_page(frame.content())
                emails.extend(found)
            except Exception:
                continue
        if not emails:
            try:
                emails.extend(emails_from_page(page.content())[0])
            except Exception:
                pass
        if not emails:
            emails.extend(self._click_reveal_buttons(page))
        return unique(emails)

    def _facebook_from_page(self, page) -> str:
        """Return the best Facebook profile link found in the rendered page."""
        title = ""
        try:
            title = page.title()
        except Exception:
            pass
        for frame in page.frames:
            try:
                found = facebook_from_html(frame.content(), title)
            except Exception:
                continue
            if found:
                return found
        return ""

    def _contact_hrefs(self, page, limit: int = 2) -> list[str]:
        hrefs: list[str] = []
        try:
            anchors = page.locator("a[href]")
            for index in range(min(anchors.count(), 400)):
                href = (anchors.nth(index).get_attribute("href") or "").strip()
                if not href:
                    continue
                lowered = href.lower()
                if any(hint in lowered for hint in CONTACT_HREF_HINTS):
                    absolute = urljoin(page.url, href)
                    if absolute.startswith("http") and absolute not in hrefs:
                        hrefs.append(absolute)
                if len(hrefs) >= limit:
                    break
        except Exception:
            pass
        return hrefs

    def _facebook_expand(self, page) -> list[str]:
        """Open the "Intro / About" block and re-extract the page HTML."""
        found: list[str] = []
        try:
            page.wait_for_timeout(800)
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(700)

            for selector in (
                'div[role="button"]:has-text("See more")',
                'span:has-text("See more")',
                '[aria-label*="See more" i]',
            ):
                locator = page.locator(selector)
                for index in range(min(2, locator.count())):
                    try:
                        locator.nth(index).click(timeout=1_500)
                        page.wait_for_timeout(600)
                    except Exception:
                        continue
                if found:
                    break

            # Some pages expose the address only on the About tab.
            for selector in (
                'a[href*="sk=about"]',
                'a[href$="/about"]',
            ):
                locator = page.locator(selector)
                if not locator.count():
                    continue
                try:
                    locator.first.click(timeout=2_500)
                    page.wait_for_load_state("domcontentloaded", timeout=8_000)
                    page.wait_for_timeout(1_200)
                    break
                except Exception:
                    continue

            found.extend(emails_from_page(page.content())[0])
        except Exception:
            pass
        return unique(found)

    def _click_reveal_buttons(self, page) -> list[str]:
        """Best effort: click up to 3 buttons whose label hints at contacts."""
        found: list[str] = []
        try:
            candidates = page.locator(
                'button:has-text("Contact"), button:has-text("Email"), '
                'button:has-text("Show"), [aria-label*="contact" i], [aria-label*="email" i]'
            )
            for index in range(min(candidates.count(), 3)):
                button = candidates.nth(index)
                try:
                    button.click(timeout=2_000)
                    page.wait_for_timeout(700)
                    found.extend(emails_from_page(page.content())[0])
                except Exception:
                    continue
        except Exception:
            pass
        return unique(found)

    @staticmethod
    def _is_blocked(page) -> bool:
        try:
            text = (page.content() or "")[:20_000].lower()
        except Exception:
            return False
        return any(
            marker in text
            for marker in ("just a moment", "captcha", "are you a robot", "access denied")
        )
