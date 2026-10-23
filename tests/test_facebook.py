"""Facebook / web search query planning and hit evaluation."""

from __future__ import annotations

from email_parser.facebook import SocialEmailFinder, filter_email_list
from email_parser.serp import SearchHit


def test_filter_email_list_drops_directories_but_keeps_venue():
    emails = [
        "info@coastalsaltoc.com",
        "noreply@facebook.com",
        "support@yelp.com",
        "no-reply@venue.com",
    ]
    assert filter_email_list(emails) == ["info@coastalsaltoc.com"]


def test_plan_starts_with_broad_free_search_friendly_facebook_query():
    plan = SocialEmailFinder.plan("Big Pecker's", "Ocean City, MD", max_queries=2)
    assert len(plan) == 2
    assert plan[0][0] == "facebook"
    # Free engines often ignore site: and choke on an exact phrase + "email";
    # the first query is deliberately broad and unquoted.
    assert "site:facebook.com" not in plan[0][1]
    assert "facebook" in plan[0][1]
    assert "email" not in plan[0][1]
    assert "Big Pecker's" in plan[0][1]
    assert "Ocean City, MD" in plan[0][1]


def test_plan_keeps_precise_site_query_later():
    plan = SocialEmailFinder.plan("Coastal Salt", "Ocean City, MD", max_queries=4)
    assert any("site:facebook.com" in query for _kind, query in plan)


def test_plan_adds_phone_query():
    plan = SocialEmailFinder.plan("Joe's", phone="(410) 555-0100", max_queries=5)
    assert any("(410) 555-0100" in query for _kind, query in plan)


def test_evaluate_extracts_email_and_facebook_url():
    plan = SocialEmailFinder.plan("Coastal Salt", "Ocean City", max_queries=2)
    hits = {
        plan[0][1]: [
            SearchHit(
                title="Coastal Salt | Ocean City MD",
                snippet="Write to info@coastalsaltoc.com",
                link="https://www.facebook.com/CoastalSaltOC/",
            )
        ]
    }
    result = SocialEmailFinder(None).evaluate("Coastal Salt", plan, hits, backend="google")
    assert result.emails == ["info@coastalsaltoc.com"]
    assert result.source == "facebook"
    assert result.facebook_url == "https://www.facebook.com/CoastalSaltOC"


def test_evaluate_prefers_profile_over_post_url():
    plan = SocialEmailFinder.plan("Coastal Salt", max_queries=1)
    hits = {
        plan[0][1]: [
            SearchHit(title="Coastal Salt", link="https://www.facebook.com/CoastalSaltOC/posts/123"),
            SearchHit(title="Coastal Salt", link="https://www.facebook.com/CoastalSaltOC/"),
        ]
    }
    result = SocialEmailFinder(None).evaluate("Coastal Salt", plan, hits)
    assert result.facebook_url == "https://www.facebook.com/CoastalSaltOC"


def test_web_hits_must_be_relevant():
    finder = SocialEmailFinder(None)
    plan = [("web", "q")]
    hits = {
        "q": [
            SearchHit(title="Some other restaurant", snippet="owner@example.com"),
            SearchHit(
                title="Coastal Salt",
                snippet="hello@coastalsaltoc.com",
                link="https://coastalsaltoc.com/contact",
            ),
        ]
    }
    result = finder.evaluate(
        "Coastal Salt",
        plan,
        hits,
        restaurant_website="https://coastalsaltoc.com/",
    )
    assert result.emails == ["hello@coastalsaltoc.com"]
    assert result.source == "web"


def test_city_facebook_page_email_is_rejected():
    """A city/tourism page must not donate menuocity@gmail.com to a restaurant."""
    finder = SocialEmailFinder(None)
    plan = [("facebook", "q")]
    hits = {
        "q": [
            SearchHit(
                # The title may mention the venue even when the URL is the city
                # page; the mailbox check must still reject its address.
                title="Ocean City Fish Company - Ocean City, MD | Facebook",
                snippet="Contact the town: menuocity@gmail.com",
                link="https://www.facebook.com/OceanCityMD/",
            )
        ]
    }
    result = finder.evaluate(
        "Ocean City Fish Company",
        plan,
        hits,
        city="Ocean City, MD",
    )
    assert result.emails == []
    assert result.facebook_url == ""
    assert result.unreliable


