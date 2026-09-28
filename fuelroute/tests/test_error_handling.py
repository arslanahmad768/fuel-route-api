"""
Cross-check tests for Task 8: verify every documented exception type reaching
``RouteFuelPlanningService.plan`` is translated by the view into the exact
status code/body from the documented error table.

Each test mocks ``RouteFuelPlanningService.plan`` directly (rather than the
deeper routing/optimizer clients) so it exercises exactly the view's own
exception -> Response mapping, independent of how any given exception is
actually triggered deeper in the stack.
"""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from fuelroute.services.fuel_optimizer import InfeasiblePlanError
from fuelroute.services.routing_client import (
    LocationNotFound,
    RoutingRateLimited,
    RoutingUnavailable,
)

ROUTE_URL = "/api/v1/route/"

VALID_PAYLOAD = {
    "start": "Los Angeles, CA",
    "finish": "New York, NY",
}


@pytest.fixture
def client():
    return APIClient()


def _patched_plan(side_effect):
    return patch(
        "fuelroute.views.RouteFuelPlanningService.plan",
        side_effect=side_effect,
    )


@pytest.mark.django_db
def test_location_not_found_returns_400(client):
    with _patched_plan(LocationNotFound("Nowhereville")):
        response = client.post(ROUTE_URL, VALID_PAYLOAD, format="json")

    assert response.status_code == 400
    assert response.data == {
        "error": "Could not resolve location: Nowhereville"
    }


@pytest.mark.django_db
def test_routing_rate_limited_returns_503(client):
    with _patched_plan(RoutingRateLimited("rate limited")):
        response = client.post(ROUTE_URL, VALID_PAYLOAD, format="json")

    assert response.status_code == 503
    assert response.data == {"error": "Routing provider rate limit exceeded"}


@pytest.mark.django_db
def test_routing_unavailable_returns_502(client):
    with _patched_plan(RoutingUnavailable("down")):
        response = client.post(ROUTE_URL, VALID_PAYLOAD, format="json")

    assert response.status_code == 502
    assert response.data == {"error": "Routing provider is unavailable"}


@pytest.mark.django_db
def test_infeasible_plan_error_returns_422(client):
    exc = InfeasiblePlanError(gap_miles=550.0, near_mile=120.0)
    with _patched_plan(exc):
        response = client.post(ROUTE_URL, VALID_PAYLOAD, format="json")

    assert response.status_code == 422
    assert response.data == {"error": str(exc)}


@pytest.mark.django_db
def test_unexpected_exception_returns_500(client):
    with _patched_plan(RuntimeError("boom")):
        response = client.post(ROUTE_URL, VALID_PAYLOAD, format="json")

    assert response.status_code == 500
    assert response.data == {"error": "Internal server error"}
