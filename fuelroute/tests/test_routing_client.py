"""
Unit tests for the ORS routing client.

All ORS HTTP calls are mocked with the `responses` library. Because these
tests use plain `responses.activate` (no passthrough), any unregistered
real network call would raise a ConnectionError from `responses` itself —
so a passing test suite is proof that no real HTTP request ever left the
process.
"""

import pytest
import requests
import responses

from fuelroute.services.routing_client import (
    LocationNotFound,
    RoutingRateLimited,
    RoutingUnavailable,
    geocode,
    get_route,
)

GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"


@responses.activate
def test_geocode_success():
    responses.add(
        responses.GET,
        GEOCODE_URL,
        json={
            "features": [
                {
                    "geometry": {"coordinates": [-87.6298, 41.8781]},
                    "properties": {"label": "Chicago, IL, USA"},
                }
            ]
        },
        status=200,
    )

    lat, lon = geocode("Chicago, IL")

    assert lat == 41.8781
    assert lon == -87.6298


@responses.activate
def test_geocode_empty_results_raises_location_not_found():
    responses.add(
        responses.GET,
        GEOCODE_URL,
        json={"features": []},
        status=200,
    )

    try:
        geocode("Nowhereville, ZZ")
        assert False, "expected LocationNotFound"
    except LocationNotFound:
        pass


@responses.activate
def test_get_route_success_converts_units():
    # Known fixture: 160934.4 meters == 100 miles, 7200 seconds == 2 hours.
    responses.add(
        responses.POST,
        DIRECTIONS_URL,
        json={
            "features": [
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-87.6298, 41.8781], [-96.7970, 32.7767]],
                    },
                    "properties": {
                        "summary": {
                            "distance": 160934.4,
                            "duration": 7200,
                        }
                    },
                }
            ]
        },
        status=200,
    )

    result = get_route((41.8781, -87.6298), (32.7767, -96.7970))

    assert result.distance_miles == 100.0
    assert result.duration_hours == 2.0
    assert result.geometry == {
        "type": "LineString",
        "coordinates": [[-87.6298, 41.8781], [-96.7970, 32.7767]],
    }


@responses.activate
def test_geocode_rate_limited_raises():
    responses.add(
        responses.GET,
        GEOCODE_URL,
        json={"error": "rate limit exceeded"},
        status=429,
    )

    try:
        geocode("Chicago, IL")
        assert False, "expected RoutingRateLimited"
    except RoutingRateLimited:
        pass


@responses.activate
def test_get_route_rate_limited_raises():
    responses.add(
        responses.POST,
        DIRECTIONS_URL,
        json={"error": "rate limit exceeded"},
        status=429,
    )

    try:
        get_route((41.8781, -87.6298), (32.7767, -96.7970))
        assert False, "expected RoutingRateLimited"
    except RoutingRateLimited:
        pass


@responses.activate
def test_geocode_connection_timeout_raises_unavailable():
    responses.add(
        responses.GET,
        GEOCODE_URL,
        body=requests.exceptions.ConnectTimeout("connection timed out"),
    )

    try:
        geocode("Chicago, IL")
        assert False, "expected RoutingUnavailable"
    except RoutingUnavailable:
        pass


@responses.activate
def test_get_route_connection_timeout_raises_unavailable():
    responses.add(
        responses.POST,
        DIRECTIONS_URL,
        body=requests.exceptions.ConnectTimeout("connection timed out"),
    )

    try:
        get_route((41.8781, -87.6298), (32.7767, -96.7970))
        assert False, "expected RoutingUnavailable"
    except RoutingUnavailable:
        pass


@responses.activate
def test_geocode_server_error_raises_unavailable():
    responses.add(
        responses.GET,
        GEOCODE_URL,
        json={"error": "internal error"},
        status=503,
    )

    try:
        geocode("Chicago, IL")
        assert False, "expected RoutingUnavailable"
    except RoutingUnavailable:
        pass


