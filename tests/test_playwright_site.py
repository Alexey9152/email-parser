"""Playwright renderer result contract and Facebook-link discovery (no browser)."""

from __future__ import annotations

from email_parser.playwright_site import PlaywrightSiteRenderer, RenderResult


class FakeFrame:
    def __init__(self, html: str):
        self.html = html

    def content(self) -> str:
        return self.html


class FakePage:
    def __init__(self, html: str, title: str = "Joe's Diner"):
        self.frames = [FakeFrame(html)]
        self._title = title

    def title(self) -> str:
        return self._title


def test_render_result_exposes_facebook_url():
    result = RenderResult(url="https://joesdiner.com")
    assert result.facebook_url == ""
    result.facebook_url = "https://www.facebook.com/joesdiner"
    assert result.facebook_url == "https://www.facebook.com/joesdiner"


def test_facebook_link_is_found_in_rendered_html():
    page = FakePage(
        '<footer><a href="https://www.facebook.com/joesdiner?ref=footer">FB</a></footer>'
    )
    assert (
        PlaywrightSiteRenderer()._facebook_from_page(page)
        == "https://www.facebook.com/joesdiner"
    )


def test_missing_facebook_link_is_empty():
    page = FakePage("<html><body>No social links here</body></html>")
    assert PlaywrightSiteRenderer()._facebook_from_page(page) == ""
