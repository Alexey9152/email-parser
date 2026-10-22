"""E-mail extraction and filtering rules (TODO 2.1.2)."""

from __future__ import annotations

from email_parser.extractors import (
    collect_links,
    contact_links,
    emails_from_page,
    extract_emails,
    normalize_email,
    social_links,
    soup_from_html,
)


def test_extracts_simple_addresses():
    text = "Call us or write to Info@Example-Restaurant.COM for bookings."
    assert extract_emails(text) == ["info@example-restaurant.com"]


def test_drops_junk_and_asset_addresses():
    html = """
    <p>logo@2x.png sentry@wixpress.com name@example.com</p>
    <p>real.person@restaurant.co.uk</p>
    """
    emails, _ = emails_from_page(html)
    assert emails == ["real.person@restaurant.co.uk"]


def test_mailto_and_obfuscation():
    html = """
    <a href="mailto:booking@place.com?subject=Table">Book</a>
    <span>owner [at] place [dot] com</span>
    """
    emails, _ = emails_from_page(html)
    assert emails == ["booking@place.com", "owner@place.com"]


def test_normalize_email_strips_noise():
    assert normalize_email("  MAILTO:Hello@JoesDiner.com?subject=x ") == "hello@joesdiner.com"
    assert normalize_email("not-an-email") == ""
    assert normalize_email("hash1234567890abcdef@site.com") == ""


def test_contact_and_social_links():
    html = """
    <a href="/contact-us">Contact</a>
    <a href="/about">About us</a>
    <a href="https://www.facebook.com/joesdiner/">Facebook</a>
    <a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a>
    <a href="https://twitter.com/joesdiner">Twitter</a>
    """
    soup = soup_from_html(html)
    links = collect_links(soup, "https://joesdiner.com/")

    contacts = contact_links(links, "https://joesdiner.com/")
    assert [link.url for link in contacts][0] == "https://joesdiner.com/contact-us"

    socials = social_links(links)
    assert socials["facebook"] == "https://www.facebook.com/joesdiner/"
    assert socials["twitter"] == "https://twitter.com/joesdiner"


def test_json_ld_meta_and_script_emails():
    html = """
    <script type="application/ld+json">
      {"@type": "Restaurant", "name": "Joe's", "email": "book@joesdiner.com"}
    </script>
    <script>var parts = "owner\\u0040joesdiner.com";</script>
    <meta property="og:email" content="press@joesdiner.com">
    """
    emails, _ = emails_from_page(html)
    assert {"book@joesdiner.com", "owner@joesdiner.com", "press@joesdiner.com"} <= set(emails)


def test_percent_encoded_address():
    assert normalize_email("info%40joesdiner.com") == "info@joesdiner.com"
    assert extract_emails("mail: manager%40joesdiner.com") == ["manager@joesdiner.com"]
