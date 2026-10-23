"""Maps-stage filtering rules."""

from __future__ import annotations

from email_parser.config import Settings
from email_parser.models import (
    SOURCE_FB_PAGE,
    SOURCE_OSM,
    SOURCE_SITE,
    ContactResult,
    Restaurant,
)
from email_parser.pipeline import (
    _add_emails,
    _facebook_page_matches,
    _needs_better_email,
    build_search_queries,
    enrich_restaurants,
    filter_restaurants,
    is_own_website,
)
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
    assert city_from_query("Ocean City, MD") == "Ocean City, MD"


def test_facebook_email_matches_website_domain():
    # Regression: facebook.com/longboardcafemd lists info@longboardcafe.net.
    # The city suffix in the slug must not make the address look foreign.
    assert _facebook_page_matches(
        "https://www.facebook.com/longboardcafemd",
        ["info@longboardcafe.net"],
        website="https://www.longboardcafe.net/",
        title="Longboard Cafe",
    )


def test_facebook_email_matches_title_tokens_without_website():
    assert _facebook_page_matches(
        "https://www.facebook.com/longboardcafemd",
        ["info@longboardcafe.net"],
        title="Longboard Cafe",
    )


def test_facebook_unrelated_email_is_still_rejected():
    assert not _facebook_page_matches(
        "https://www.facebook.com/joesdiner",
        ["info@random-company.com"],
        website="",
        title="Joe's Diner",
    )


def test_facebook_title_match_on_corporate_domain_is_not_enough():
    # facebook.com/TheBonfireSXM is a SiriusXM radio show; the restaurant at
    # 7001 Coastal Highway uses thebonfirerestaurant@gmail.com instead.
    assert not _facebook_page_matches(
        "https://www.facebook.com/TheBonfireSXM",
        ["thebonfire@siriusxm.com"],
        title="The Bonfire",
    )
    assert _facebook_page_matches(
        "https://www.facebook.com/TheBonfireSXM",
        ["thebonfirerestaurant@gmail.com"],
        title="The Bonfire",
    )


def test_facebook_consumer_mailbox_matches_venue_title():
    # Regression: facebook.com/DoughRollerRestaurants has no website and lists
    # doughrollerocmd@gmail.com; the old slug-only check rejected it.
    assert _facebook_page_matches(
        "https://www.facebook.com/DoughRollerRestaurants",
        ["doughrollerocmd@gmail.com"],
        title="The Dough Roller",
    )


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


def test_email_stages_merge_and_prefer_hiring_address():
    row = ContactResult(
        title="Joe's",
        website="https://joesdiner.com",
        emails=["info@joesdiner.com"],
        email_sources={"info@joesdiner.com": SOURCE_SITE},
    )
    assert _needs_better_email(row)

    # Duplicate info@ is not added twice; the manager address comes from FB.
    added = _add_emails(
        row, ["manager@joesdiner.com", "INFO@joesdiner.com"], SOURCE_FB_PAGE
    )
    assert added == ["manager@joesdiner.com"]
    assert row.email_sources["manager@joesdiner.com"] == SOURCE_FB_PAGE
    assert row.emails.count("info@joesdiner.com") == 1
    assert not _needs_better_email(row)
    assert row.primary_email == "manager@joesdiner.com"


def test_osm_email_is_used_without_website_or_search():
    restaurant = Restaurant(title="Joe's Kiosk", email="owner@joeskiosk.com")
    results, stats = enrich_restaurants(
        Settings(http_workers=1),
        [restaurant],
        use_search=False,
        facebook_browser=False,
    )
    assert results[0].primary_email == "owner@joeskiosk.com"
    assert results[0].primary_email_source == SOURCE_OSM
    assert results[0].status == "Found on OpenStreetMap"
    assert stats.from_osm == 1