def test_legacy_pages_url_is_recognized_as_venue_page():
    finder = SocialEmailFinder(None)
    plan = [("facebook", "q")]
    hits = {
        "q": [
            SearchHit(
                title="Coastal Salt | Facebook",
                snippet="info@coastalsaltoc.com",
                link="https://www.facebook.com/pages/Coastal-Salt/123456",
            )
        ]
    }
    result = finder.evaluate("Coastal Salt", plan, hits, city="Ocean City, MD")
    assert result.emails == ["info@coastalsaltoc.com"]
    assert result.facebook_url == "https://www.facebook.com/pages/Coastal-Salt/123456"


def test_shortened_page_slug_keeps_venue_email():
    finder = SocialEmailFinder(None)
    plan = [("facebook", "q")]
    hits = {
        "q": [
            SearchHit(
                title="Macky's | Facebook",
                snippet="info@mackys.com",
                link="https://www.facebook.com/Mackys/",
            )
        ]
    }
    result = finder.evaluate(
        "Macky's Bayside Bar and Grill",
        plan,
        hits,
        city="Ocean City, MD",
    )
    assert result.emails == ["info@mackys.com"]
    assert result.facebook_url == "https://www.facebook.com/Mackys"


def test_pg_url_is_canonicalized_to_a_venue_page():
    finder = SocialEmailFinder(None)
    plan = [("facebook", "q")]
    hits = {
        "q": [
            SearchHit(
                title="Coastal Salt | Facebook",
                snippet="info@coastalsaltoc.com",
                link="https://www.facebook.com/pg/Coastal-Salt",
            )
        ]
    }
    result = finder.evaluate("Coastal Salt", plan, hits, city="Ocean City, MD")
    assert result.emails == ["info@coastalsaltoc.com"]
    assert result.facebook_url == "https://www.facebook.com/Coastal-Salt"


def test_venue_facebook_page_email_is_kept():
    finder = SocialEmailFinder(None)
    plan = [("facebook", "q")]
    hits = {
        "q": [
            SearchHit(
                title="Ocean City Fish Company | Facebook",
                snippet="info@oceancityfishcompany.com",
                link="https://www.facebook.com/OceanCityFishCompany/",
            )
        ]
    }
    result = finder.evaluate(
        "Ocean City Fish Company",
        plan,
        hits,
        city="Ocean City, MD",
    )
    assert result.emails == ["info@oceancityfishcompany.com"]
    assert result.facebook_url == "https://www.facebook.com/OceanCityFishCompany"


def test_corporate_mailbox_mentioning_venue_is_rejected():
    finder = SocialEmailFinder(None)
    plan = [("facebook", "q")]
    hits = {
        "q": [
            SearchHit(
                title="The Bonfire | Facebook",
                snippet="thebonfire@siriusxm.com",
                link="https://www.facebook.com/TheBonfireSXM/",
            )
        ]
    }
    result = finder.evaluate("The Bonfire", plan, hits)
    assert result.emails == []


def test_aggregate_facebook_page_emails_are_rejected():
    """A category page lists dozens of venues: its snippet must be ignored."""
    finder = SocialEmailFinder(None)
    plan = [("facebook", "q")]
    hits = {
        "q": [
            SearchHit(
                title="Seafood Restaurants in Ocean City, MD",
                snippet="pearsonsseafood@gmail.com alluse@aol.com",
                link="https://www.facebook.com/pages/category/Seafood-Restaurant/",
            )
        ]
    }
    result = finder.evaluate("OC Uptown Eats", plan, hits)
    assert result.emails == []
    assert result.unreliable


def test_web_hits_must_match_known_facebook_page():
    finder = SocialEmailFinder(None)
    plan = [("web", "q")]
    hits = {
        "q": [
            SearchHit(title="Some other restaurant", snippet="other@gmail.com"),
            SearchHit(
                title="OC Uptown Eats",
                snippet="alluse@aol.com",
                link="https://www.facebook.com/409248996222954",
            ),
        ]
    }
    result = finder.evaluate(
        "OC Uptown Eats",
        plan,
        hits,
        facebook_url="https://www.facebook.com/409248996222954",
    )
    assert result.emails == ["alluse@aol.com"]
    assert result.source == "web"
