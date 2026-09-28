"""
Unit tests for the route corridor matching service.
"""

import math
import random
import time
from decimal import Decimal

import pytest

from fuelroute.constants import EARTH_RADIUS_MILES, ROUTE_CORRIDOR_BUFFER_MILES
from fuelroute.models import FuelStation
from fuelroute.services.route_matching import (
    _nearest_point_on_segment,
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


def _exhaustive_reference_projection(stations, route_coordinates):
    """Brute-force all-segments scan: the pre-optimization algorithm, verbatim.

    Kept here as an oracle so the grid-accelerated implementation can be
    checked for *identical* output (same stations, same order, same float
    distances), not merely "close enough".
    """
    route_points = [(lat, lon) for lon, lat in route_coordinates]
    cumulative = [0.0]
    for i in range(1, len(route_points)):
        (a_lat, a_lon), (b_lat, b_lon) = route_points[i - 1], route_points[i]
        cumulative.append(cumulative[-1] + haversine_miles(a_lat, a_lon, b_lat, b_lon))

    results = []
    for station in stations:
        point = (float(station.latitude), float(station.longitude))
        best_distance = None
        best_offset = None
        for i in range(len(route_points) - 1):
            seg_start, seg_end = route_points[i], route_points[i + 1]
            nearest = _nearest_point_on_segment(point, seg_start, seg_end)
            distance = haversine_miles(point[0], point[1], nearest[0], nearest[1])
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_offset = cumulative[i] + haversine_miles(
                    seg_start[0], seg_start[1], nearest[0], nearest[1]
                )
        if best_distance is not None and best_distance <= ROUTE_CORRIDOR_BUFFER_MILES:
            results.append({"station": station, "distance_from_origin_miles": best_offset})
    results.sort(key=lambda r: r["distance_from_origin_miles"])
    return results


def _dense_route(waypoints, n_points, jitter_degrees, seed):
    """Build an ORS-style dense (lon, lat) polyline through `waypoints`."""
    rng = random.Random(seed)
    per_leg = n_points // (len(waypoints) - 1)
    coords = []
    for (a_lat, a_lon), (b_lat, b_lon) in zip(waypoints, waypoints[1:]):
        for k in range(per_leg):
            f = k / per_leg
            coords.append(
                (
                    a_lon + (b_lon - a_lon) * f + rng.uniform(-jitter_degrees, jitter_degrees),
                    a_lat + (b_lat - a_lat) * f + rng.uniform(-jitter_degrees, jitter_degrees),
                )
            )
    coords.append((waypoints[-1][1], waypoints[-1][0]))
    return coords


def _stations_near_route(route_coordinates, count, max_offset_miles, seed):
    """Unsaved FuelStation instances scattered around the route.

    Offsets are drawn up to `max_offset_miles` from random route vertices,
    deliberately straddling the 2-mile corridor boundary so that both the
    kept and rejected sides of the filter get exercised.
    """
    rng = random.Random(seed)
    stations = []
    for opis_id in range(count):
        lon, lat = rng.choice(route_coordinates)
        bearing = rng.uniform(0, 2 * math.pi)
        miles = rng.uniform(0, max_offset_miles)
        d_lat = miles * math.cos(bearing) / MILES_PER_DEGREE_LAT
        d_lon = miles * math.sin(bearing) / MILES_PER_DEGREE_LAT / math.cos(math.radians(lat))
        stations.append(
            FuelStation(
                opis_id=opis_id,
                name=f"S{opis_id}",
                retail_price=Decimal("3.500"),
                latitude=Decimal(f"{lat + d_lat:.6f}"),
                longitude=Decimal(f"{lon + d_lon:.6f}"),
            )
        )
    return stations


def _key(results):
    return [(r["station"].opis_id, r["distance_from_origin_miles"]) for r in results]


# Roughly Chicago -> St. Louis -> Memphis -> Little Rock -> Dallas -> Houston.
CHICAGO_TO_HOUSTON_WAYPOINTS = [
    (41.8781, -87.6298),
    (38.6270, -90.1994),
    (35.1495, -90.0490),
    (34.7465, -92.2896),
    (32.7767, -96.7970),
    (29.7604, -95.3698),
]


class TestProjectStationsOntoRouteAcceleration:
    """The spatial grid must be a pure speed-up, never a behavior change."""

    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_matches_exhaustive_scan_exactly_on_dense_route(self, seed):
        # Kept modest: the brute-force oracle is itself the slow algorithm.
        route = _dense_route(CHICAGO_TO_HOUSTON_WAYPOINTS, 800, 0.01, seed=seed)
        stations = _stations_near_route(route, 300, max_offset_miles=4.0, seed=seed)

        got = project_stations_onto_route(stations, route)
        want = _exhaustive_reference_projection(stations, route)

        assert want  # sanity: the scenario actually keeps some stations
        assert len(want) < len(stations)  # ...and rejects some
        assert _key(got) == _key(want)

    def test_matches_exhaustive_scan_exactly_on_sparse_long_segments(self):
        # Waypoints only: segments hundreds of miles long, spanning many grid
        # cells each, exercising the long-segment splitting path.
        route = [(lon, lat) for lat, lon in CHICAGO_TO_HOUSTON_WAYPOINTS]
        sampling_points = _dense_route(CHICAGO_TO_HOUSTON_WAYPOINTS, 3000, 0.0, seed=0)
        stations = _stations_near_route(sampling_points, 400, max_offset_miles=4.0, seed=7)

        got = project_stations_onto_route(stations, route)
        want = _exhaustive_reference_projection(stations, route)

        assert want
        assert len(want) < len(stations)
        assert _key(got) == _key(want)

    def test_dense_polyline_with_many_candidates_is_fast(self):
        # Regression guard: real ORS polylines have thousands of points, and
        # the old all-segments scan took tens of seconds to minutes here.
        route = _dense_route(CHICAGO_TO_HOUSTON_WAYPOINTS, 10_000, 0.01, seed=42)
        stations = _stations_near_route(route, 1_600, max_offset_miles=30.0, seed=42)

        started = time.perf_counter()
        results = project_stations_onto_route(stations, route)
        elapsed = time.perf_counter() - started

        assert results  # did real work, not a trivially empty run
        assert elapsed < 2.0, f"corridor matching took {elapsed:.2f}s"
