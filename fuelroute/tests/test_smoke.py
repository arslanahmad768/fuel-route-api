"""
Smoke tests to verify Django loads correctly.
"""

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_django_loads():
    """Test that Django initializes without errors."""
    client = Client()
    # This ensures the Django app initializes successfully
    # The root path will 404, but it shouldn't crash
    response = client.get('/')
    # We just want to ensure it doesn't crash during startup
    assert response.status_code in (404, 405)  # 404 Not Found or 405 Method Not Allowed


def test_constants_imported():
    """Test that fuelroute constants can be imported."""
    from fuelroute.constants import (
        VEHICLE_RANGE_MILES,
        VEHICLE_MPG,
        EARTH_RADIUS_MILES,
        ROUTE_CORRIDOR_BUFFER_MILES,
    )

    assert VEHICLE_RANGE_MILES == 500
    assert VEHICLE_MPG == 10
    assert EARTH_RADIUS_MILES == 3958.8
    assert ROUTE_CORRIDOR_BUFFER_MILES == 2.0
