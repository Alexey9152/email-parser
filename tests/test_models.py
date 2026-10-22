"""Primary e-mail selection for Work & Travel applications."""

from __future__ import annotations

from email_parser.models import (
    SOURCE_FB_PAGE,
    SOURCE_GUESS,
    SOURCE_SITE,
    ContactResult,
    is_hiring_email,
    rank_emails,
    score_email,
)


def test_manager_beats_generic_info_even_when_info_is_first():
    emails = ["info@joesdiner.com", "manager@joesdiner.com"]
    sources = {email: SOURCE_SITE for email in emails}
    ranked = rank_emails(emails, sources, "https://joesdiner.com")
    assert ranked[0] == "manager@joesdiner.com"
    assert ranked[0] == ranked[0].lower()


def test_facebook_manager_beats_website_info():
    emails = ["info@joesdiner.com", "hiring@joesdiner.com"]
    sources = {
        "info@joesdiner.com": SOURCE_SITE,
        "hiring@joesdiner.com": SOURCE_FB_PAGE,
    }
    ranked = rank_emails(emails, sources, "https://joesdiner.com")
    assert ranked[0] == "hiring@joesdiner.com"


def test_reservations_is_a_worse_target_than_info():
    emails = ["reservations@joesdiner.com", "info@joesdiner.com"]
    ranked = rank_emails(emails, {}, "https://joesdiner.com")
    assert ranked[0] == "info@joesdiner.com"


def test_guessed_address_is_always_last():
    emails = ["info@joesdiner.com", "manager@joesdiner.com"]
    sources = {
        "info@joesdiner.com": SOURCE_SITE,
        "manager@joesdiner.com": SOURCE_GUESS,
    }
    ranked = rank_emails(emails, sources, "https://joesdiner.com")
    assert ranked[-1] == "manager@joesdiner.com"
    assert score_email("manager@joesdiner.com", SOURCE_GUESS) < score_email(
        "info@joesdiner.com", SOURCE_SITE
    )


def test_contact_result_exposes_primary_source():
    row = ContactResult(
        title="Joe's",
        website="https://joesdiner.com",
        facebook_url="https://www.facebook.com/joesdiner",
        emails=["info@joesdiner.com", "manager@joesdiner.com"],
        email_sources={
            "info@joesdiner.com": SOURCE_SITE,
            "manager@joesdiner.com": SOURCE_FB_PAGE,
        },
    )
    assert row.primary_email == "manager@joesdiner.com"
    assert row.primary_email_source == SOURCE_FB_PAGE
    assert row.primary_email_source_url == "https://www.facebook.com/joesdiner"
    assert is_hiring_email(row.primary_email)
    assert not is_hiring_email("info@joesdiner.com")
