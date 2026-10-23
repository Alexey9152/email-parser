"""Personalized mailer: ranking, Excel round-trip and message building."""

from __future__ import annotations

from email_parser.config import Settings
from email_parser.exporter import load_results_xlsx, save_results_xlsx
from email_parser.mailer import (
    DEFAULT_TEMPLATE,
    build_message,
    describe_source,
    found_note,
    normalize_statuses,
    select_recipients,
)
from email_parser.models import (
    SOURCE_FB_PAGE,
    SOURCE_GUESS,
    SOURCE_OSM,
    SOURCE_SEARCH,
    SOURCE_SITE,
    STATUS_FB,
    STATUS_GUESS,
    STATUS_OSM,
    STATUS_SITE,
    ContactResult,
)


def make_row(**kwargs) -> ContactResult:
    base = dict(
        title="Coastal Salt",
        website="https://coastalsaltoc.com",
        facebook_url="https://www.facebook.com/CoastalSaltOC",
        emails=["info@coastalsaltoc.com", "manager@coastalsaltoc.com"],
        email_sources={
            "info@coastalsaltoc.com": SOURCE_SITE,
            "manager@coastalsaltoc.com": SOURCE_FB_PAGE,
        },
        status=STATUS_FB,
    )
    base.update(kwargs)
    return ContactResult(**base)


def test_message_mentions_restaurant_email_and_facebook_source():
    row = make_row()
    settings = Settings(mail_from="alex@example.com", mail_from_name="Alex Los")
    message = build_message(row, settings=settings, template=DEFAULT_TEMPLATE)
    body = message.get_body(preferencelist=("plain",)).get_content()

    assert message["To"] == "manager@coastalsaltoc.com"
    assert message["Subject"] == "Work & Travel application — Coastal Salt"
    assert "team at Coastal Salt" in body
    assert "manager@coastalsaltoc.com" in body
    assert "on your Facebook page" in body
    assert "https://www.facebook.com/CoastalSaltOC" in body
    assert "Alex Los" in body
    assert "June 28, 2027" in body
    # No resume was passed, so the letter must not claim one is attached.
    assert "attached my Resume" not in body


def test_message_mentions_website_source():
    row = make_row(
        emails=["manager@coastalsaltoc.com"],
        email_sources={"manager@coastalsaltoc.com": SOURCE_SITE},
        status=STATUS_SITE,
        facebook_url="",
    )
    phrase, link = describe_source(row)
    assert phrase == "on your website"
    assert link == "https://coastalsaltoc.com"


def test_osm_source_phrase_and_link():
    row = make_row(
        emails=["owner@coastalsaltoc.com"],
        email_sources={"owner@coastalsaltoc.com": SOURCE_OSM},
        status=STATUS_OSM,
        google_maps_url="https://www.google.com/maps/search/?api=1&query=Coastal+Salt",
    )
    phrase, link = describe_source(row)
    assert phrase == "in the public OpenStreetMap listing for your business"
    assert link.startswith("https://www.google.com/maps/search/")
    note = found_note(row)
    assert "OpenStreetMap" in note
    assert "owner@coastalsaltoc.com" in note


def test_search_source_phrase_contains_venue_name():
    row = make_row(
        emails=["hello@coastalsaltoc.com"],
        email_sources={"hello@coastalsaltoc.com": SOURCE_SEARCH},
        status="Found via web search",
    )
    phrase, _link = describe_source(row)
    assert "public online listing for Coastal Salt" in phrase


def test_attachment_is_added(tmp_path):
    resume = tmp_path / "Alex_Los_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    settings = Settings(mail_from="alex@example.com")
    message = build_message(
        make_row(),
        settings=settings,
        template=DEFAULT_TEMPLATE,
        resume_path=resume,
    )
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "Alex_Los_Resume.pdf"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "attached my Resume" in body


def test_select_recipients_skips_guessed_duplicates_and_sent():
    first = make_row()
    second = make_row(title="Another", emails=["info@another.com"], email_sources={})
    guessed = make_row(
        title="Guess",
        emails=["info@guess.com"],
        email_sources={"info@guess.com": SOURCE_GUESS},
        status=STATUS_GUESS,
    )
    duplicate = make_row(
        title="Duplicate",
        emails=["info@another.com"],
        email_sources={},
    )

    selected, stats = select_recipients(
        [first, second, guessed, duplicate],
        sent={"manager@coastalsaltoc.com": {"status": "sent"}},
    )
    emails = [row.primary_email for row in selected]
    assert emails == ["info@another.com"]
    assert stats["guessed"] == 1
    assert stats["duplicate"] == 1
    assert stats["already_sent"] == 1


def test_status_filter_accepts_russian_labels():
    assert normalize_statuses("Найден на Facebook") == {STATUS_FB}
    assert normalize_statuses("Found on site") == {STATUS_SITE}


def test_xlsx_round_trip_keeps_primary_email_and_source(tmp_path):
    row = make_row()
    path = tmp_path / "report.xlsx"
    save_results_xlsx(path, [row])
    loaded = load_results_xlsx(path)

    assert len(loaded) == 1
    assert loaded[0].primary_email == "manager@coastalsaltoc.com"
    assert loaded[0].primary_email_source == SOURCE_FB_PAGE
    assert loaded[0].primary_email_source_url == "https://www.facebook.com/CoastalSaltOC"
    assert loaded[0].title == "Coastal Salt"
