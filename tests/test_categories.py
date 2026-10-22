"""Category expansion for city-wide collection."""

from __future__ import annotations

from email_parser.categories import (
    CATEGORY_GROUPS,
    FOOD_CATEGORIES,
    build_queries,
    parse_categories,
    per_query_limit,
    resolve_location,
)


def test_all_food_covers_niche_places():
    for expected in ("ice cream", "shawarma", "cocktail bar", "food truck", "kebab"):
        assert expected in FOOD_CATEGORIES


def test_parse_categories_expands_groups_and_aliases():
    parsed = parse_categories("sweets, shaurma, Bars")
    assert "ice cream" in parsed
    assert "shawarma" in parsed
    assert "bar" in parsed
    assert "bakery" in parsed


def test_build_queries_uses_location_and_dedupes():
    queries = build_queries("restaurants in Ocean City, MD", ["ice cream", "shawarma", "ice cream"])
    assert queries == ["ice cream in Ocean City, MD", "shawarma in Ocean City, MD"]


def test_build_queries_without_categories_keeps_location():
    assert build_queries("Ocean City, MD") == ["Ocean City, MD"]


def test_resolve_location():
    assert resolve_location("cafes in Austin, TX") == "Austin, TX"
    assert resolve_location("Boston, MA") == "Boston, MA"


def test_per_query_limit_has_headroom():
    assert per_query_limit(300, 20) >= 15
    assert per_query_limit(1, 50) == 5


def test_groups_reference_known_categories():
    for name, items in CATEGORY_GROUPS.items():
        assert items, name
        assert all(item in FOOD_CATEGORIES for item in items), name
