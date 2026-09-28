"""
End-to-end integration tests for ``POST /api/v1/route/``.

Unlike ``test_error_handling.py`` (Task 8), which mocks
``RouteFuelPlanningService.plan`` directly to exercise only the view's
exception -> status mapping, these tests mock at the HTTP boundary
(``routing_client.geocode`` / ``routing_client.get_route``) and let the real
route-matching and fuel-optimizer logic run against seeded ``FuelStation``
rows on a synthetic straight-line route. This proves the full
request/response cycle, not just the view's error handling.

The synthetic route runs along a single meridian (constant longitude), so
the great-circle distance between any two points on it reduces exactly to
``EARTH_RADIUS_MILES * radians(delta_latitude)`` -- no floating-point drift
from the corridor-matching haversine math. ``lat_at_mile`` inverts that
relationship to place the route's endpoints and seeded stations at exact
mile markers along the line.
"""

from __future__ import annotations

import math
from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from fuelroute.constants import EARTH_RADIUS_MILES
from fuelroute.models import FuelStation
from fuelroute.services.routing_client import (
    RouteResult,
    RoutingRateLimited,
    RoutingUnavailable,
)

ROUTE_URL = "/api/v1/route/"

# Degrees of latitude per mile along a meridian -- exact for a great circle
# with zero longitude delta (haversine's `c` reduces to `delta_phi`).
_DEG_PER_MILE = 180 / (math.pi * EARTH_RADIUS_MILES)


def lat_at_mile(start_lat: float, miles: float) -> float:
    """Latitude `miles` north of `start_lat` along the same meridian."""
    return start_lat + miles * _DEG_PER_MILE


def make_route_result(
    start_lat: float, lon: float, total_miles: float, duration_hours: float = 9.6
) -> RouteResult:
    finish_lat = lat_at_mile(start_lat, total_miles)
    return RouteResult(
        distance_miles=total_miles,
        duration_hours=duration_hours,
        geometry={
            "type": "LineString",
            "coordinates": [[lon, start_lat], [lon, finish_lat]],
        },
    )


@pytest.fixture
def client():
    return APIClient()


RESPONSE_TOP_LEVEL_FIELDS = {
    "start",
    "finish",
    "distance_miles",
    "duration_hours",
    "route_geometry",
    "fuel_stops",
    "total_fuel_cost",
    "total_gallons",
}

FUEL_STOP_FIELDS = {
    "station_name",
    "city",
    "state",
    "distance_from_origin_miles",
    "price_per_gallon",
    "gallons_purchased",
    "cost",
}


@pytest.mark.django_db
class TestRouteEndpointHappyPath:
    """One station at mile 400 of a 600mi route -- exactly one fuel stop."""

    START_LAT = 30.0
    LON = -100.0
    TOTAL_MILES = 600.0
    STATION_MILE = 400.0
    STATION_PRICE = Decimal("3.500")

    def _seed_station(self):
        return FuelStation.objects.create(
            opis_id=1,
            name="Test Travel Center",
            address="I-00, EXIT 1",
            city="Testville",
            state="TX",
            rack_id="1",
            retail_price=self.STATION_PRICE,
            latitude=Decimal(str(lat_at_mile(self.START_LAT, self.STATION_MILE))),
            longitude=Decimal(str(self.LON)),
            geocode_confidence="city",
        )

    def _route_result(self):
        return make_route_result(self.START_LAT, self.LON, self.TOTAL_MILES)

    def test_free_text_request_returns_full_response_shape(self, client):
        self._seed_station()
        finish_lat = lat_at_mile(self.START_LAT, self.TOTAL_MILES)

        def fake_geocode(query):
            if query == "Los Angeles, CA":
                return (self.START_LAT, self.LON)
            if query == "New York, NY":
                return (finish_lat, self.LON)
            raise AssertionError(f"unexpected geocode query: {query!r}")

        with patch(
            "fuelroute.services.route_planning.routing_client.geocode",
            side_effect=fake_geocode,
        ) as mock_geocode, patch(
            "fuelroute.services.route_planning.routing_client.get_route",
            return_value=self._route_result(),
        ):
            response = client.post(
                ROUTE_URL,
                {"start": "Los Angeles, CA", "finish": "New York, NY"},
                format="json",
            )

        assert response.status_code == 200
        assert mock_geocode.call_count == 2

        data = response.data
        assert RESPONSE_TOP_LEVEL_FIELDS.issubset(data.keys())
        assert data["start"] == "Los Angeles, CA"
        assert data["finish"] == "New York, NY"
        assert data["distance_miles"] == pytest.approx(600.0)
        assert data["duration_hours"] == pytest.approx(9.6)
        assert data["route_geometry"]["type"] == "LineString"

        assert len(data["fuel_stops"]) == 1
        stop = data["fuel_stops"][0]
        assert FUEL_STOP_FIELDS.issubset(stop.keys())
        assert stop["station_name"] == "Test Travel Center"
        assert stop["city"] == "Testville"
        assert stop["state"] == "TX"
        assert stop["distance_from_origin_miles"] == pytest.approx(400.0, abs=0.1)
        assert stop["price_per_gallon"] == pytest.approx(3.5)
        assert stop["gallons_purchased"] == pytest.approx(10.0)
        assert stop["cost"] == pytest.approx(35.0)

        assert data["total_fuel_cost"] == pytest.approx(35.0)
        assert data["total_gallons"] == pytest.approx(10.0)

    def test_coordinates_only_request_skips_geocode(self, client):
        self._seed_station()
        finish_lat = lat_at_mile(self.START_LAT, self.TOTAL_MILES)

        payload = {
            "start_lat": self.START_LAT,
            "start_lng": self.LON,
            "finish_lat": finish_lat,
            "finish_lng": self.LON,
        }

        with patch(
            "fuelroute.services.route_planning.routing_client.geocode"
        ) as mock_geocode, patch(
            "fuelroute.services.route_planning.routing_client.get_route",
            return_value=self._route_result(),
        ):
            response = client.post(ROUTE_URL, payload, format="json")

        assert response.status_code == 200
        mock_geocode.assert_not_called()

        data = response.data
        assert len(data["fuel_stops"]) == 1
        assert data["fuel_stops"][0]["cost"] == pytest.approx(35.0)


