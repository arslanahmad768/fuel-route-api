"""
Tests for how RouteFuelPlanningService resolves start/finish locations.

ORS is replaced with in-process fakes; the route is kept under the vehicle's
range so the plan needs no stations and these tests isolate endpoint
resolution alone.
"""

import threading
from unittest.mock import patch

import pytest

from fuelroute.services.route_planning import RouteFuelPlanningService
from fuelroute.services.routing_client import LocationNotFound, RouteResult

SHORT_ROUTE = RouteResult(
    distance_miles=100.0,
    duration_hours=2.0,
    geometry={"type": "LineString", "coordinates": [[-87.63, 41.88], [-87.9, 41.0]]},
)
COORDINATES = {"Chicago, IL": (41.88, -87.63), "Joliet, IL": (41.0, -87.9)}


def _plan(request_data, geocode):
    with patch(
        "fuelroute.services.route_planning.routing_client.geocode", side_effect=geocode
    ) as mock_geocode, patch(
        "fuelroute.services.route_planning.routing_client.get_route",
        return_value=SHORT_ROUTE,
    ) as mock_get_route:
        result = RouteFuelPlanningService.plan(request_data)
    return result, mock_geocode, mock_get_route


@pytest.mark.django_db
def test_start_and_finish_are_geocoded_concurrently():
    # Both calls must be in flight at once for the barrier to release; a
    # sequential implementation would block on the first call and time out.
    barrier = threading.Barrier(2, timeout=5)

    def geocode(query):
        barrier.wait()
        return COORDINATES[query]

    result, _, mock_get_route = _plan({"start": "Chicago, IL", "finish": "Joliet, IL"}, geocode)

    mock_get_route.assert_called_once_with((41.88, -87.63), (41.0, -87.9))
    assert result["start"] == "Chicago, IL"
    assert result["finish"] == "Joliet, IL"


@pytest.mark.django_db
def test_coordinates_skip_geocoding_entirely():
    request_data = {
        "start_lat": 41.88, "start_lng": -87.63, "finish_lat": 41.0, "finish_lng": -87.9,
    }

    _, mock_geocode, mock_get_route = _plan(request_data, COORDINATES.get)

    mock_geocode.assert_not_called()
    mock_get_route.assert_called_once_with((41.88, -87.63), (41.0, -87.9))


@pytest.mark.django_db
def test_mixed_input_geocodes_only_the_text_endpoint():
    request_data = {"start_lat": 41.88, "start_lng": -87.63, "finish": "Joliet, IL"}

    result, mock_geocode, _ = _plan(request_data, COORDINATES.get)

    mock_geocode.assert_called_once_with("Joliet, IL")
    assert result["start"] == "41.8800,-87.6300"


@pytest.mark.django_db
def test_unresolvable_finish_reports_the_finish_location():
    def geocode(query):
        if query == "Nowhere, ZZ":
            raise LocationNotFound("no results")
        return COORDINATES[query]

    with pytest.raises(LocationNotFound) as exc_info:
        _plan({"start": "Chicago, IL", "finish": "Nowhere, ZZ"}, geocode)

    assert str(exc_info.value) == "Nowhere, ZZ"


@pytest.mark.django_db
def test_when_both_fail_the_start_location_is_reported():
    def geocode(query):
        raise LocationNotFound("no results")

    with pytest.raises(LocationNotFound) as exc_info:
        _plan({"start": "Nowhere, ZZ", "finish": "Elsewhere, ZZ"}, geocode)

    assert str(exc_info.value) == "Nowhere, ZZ"
