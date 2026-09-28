from django.db import models


class FuelStation(models.Model):
    """Model representing a fuel station with location and pricing information."""

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    rack_id = models.CharField(max_length=100, blank=True)
    retail_price = models.DecimalField(max_digits=6, decimal_places=3)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    geocode_confidence = models.CharField(
        max_length=20, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["latitude", "longitude"]),
            models.Index(fields=["state"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state})"
