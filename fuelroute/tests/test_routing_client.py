"""
Unit tests for the ORS routing client.

All ORS HTTP calls are mocked with the `responses` library. Because these
tests use plain `responses.activate` (no passthrough), any unregistered
real network call would raise a ConnectionError from `responses` itself —
so a passing test suite is proof that no real HTTP request ever left the
process.
"""

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
