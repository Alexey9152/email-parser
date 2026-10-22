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


def test_plan_starts_with_facebook_and_respects_budget():
    plan = SocialEmailFinder.plan("Coastal Salt", "Ocean City, MD", max_queries=2)
    assert len(plan) == 2
    assert plan[0][0] == "facebook"
    assert "site:facebook.com" in plan[0][1]
    assert '"Ocean City, MD"' in plan[0][1]


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
            SearchHit(title="Coastal Salt", snippet="hello@coastalsaltoc.com"),
        ]
    }
    result = finder.evaluate("Coastal Salt", plan, hits)
    assert result.emails == ["hello@coastalsaltoc.com"]
    assert result.source == "web"
