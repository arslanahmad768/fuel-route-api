"""
Tests for the import_fuel_prices management command.
"""

from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from fuelroute.models import FuelStation

FUEL_PRICES_CSV = """OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price
100,PILOT TRAVEL CENTER,"I-40, EXIT 1 & SR-1",Springfield,MO,101,3.100000
200,LOVES TRAVEL STOP,"I-90, EXIT 200 & CA-12",Reno,NV,201,3.500000
200,LOVE'S #200,"I-90, EXIT 200 & CA-12",Reno,NV,201,3.200000
300,FLYING J,"I-10, EXIT 5 & TX-1",Unknown City,TX,301,2.999000
400,PETRO CANADA,"HWY 401, EXIT 50",Toronto,ON,401,1.200000
500,DUP STATION,"I-70, EXIT 10 & CO-5",Denver,CO,501,3.750000
500,DUP STATION,"I-70, EXIT 10 & CO-5",Denver,CO,501,3.750000
"""

GEOCODE_CACHE_CSV = """city,state,latitude,longitude,confidence
SPRINGFIELD,MO,37.2089572,-93.2922989,city
RENO,NV,39.5296329,-119.8138027,city
DENVER,CO,39.7392358,-104.990251,city
"""


@pytest.fixture
def fuel_prices_csv_path(tmp_path):
    path = tmp_path / "fuel-prices.csv"
    path.write_text(FUEL_PRICES_CSV, encoding="utf-8")
    return str(path)


@pytest.fixture
def geocode_cache_csv_path(tmp_path):
    path = tmp_path / "geocode-cache.csv"
    path.write_text(GEOCODE_CACHE_CSV, encoding="utf-8")
    return str(path)


@pytest.mark.django_db
class TestImportFuelPricesCommand:
    def _run(self, csv_path, geocode_cache_path):
        out = StringIO()
        call_command(
            "import_fuel_prices",
            f"--csv-path={csv_path}",
            f"--geocode-cache={geocode_cache_path}",
            stdout=out,
        )
        return out.getvalue()

    def test_import_cleans_and_loads_data(
        self, fuel_prices_csv_path, geocode_cache_csv_path
    ):
        self._run(fuel_prices_csv_path, geocode_cache_csv_path)

        # Final row count: opis 100, 200, 300, 500 survive (400 is Canadian,
        # dropped). 500's exact duplicate pair collapses to one row.
        assert FuelStation.objects.count() == 4

        # Canadian row dropped entirely.
        assert not FuelStation.objects.filter(opis_id=400).exists()

        # OPIS-id group with differing prices AND differing names: min
        # price wins (from the second, cheaper row), but the name must
        # come from the *first* row in file order ("LOVES TRAVEL STOP"),
        # not the second ("LOVE'S #200") or the cheaper row's name — this
        # would fail if the collapsing logic picked the wrong row's name.
        station_200 = FuelStation.objects.get(opis_id=200)
        assert station_200.retail_price == Decimal("3.200000")
        assert station_200.name == "LOVES TRAVEL STOP"

        # Exact-duplicate pair collapses to a single row (not raising, not
        # duplicating), with the original price preserved.
        station_500 = FuelStation.objects.get(opis_id=500)
        assert station_500.retail_price == Decimal("3.750000")

        # Row whose city matches the fixture geocode cache gets populated
        # geocode fields.
        station_100 = FuelStation.objects.get(opis_id=100)
        assert station_100.latitude == Decimal("37.208957")
        assert station_100.longitude == Decimal("-93.292299")
        assert station_100.geocode_confidence == "city"

        # Row whose city does not match the fixture geocode cache leaves
        # geocode fields None.
        station_300 = FuelStation.objects.get(opis_id=300)
        assert station_300.latitude is None
        assert station_300.longitude is None
        assert station_300.geocode_confidence is None

    def test_import_prints_summary(
        self, fuel_prices_csv_path, geocode_cache_csv_path
    ):
        output = self._run(fuel_prices_csv_path, geocode_cache_csv_path)

        assert "rows read: 7" in output
        assert "exact duplicates dropped: 1" in output
        assert "non-US rows dropped: 1" in output
        assert "unique stations upserted: 4" in output
        # 100, 200, 500 match the cache; 300 (Unknown City) does not.
        assert "geocode misses: 1" in output

    def test_import_is_idempotent(
        self, fuel_prices_csv_path, geocode_cache_csv_path
    ):
        self._run(fuel_prices_csv_path, geocode_cache_csv_path)
        first_state = list(
            FuelStation.objects.order_by("opis_id").values(
                "opis_id",
                "name",
                "address",
                "city",
                "state",
                "rack_id",
                "retail_price",
                "latitude",
                "longitude",
                "geocode_confidence",
            )
        )

        self._run(fuel_prices_csv_path, geocode_cache_csv_path)
        second_state = list(
            FuelStation.objects.order_by("opis_id").values(
                "opis_id",
                "name",
                "address",
                "city",
                "state",
                "rack_id",
                "retail_price",
                "latitude",
                "longitude",
                "geocode_confidence",
            )
        )

        assert FuelStation.objects.count() == 4
        assert first_state == second_state