# --- Malformed-but-200 responses must map to documented exceptions, never
# --- escape as KeyError/IndexError/TypeError (which the view turns into 500).


@pytest.mark.parametrize(
    "body",
    [
        {},  # no "features" key at all
        {"features": None},
        {"features": [{}]},  # feature without geometry
        {"features": [{"geometry": {}}]},  # geometry without coordinates
        {"features": [{"geometry": {"coordinates": [-87.6]}}]},  # too short
        {"features": [{"geometry": {"coordinates": ["x", "y"]}}]},  # not numbers
        [],  # JSON, but not an object
    ],
    ids=[
        "missing-features",
        "null-features",
        "no-geometry",
        "no-coordinates",
        "short-coordinates",
        "non-numeric-coordinates",
        "non-object-body",
    ],
)
@responses.activate
def test_geocode_malformed_200_raises_unavailable(body):
    responses.add(responses.GET, GEOCODE_URL, json=body, status=200)

    with pytest.raises(RoutingUnavailable):
        geocode("Chicago, IL")


@responses.activate
def test_geocode_non_json_200_raises_unavailable():
    responses.add(
        responses.GET, GEOCODE_URL, body="<html>oops</html>", status=200,
        content_type="text/html",
    )

    with pytest.raises(RoutingUnavailable):
        geocode("Chicago, IL")


_GOOD_FEATURE = {
    "geometry": {
        "type": "LineString",
        "coordinates": [[-87.6298, 41.8781], [-96.7970, 32.7767]],
    },
    "properties": {"summary": {"distance": 160934.4, "duration": 7200}},
}


@pytest.mark.parametrize(
    "body",
    [
        {},  # no "features" key
        {"features": []},  # empty route list
        {"features": None},
        {"features": [{"geometry": _GOOD_FEATURE["geometry"]}]},  # no properties
        {"features": [{"geometry": _GOOD_FEATURE["geometry"], "properties": {}}]},
        {"features": [{"properties": _GOOD_FEATURE["properties"]}]},  # no geometry
        {"features": [{"geometry": {"type": "LineString"}, "properties": _GOOD_FEATURE["properties"]}]},
        {"features": [{"geometry": {"type": "LineString", "coordinates": [[-87.6, 41.8]]}, "properties": _GOOD_FEATURE["properties"]}]},
        {"features": [{"geometry": _GOOD_FEATURE["geometry"], "properties": {"summary": {"distance": "far"}}}]},
        [],  # JSON, but not an object
    ],
    ids=[
        "missing-features",
        "empty-features",
        "null-features",
        "no-properties",
        "no-summary",
        "no-geometry",
        "no-coordinates",
        "single-coordinate",
        "non-numeric-distance",
        "non-object-body",
    ],
)
@responses.activate
def test_get_route_malformed_200_raises_unavailable(body):
    responses.add(responses.POST, DIRECTIONS_URL, json=body, status=200)

    with pytest.raises(RoutingUnavailable):
        get_route((41.8781, -87.6298), (32.7767, -96.7970))


@responses.activate
def test_get_route_non_json_200_raises_unavailable():
    responses.add(
        responses.POST, DIRECTIONS_URL, body="not json", status=200,
        content_type="text/plain",
    )

    with pytest.raises(RoutingUnavailable):
        get_route((41.8781, -87.6298), (32.7767, -96.7970))


@responses.activate
def test_get_route_summary_without_distance_means_zero():
    # ORS omits zero-valued summary fields (e.g. start == finish).
    feature = {
        "geometry": {
            "type": "LineString",
            "coordinates": [[-87.6298, 41.8781], [-87.6298, 41.8781]],
        },
        "properties": {"summary": {}},
    }
    responses.add(responses.POST, DIRECTIONS_URL, json={"features": [feature]}, status=200)

    result = get_route((41.8781, -87.6298), (41.8781, -87.6298))

    assert result.distance_miles == 0.0
    assert result.duration_hours == 0.0
