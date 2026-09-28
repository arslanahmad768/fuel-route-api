"""
Unit tests for FuelStation model.
"""

import pytest
from decimal import Decimal
from django.db import IntegrityError
from fuelroute.models import FuelStation


@pytest.mark.django_db
class TestFuelStationModel:
    """Test suite for FuelStation model."""

    def test_create_fuel_station(self):
        """Test creating a FuelStation instance."""
        station = FuelStation.objects.create(
            opis_id=12345,
            name="Shell Station",
            address="123 Main St",
            city="New York",
            state="NY",
            rack_id="RACK001",
            retail_price=Decimal("3.450"),
            latitude=Decimal("40.712776"),
            longitude=Decimal("-74.005974"),
            geocode_confidence="high",
        )
        assert station.opis_id == 12345
        assert station.name == "Shell Station"
        assert station.city == "New York"
        assert station.state == "NY"

    def test_fuel_station_str(self):
        """Test __str__ method returns expected format."""
        station = FuelStation.objects.create(
            opis_id=12345,
            name="Shell Station",
            address="123 Main St",
            city="New York",
            state="NY",
            retail_price=Decimal("3.450"),
        )
        assert str(station) == "Shell Station (New York, NY)"

    def test_opis_id_uniqueness_enforced(self):
        """Test that opis_id uniqueness constraint is enforced."""
        # Create first station
        FuelStation.objects.create(
            opis_id=12345,
            name="Shell Station",
            address="123 Main St",
            city="New York",
            state="NY",
            retail_price=Decimal("3.450"),
        )

        # Attempt to create second station with same opis_id
        with pytest.raises(IntegrityError):
            FuelStation.objects.create(
                opis_id=12345,
                name="Exxon Station",
                address="456 Oak Ave",
                city="Los Angeles",
                state="CA",
                retail_price=Decimal("3.550"),
            )
