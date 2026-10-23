"""Facebook links supplied by map providers are kept for the FB stage."""

from __future__ import annotations

from email_parser.maps.apify import ApifyProvider
from email_parser.maps.outscraper import OutscraperProvider
from email_parser.maps.social import facebook_from_payload


def test_facebook_from_payload_handles_common_provider_fields():
    assert (
        facebook_from_payload(
            {"facebook": "https://www.facebook.com/bigpeckersoc/"}
        )
        == "https://www.facebook.com/bigpeckersoc"
    )
    assert (
        facebook_from_payload(
            {"socialLinks": [{"url": "https://www.facebook.com/coastalsaltoc"}]}
        )
        == "https://www.facebook.com/coastalsaltoc"
    )
    assert (
        facebook_from_payload(
            {"about": {"details": {"facebookUrl": "https://fb.me/grottopizza"}}}
        )
        == "https://www.facebook.com/grottopizza"
    )
    assert facebook_from_payload({"website": "https://example.com"}) == ""


def test_apify_mapping_keeps_facebook_url():
    item = {
        "title": "Big Pecker's",
        "website": "https://www.bigpeckersoc.com/",
        "social": {"facebook": "https://www.facebook.com/bigpeckersoc/"},
    }
    row = ApifyProvider._to_restaurant(item, "restaurants in Ocean City, MD")
    assert row is not None
    assert row.facebook_url == "https://www.facebook.com/bigpeckersoc"


def test_outscraper_mapping_keeps_facebook_url():
    item = {
        "name": "Grotto Pizza",
        "site": "https://www.grottopizza.com/",
        "facebook_url": "https://www.facebook.com/GrottoPizzaDE",
    }
    row = OutscraperProvider._to_restaurant(item, "pizza in Rehoboth Beach, DE")
    assert row is not None
    assert row.facebook_url == "https://www.facebook.com/GrottoPizzaDE"
