"""
Unit tests for the route corridor matching service.
"""

import math
from decimal import Decimal

import pytest

from fuelroute.constants import EARTH_RADIUS_MILES, ROUTE_CORRIDOR_BUFFER_MILES
from fuelroute.models import FuelStation
from fuelroute.services.route_matching import (
    bounding_box,
    find_candidate_stations,
    haversine_miles,
    point_to_segment_distance_miles,
    project_stations_onto_route,
)

# A synthetic straight-line route running due north along longitude
# -95.0, from lat 40.0 to lat 41.0 (GeoJSON (lon, lat) pairs).
STRAIGHT_ROUTE_COORDINATES = [(-95.0, 40.0), (-95.0, 41.0)]
STRAIGHT_ROUTE_GEOMETRY = {
    "type": "LineString",
    "coordinates": STRAIGHT_ROUTE_COORDINATES,
}

MILES_PER_DEGREE_LAT = 69.0


def _make_station(opis_id, lat, lon, name="Station"):
    return FuelStation.objects.create(
        opis_id=opis_id,
        name=name,
        address="123 Main St",
        city="Somewhere",
        state="KS",
        retail_price=Decimal("3.500"),
        latitude=Decimal(str(lat)) if lat is not None else None,
        longitude=Decimal(str(lon)) if lon is not None else None,
    )


class TestHaversineMiles:
    def test_zero_distance_for_identical_points(self):
        assert haversine_miles(40.0, -95.0, 40.0, -95.0) == pytest.approx(0.0, abs=1e-6)

    def test_one_degree_latitude_is_about_69_miles(self):
        distance = haversine_miles(40.0, -95.0, 41.0, -95.0)
        assert distance == pytest.approx(69.0, abs=1.0)

    def test_known_quarter_circumference(self):
        # Two points 90 degrees apart on the equator: a quarter of Earth's
        # circumference.
        distance = haversine_miles(0.0, 0.0, 0.0, 90.0)
        expected = (math.pi / 2) * EARTH_RADIUS_MILES
        assert distance == pytest.approx(expected, rel=1e-6)


class TestPointToSegmentDistanceMiles:
    def test_point_on_segment_has_zero_distance(self):
        distance = point_to_segment_distance_miles(
            (40.5, -95.0), (40.0, -95.0), (41.0, -95.0)
        )
        assert distance == pytest.approx(0.0, abs=1e-6)

    def test_point_offset_perpendicular_to_segment(self):
        # ~1 degree of longitude at lat 40 is compressed by cos(40deg).
        # Offset the point due east by roughly 2 miles.
        lon_delta = (2.0 / MILES_PER_DEGREE_LAT) / math.cos(math.radians(40.0))
        distance = point_to_segment_distance_miles(
            (40.5, -95.0 + lon_delta), (40.0, -95.0), (41.0, -95.0)
        )
        assert distance == pytest.approx(2.0, abs=1.0)

    def test_point_beyond_segment_end_clamps_to_endpoint(self):
        # Point due north of the segment's end point: nearest point should
        # clamp to the endpoint, not extrapolate past it.
        distance = point_to_segment_distance_miles(
            (42.0, -95.0), (40.0, -95.0), (41.0, -95.0)
        )
        expected = haversine_miles(42.0, -95.0, 41.0, -95.0)
        assert distance == pytest.approx(expected, abs=1e-6)


class TestBoundingBox:
    def test_expands_by_buffer_in_all_directions(self):
        min_lat, max_lat, min_lon, max_lon = bounding_box(
            STRAIGHT_ROUTE_COORDINATES, buffer_miles=2.0
        )
        lat_delta = 2.0 / MILES_PER_DEGREE_LAT
        mean_lat = 40.5
        lon_delta = lat_delta / math.cos(math.radians(mean_lat))

        assert min_lat == pytest.approx(40.0 - lat_delta)
        assert max_lat == pytest.approx(41.0 + lat_delta)
        assert min_lon == pytest.approx(-95.0 - lon_delta)
        assert max_lon == pytest.approx(-95.0 + lon_delta)


