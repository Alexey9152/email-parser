"""Map area helpers: bbox parsing, GeoJSON polygon, coordinate parsing."""

from __future__ import annotations

import pytest

from email_parser.utils import Area, coords_from_maps_url, parse_bbox


def test_parse_bbox_order_is_south_west_north_east():
    area = parse_bbox("38.30,-75.10,38.40,-75.03")
    assert area is not None
    assert area.south == 38.30
    assert area.west == -75.10
    assert area.north == 38.40
    assert area.east == -75.03
    assert area.as_text() == "38.300000,-75.100000,38.400000,-75.030000"


def test_parse_bbox_rejects_wrong_input():
    assert parse_bbox("") is None
    with pytest.raises(ValueError):
        parse_bbox("1,2,3")
    with pytest.raises(ValueError):
        parse_bbox("40,10,30,20")  # south must be below north
    with pytest.raises(ValueError):
        parse_bbox("a,b,c,d")


def test_polygon_is_closed_and_lng_lat():
    area = Area(south=1.0, west=2.0, north=3.0, east=4.0)
    ring = area.polygon()
    assert ring[0] == ring[-1] == [2.0, 1.0]
    assert area.geojson()["type"] == "Polygon"
    assert area.geojson()["coordinates"] == [ring]


def test_contains_and_center():
    area = Area(south=10.0, west=20.0, north=12.0, east=22.0)
    assert area.contains(11.0, 21.0)
    assert not area.contains(9.0, 21.0)
    assert area.center == (11.0, 21.0)


def test_coords_from_maps_url():
    place = "https://www.google.com/maps/place/X/data=!4m5!3d38.3365!4d-75.0849"
    assert coords_from_maps_url(place) == (38.3365, -75.0849)
    assert coords_from_maps_url("https://www.google.com/maps/@38.3,-75.1,14z") == (38.3, -75.1)
    assert coords_from_maps_url("https://example.com") is None