@pytest.mark.django_db
def test_invalid_request_missing_both_input_shapes_returns_400(client):
    response = client.post(ROUTE_URL, {}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_routing_unavailable_from_get_route_returns_502(client):
    payload = {
        "start_lat": 30.0,
        "start_lng": -100.0,
        "finish_lat": 35.0,
        "finish_lng": -100.0,
    }
    with patch(
        "fuelroute.services.route_planning.routing_client.get_route",
        side_effect=RoutingUnavailable("ORS is down"),
    ):
        response = client.post(ROUTE_URL, payload, format="json")

    assert response.status_code == 502
    assert response.data == {"error": "Routing provider is unavailable"}


@pytest.mark.django_db
def test_routing_rate_limited_from_get_route_returns_503(client):
    payload = {
        "start_lat": 30.0,
        "start_lng": -100.0,
        "finish_lat": 35.0,
        "finish_lng": -100.0,
    }
    with patch(
        "fuelroute.services.route_planning.routing_client.get_route",
        side_effect=RoutingRateLimited("rate limited"),
    ):
        response = client.post(ROUTE_URL, payload, format="json")

    assert response.status_code == 503
    assert response.data == {"error": "Routing provider rate limit exceeded"}


@pytest.mark.django_db
def test_gap_over_500_miles_returns_422_with_gap_message(client):
    """One station at mile 450 of a 1000mi route: the remaining 550mi to the
    destination exceeds the vehicle's 500mi range, with no further station
    to break up the gap -- InfeasiblePlanError."""
    start_lat = 25.0
    lon = -105.0
    total_miles = 1000.0
    station_mile = 450.0

    FuelStation.objects.create(
        opis_id=2,
        name="Lone Station",
        address="I-00, EXIT 2",
        city="Loneville",
        state="TX",
        rack_id="2",
        retail_price=Decimal("3.000"),
        latitude=Decimal(str(lat_at_mile(start_lat, station_mile))),
        longitude=Decimal(str(lon)),
        geocode_confidence="city",
    )

    payload = {
        "start_lat": start_lat,
        "start_lng": lon,
        "finish_lat": lat_at_mile(start_lat, total_miles),
        "finish_lng": lon,
    }

    with patch(
        "fuelroute.services.route_planning.routing_client.get_route",
        return_value=make_route_result(start_lat, lon, total_miles),
    ):
        response = client.post(ROUTE_URL, payload, format="json")

    assert response.status_code == 422
    assert "error" in response.data
    message = response.data["error"]
    assert "gap of 550.0 miles" in message
    assert "near mile 450.0" in message
