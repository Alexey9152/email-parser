"""Maps-stage filtering rules (TODO 1.2.3)."""

from __future__ import annotations

from email_parser.models import Restaurant
from email_parser.pipeline import filter_restaurants, is_own_website
from email_parser.utils import city_from_query, registrable_domain


def venue(title: str, website: str) -> Restaurant:
    return Restaurant(title=title, website=website, phone="555")


def test_requires_website():
    items = [venue("Good", "https://good.com"), venue("No site", "")]
    assert [r.title for r in filter_restaurants(items)] == ["Good"]


def test_drops_aggregator_domains():
    items = [
        venue("Yelp page", "https://www.yelp.com/biz/joes"),
        venue("FB page", "https://facebook.com/joes"),
        venue("Real", "https://joesdiner.com"),
    ]
    assert [r.title for r in filter_restaurants(items)] == ["Real"]


def test_dedupes_by_domain_and_title():
    items = [
        venue("Joe's Diner", "https://joesdiner.com"),
        venue("Joe's Diner Inc", "https://www.joesdiner.com/menu"),
        venue("Joe's Diner", "https://joesdiner.net"),
    ]
    assert len(filter_restaurants(items)) == 1


def test_limit_and_helpers():
    items = [venue(f"Place {i}", f"https://place{i}.com") for i in range(5)]
    assert len(filter_restaurants(items, limit=3)) == 3
    assert is_own_website("https://place1.com") is True
    assert registrable_domain("https://menu.joes.co.uk/x") == "joes.co.uk"
    assert city_from_query("restaurants in Ocean City, MD") == "Ocean City, MD"
