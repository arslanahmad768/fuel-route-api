"""
Smoke tests to verify Django loads correctly.
"""

import pytest
from django.apps import apps


def test_django_loads():
    """Test that Django initializes without errors.

    Checks that all configured apps are ready without requiring
    SECRET_KEY (avoids cookie-signing middleware paths).
    """
    # Verify all installed apps are loaded
    assert apps.ready, "Django apps should be ready"
    # Verify our app is in the registry
    assert apps.is_installed('fuelroute'), "fuelroute app should be installed"


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
