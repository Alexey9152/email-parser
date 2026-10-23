"""Chain detection and corporate-address filtering."""

from __future__ import annotations

from email_parser.chains import detect_chain, filter_chain_emails
from email_parser.config import Settings
from email_parser.models import (
    STATUS_CHAIN,
    STATUS_OSM,
    Restaurant,
)
from email_parser.pipeline import enrich_restaurants, filter_restaurants


def test_detects_common_chains():
    assert detect_chain("Dunkin' #1234") == "Dunkin'"
    assert detect_chain("McDonald's Ocean City") == "McDonald's"
    assert detect_chain("7-Eleven") == "7-Eleven"
    assert detect_chain("Starbucks - Coastal Hwy") == "Starbucks"
    assert detect_chain("Subway") == "Subway"


def test_independent_venues_are_not_chains():
    assert detect_chain("Coastal Salt") == ""
    assert detect_chain("The Hobbit Restaurant") == ""
    assert detect_chain("Big Pecker's") == ""


def test_corporate_chain_mailbox_is_dropped_but_store_mailbox_is_kept():
    assert filter_chain_emails(
        ["info@dunkindonuts.com", "store1234@dunkindonuts.com"],
        "Dunkin'",
    ) == ["store1234@dunkindonuts.com"]
    assert filter_chain_emails(
        ["dunkinoc@gmail.com"],
        "Dunkin'",
    ) == ["dunkinoc@gmail.com"]
    assert filter_chain_emails(
        ["careers@inspirebrands.com", "store1234@dunkindonuts.com"],
        "Dunkin'",
    ) == ["store1234@dunkindonuts.com"]


def test_chain_locations_are_not_deduplicated_by_corporate_domain():
    rows = filter_restaurants(
        [
            Restaurant(
                title="Dunkin' #1",
                website="https://www.dunkindonuts.com/",
                address="1 Main St",
                place_id="place-1",
            ),
            Restaurant(
                title="Dunkin' #2",
                website="https://www.dunkindonuts.com/",
                address="2 Main St",
                place_id="place-2",
            ),
        ]
    )
    assert len(rows) == 2
    assert all(row.chain == "Dunkin'" for row in rows)


def test_chain_with_only_corporate_email_gets_separate_status():
    settings = Settings()
    results, stats = enrich_restaurants(
        settings,
        [
            Restaurant(
                title="Dunkin'",
                email="info@dunkindonuts.com",
            )
        ],
        use_search=False,
        facebook_browser=False,
        workers=1,
    )
    row = results[0]
    assert row.chain == "Dunkin'"
    assert row.emails == []
    assert row.status == STATUS_CHAIN
    assert stats.chains == 1
    assert stats.not_found == 0


def test_chain_keeps_location_specific_email():
    settings = Settings()
    results, _stats = enrich_restaurants(
        settings,
        [
            Restaurant(
                title="Dunkin'",
                email="store1234@dunkindonuts.com",
            )
        ],
        use_search=False,
        facebook_browser=False,
        workers=1,
    )
    row = results[0]
    assert row.chain == "Dunkin'"
    assert row.emails == ["store1234@dunkindonuts.com"]
    assert row.status == STATUS_OSM