@pytest.mark.django_db
class TestFindCandidateStations:
    def test_excludes_stations_outside_bounding_box_and_with_null_coords(self):
        inside = _make_station(1, 40.5, -95.0, name="Inside")
        _make_station(2, 50.0, -95.0, name="FarNorth")
        FuelStation.objects.create(
            opis_id=3,
            name="NullCoords",
            address="1 St",
            city="Nowhere",
            state="KS",
            retail_price=Decimal("3.500"),
            latitude=None,
            longitude=None,
        )

        candidates = find_candidate_stations(STRAIGHT_ROUTE_GEOMETRY, buffer_miles=2.0)

        assert list(candidates) == [inside]


@pytest.mark.django_db
class TestProjectStationsOntoRoute:
    def test_synthetic_route_matches_expected_set_order_and_distance(self):
        # Route runs due north along lon -95.0 from lat 40.0 to 41.0.
        # Longitude degrees are compressed by cos(lat) near lat 40-41.
        cos_lat = math.cos(math.radians(40.5))

        def lon_offset_for_miles(miles):
            return (miles / MILES_PER_DEGREE_LAT) / cos_lat

        # The route spans lat 40.0 -> 41.0, i.e. ~69 miles total, so every
        # along-route offset below must stay within that range.

        # Station A: ~40 miles along the route, 1 mile east of it (inside).
        station_a = _make_station(
            1, 40.0 + 40 / MILES_PER_DEGREE_LAT, -95.0 + lon_offset_for_miles(1.0),
            name="A-Inside-Near-Origin",
        )
        # Station B: ~15 miles along the route, 1.9 miles west of it (inside,
        # near the boundary but still within the 2-mile buffer).
        station_b = _make_station(
            2, 40.0 + 15 / MILES_PER_DEGREE_LAT, -95.0 - lon_offset_for_miles(1.9),
            name="B-Inside-Boundary",
        )
        # Station C: ~60 miles along the route, right at the 2-mile
        # boundary (inside — filter is `<=`, and this is within the
        # ±1-mile projection tolerance of the boundary either way).
        station_c = _make_station(
            3, 40.0 + 60 / MILES_PER_DEGREE_LAT, -95.0 + lon_offset_for_miles(1.95),
            name="C-Near-Boundary",
        )
        # Station D: ~25 miles along the route, 5 miles east of it (outside).
        _make_station(
            4, 40.0 + 25 / MILES_PER_DEGREE_LAT, -95.0 + lon_offset_for_miles(5.0),
            name="D-Outside",
        )

        candidates = FuelStation.objects.filter(
            opis_id__in=[1, 2, 3, 4]
        )

        results = project_stations_onto_route(candidates, STRAIGHT_ROUTE_COORDINATES)

        assert [r["station"] for r in results] == [station_b, station_a, station_c]

        assert results[0]["distance_from_origin_miles"] == pytest.approx(15, abs=1.0)
        assert results[1]["distance_from_origin_miles"] == pytest.approx(40, abs=1.0)
        assert results[2]["distance_from_origin_miles"] == pytest.approx(60, abs=1.0)

    def test_empty_candidates_returns_empty_list(self):
        results = project_stations_onto_route(
            FuelStation.objects.none(), STRAIGHT_ROUTE_COORDINATES
        )
        assert results == []

    def test_result_is_within_buffer_threshold(self):
        # Just inside the 2-mile buffer edge (allowing headroom for the
        # projection approximation's own error margin).
        cos_lat = math.cos(math.radians(40.5))
        lon_offset = ((ROUTE_CORRIDOR_BUFFER_MILES - 0.05) / MILES_PER_DEGREE_LAT) / cos_lat
        station = _make_station(
            10, 40.5, -95.0 + lon_offset, name="AtBufferEdge"
        )

        results = project_stations_onto_route(
            FuelStation.objects.filter(opis_id=10), STRAIGHT_ROUTE_COORDINATES
        )

        assert len(results) == 1
        assert results[0]["station"] == station
