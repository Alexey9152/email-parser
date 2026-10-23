"""OpenStreetMap provider: tag mapping and area handling (no network)."""

from __future__ import annotations

from email_parser.maps.osm import OsmProvider
from email_parser.utils import Area


def test_osm_element_maps_to_restaurant():
    element = {
        "type": "node",
        "id": 123,
        "lat": 38.35,
        "lon": -75.08,
        "tags": {
            "name": "OC Uptown Eats",
            "amenity": "fast_food",
            "cuisine": "seafood",
            "addr:housenumber": "14107",
            "addr:street": "Coastal Hwy",
            "addr:city": "Ocean City",
            "addr:state": "MD",
            "addr:postcode": "21842",
            "phone": "+1 443-555-0101",
            "website": "https://ocuptowneats.com/",
            "contact:facebook": "https://www.facebook.com/409248996222954",
            "contact:email": "alluse@aol.com",
        },
    }
    item = OsmProvider()._to_restaurant(element, "fast food in Ocean City, MD")

    assert item is not None
    assert item.title == "OC Uptown Eats"
    assert item.address == "14107 Coastal Hwy, Ocean City, MD, 21842"
    assert item.phone == "+1 443-555-0101"
    assert item.website == "https://ocuptowneats.com/"
    assert item.email == "alluse@aol.com"
    assert item.facebook_url == "https://www.facebook.com/409248996222954"
    assert item.category == "fast food, seafood"
    assert item.place_id == "node/123"
    assert item.latitude == 38.35
    assert item.longitude == -75.08
    assert item.google_maps_url.startswith(
        "https://www.google.com/maps/search/?api=1&query="
    )


def test_osm_skips_unnamed_elements():
    assert (
        OsmProvider()._to_restaurant(
            {"type": "node", "id": 1, "tags": {"amenity": "cafe"}},
            "cafe in Ocean City",
        )
        is None
    )


def test_osm_splits_large_areas_into_tiles():
    area = Area(south=38.0, west=-75.5, north=39.0, east=-74.5)
    tiles = OsmProvider()._tiles(area)
    assert len(tiles) == 9
    assert all(tile.south >= area.south and tile.north <= area.north for tile in tiles)
    assert all(tile.west >= area.west and tile.east <= area.east for tile in tiles)


def test_osm_geocode_uses_nominatim_bbox_order(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return [
                {
                    "boundingbox": ["38.0", "38.5", "-75.5", "-74.5"],
                    "display_name": "Ocean City, MD, USA",
                }
            ]

    monkeypatch.setattr(
        "email_parser.maps.osm.httpx.get", lambda *args, **kwargs: Response()
    )
    area = OsmProvider()._geocode("restaurants in Ocean City, MD")
    assert (area.south, area.north, area.west, area.east) == (
        38.0,
        38.5,
        -75.5,
        -74.5,
    )


def test_osm_query_contains_food_tags_and_bbox():
    area = Area(south=38.30, west=-75.10, north=38.40, east=-75.00)
    query = OsmProvider._query("amenity", "restaurant|cafe", area, 500)
    assert 'nwr["amenity"~"^(restaurant|cafe)$"]' in query
    assert "(38.300000,-75.100000,38.400000,-75.000000)" in query
    assert "out center tags 500" in query
