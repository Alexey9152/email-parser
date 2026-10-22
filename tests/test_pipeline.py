"""Maps-stage filtering rules."""

from __future__ import annotations

from email_parser.models import Restaurant
from email_parser.pipeline import build_search_queries, filter_restaurants, is_own_website
from email_parser.utils import Area, city_from_query, registrable_domain


def venue(title: str, website: str, address: str = "") -> Restaurant:
    return Restaurant(title=title, website=website, phone="555", address=address)


def test_keeps_venues_without_website():
    items = [venue("Good", "https://good.com"), venue("No site", "")]
    assert [r.title for r in filter_restaurants(items)] == ["Good", "No site"]


def test_only_with_website_flag_restores_old_behaviour():
    items = [venue("Good", "https://good.com"), venue("No site", "")]
    assert [r.title for r in filter_restaurants(items, require_website=True)] == ["Good"]


def test_aggregator_page_is_kept_as_facebook_hint():
    items = [
        venue("Yelp page", "https://www.yelp.com/biz/joes"),
        venue("FB page", "https://facebook.com/joes"),
        venue("Real", "https://joesdiner.com"),
    ]
    cleaned = filter_restaurants(items)
    assert [r.title for r in cleaned] == ["Yelp page", "FB page", "Real"]
    fb = next(r for r in cleaned if r.title == "FB page")
    assert fb.website == ""
    assert fb.facebook_url == "https://www.facebook.com/joes"


def test_dedupes_by_domain_and_title():
    items = [
        venue("Joe's Diner", "https://joesdiner.com"),
        venue("Joe's Diner Inc", "https://www.joesdiner.com/menu"),
        venue("Joe's Diner", "https://joesdiner.net"),
    ]
    assert len(filter_restaurants(items)) == 1


def test_dedupes_by_place_id():
    items = [
        Restaurant(title="A", place_id="same"),
        Restaurant(title="A", place_id="same", website="https://a.com"),
    ]
    assert len(filter_restaurants(items)) == 1


def test_limit_and_helpers():
    items = [venue(f"Place {i}", f"https://place{i}.com") for i in range(5)]
    assert len(filter_restaurants(items, limit=3)) == 3
    assert is_own_website("https://place1.com") is True
    assert registrable_domain("https://menu.joes.co.uk/x") == "joes.co.uk"
    assert city_from_query("restaurants in Ocean City, MD") == "Ocean City, MD"


def test_search_queries_follow_the_drawn_area():
    area = Area(south=38.3, west=-75.1, north=38.4, east=-75.0)
    # The polygon defines the location: no redundant "in City" suffix.
    assert build_search_queries("Ocean City, MD", ["ice cream", "shawarma"], area) == [
        "ice cream",
        "shawarma",
    ]
    assert build_search_queries("Ocean City, MD", ["ice cream"], None) == [
        "ice cream in Ocean City, MD"
    ]
    assert build_search_queries("cafes in Austin, TX") == ["cafes in Austin, TX"]
    assert build_search_queries("", ["ice cream"], area) == ["ice cream"]
