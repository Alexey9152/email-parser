"""Food & drink search categories used for city-wide / area-wide collection.

Google Maps caps a single search at roughly 120 results, so covering a whole
city means running many narrow category searches instead of one broad one.
"""

from __future__ import annotations

import math
import re

from .utils import clean_whitespace, unique

# Broad list: covers restaurants, drinks, desserts and street food stalls.
FOOD_CATEGORIES: tuple[str, ...] = (
    "restaurant",
    "cafe",
    "coffee shop",
    "bar",
    "pub",
    "cocktail bar",
    "wine bar",
    "beer garden",
    "fast food",
    "pizza",
    "burger",
    "sandwich shop",
    "deli",
    "bakery",
    "dessert shop",
    "ice cream",
    "donut shop",
    "juice bar",
    "smoothie",
    "breakfast",
    "brunch",
    "diner",
    "family restaurant",
    "steakhouse",
    "seafood",
    "sushi",
    "mexican",
    "italian",
    "chinese",
    "indian",
    "thai",
    "vietnamese",
    "korean",
    "greek",
    "middle eastern",
    "shawarma",
    "kebab",
    "falafel",
    "food truck",
    "food stand",
    "bistro",
    "grill",
    "barbecue",
    "chicken",
    "vegetarian",
    "vegan",
    "soup",
    "salad",
    "fish and chips",
    "tapas",
    "tea house",
    "candy store",
    "food court",
)

# Ready-made subsets for the UI / CLI.
CATEGORY_GROUPS: dict[str, tuple[str, ...]] = {
    "all": FOOD_CATEGORIES,
    "restaurants": (
        "restaurant", "diner", "bistro", "steakhouse", "grill", "family restaurant",
        "breakfast", "brunch", "seafood", "sushi", "pizza", "barbecue",
    ),
    "drinks": (
        "bar", "pub", "cocktail bar", "wine bar", "beer garden", "juice bar", "tea house",
    ),
    "sweets": (
        "cafe", "coffee shop", "bakery", "dessert shop", "ice cream", "donut shop",
        "candy store", "smoothie",
    ),
    "street": (
        "fast food", "food truck", "food stand", "shawarma", "kebab", "falafel",
        "sandwich shop", "burger",
    ),
    "cuisines": (
        "mexican", "italian", "chinese", "indian", "thai", "vietnamese",
        "korean", "greek", "middle eastern",
    ),
}

ALIASES = {
    "restaurants": "restaurant",
    "bars": "bar",
    "cafes": "cafe",
    "coffee": "coffee shop",
    "icecream": "ice cream",
    "ice-cream": "ice cream",
    "shawarma": "shawarma",
    "shaurma": "shawarma",
}


def resolve_location(query: str) -> str:
    """'restaurants in Ocean City, MD' -> 'Ocean City, MD'; keeps plain locations."""
    text = clean_whitespace(query)
    match = re.search(r"\b(?:in|near|around)\s+(.+)$", text, re.IGNORECASE)
    if match:
        return clean_whitespace(match.group(1))
    return text


def parse_categories(value: str | None) -> list[str]:
    """'ice cream, shawarma, Bars' -> ['ice cream', 'shawarma', 'bar']."""
    if not value:
        return []
    raw = [part.strip().lower() for part in re.split(r"[,;]", value) if part.strip()]
    resolved: list[str] = []
    for item in raw:
        if item in CATEGORY_GROUPS:
            resolved.extend(CATEGORY_GROUPS[item])
        else:
            resolved.append(ALIASES.get(item, item))
    return unique(resolved)


def build_queries(location: str, categories: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Turn a location + category list into Maps search strings."""
    place = resolve_location(location)
    if not place:
        return []
    if not categories:
        return [place]
    return [f"{category} in {place}" for category in unique(categories)]


def per_query_limit(total_limit: int, queries: int, headroom: float = 1.4) -> int:
    """How many places to ask per query so the final cap is reachable."""
    queries = max(1, queries)
    return max(5, math.ceil(total_limit * headroom / queries))
