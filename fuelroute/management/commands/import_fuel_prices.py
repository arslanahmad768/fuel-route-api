"""
Management command: import_fuel_prices

Cleans and loads the raw fuel-price dataset into the FuelStation model,
joined against a precomputed city-level geocode cache.

Usage:
    uv run python manage.py import_fuel_prices \
        [--csv-path data/fuel-prices-for-be-assessment.csv] \
        [--geocode-cache data/city_geocode_cache.csv]
"""

import argparse
import csv
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuelroute.models import FuelStation
from fuelroute.us_states import US_STATES

DEFAULT_CSV_PATH = "data/fuel-prices-for-be-assessment.csv"
DEFAULT_GEOCODE_CACHE_PATH = "data/city_geocode_cache.csv"


class Command(BaseCommand):
    help = (
        "Import and clean the fuel-price dataset into FuelStation, joined "
        "against a precomputed city-level geocode cache. Idempotent: "
        "re-running upserts existing rows by opis_id rather than "
        "duplicating them."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--csv-path",
            default=DEFAULT_CSV_PATH,
            help=f"Path to the raw fuel-price CSV (default: {DEFAULT_CSV_PATH})",
        )
        parser.add_argument(
            "--geocode-cache",
            default=DEFAULT_GEOCODE_CACHE_PATH,
            help=(
                "Path to the city-level geocode cache CSV "
                f"(default: {DEFAULT_GEOCODE_CACHE_PATH})"
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        csv_path = options["csv_path"]
        geocode_cache_path = options["geocode_cache"]

        geocode_cache = self._load_geocode_cache(geocode_cache_path)

        try:
            source_file = open(csv_path, newline="", encoding="utf-8")
        except OSError as exc:
            raise CommandError(f"Could not open CSV file '{csv_path}': {exc}")

        rows_read = 0
        exact_duplicates_dropped = 0
        non_us_dropped = 0
        seen_rows = set()
        groups: dict[int, list[dict]] = {}

        with source_file:
            reader = csv.DictReader(source_file)
            for row in reader:
                rows_read += 1

                row_tuple = (
                    row["OPIS Truckstop ID"],
                    row["Truckstop Name"],
                    row["Address"],
                    row["City"],
                    row["State"],
                    row["Rack ID"],
                    row["Retail Price"],
                )
                if row_tuple in seen_rows:
                    exact_duplicates_dropped += 1
                    continue
                seen_rows.add(row_tuple)

                state = row["State"].strip().upper()
                if state not in US_STATES:
                    non_us_dropped += 1
                    continue

                opis_id = int(row["OPIS Truckstop ID"])
                groups.setdefault(opis_id, []).append(row)

        geocode_misses = 0
        unique_stations_upserted = 0

        with transaction.atomic():
            for opis_id, group_rows in groups.items():
                first_row = group_rows[0]
                name = first_row["Truckstop Name"]
                address = first_row["Address"]
                city = first_row["City"]
                state = first_row["State"].strip().upper()
                rack_id = first_row["Rack ID"]

                try:
                    retail_price = min(
                        Decimal(r["Retail Price"]) for r in group_rows
                    )
                except InvalidOperation as exc:
                    raise CommandError(
                        f"Invalid retail price for OPIS ID {opis_id}: {exc}"
                    )

                cache_key = (city.strip().upper(), state)
                geocode_entry = geocode_cache.get(cache_key)

                if geocode_entry is not None:
                    latitude, longitude, confidence = geocode_entry
                else:
                    latitude, longitude, confidence = None, None, None
                    geocode_misses += 1

                FuelStation.objects.update_or_create(
                    opis_id=opis_id,
                    defaults={
                        "name": name,
                        "address": address,
                        "city": city,
                        "state": state,
                        "rack_id": rack_id,
                        "retail_price": retail_price,
                        "latitude": latitude,
                        "longitude": longitude,
                        "geocode_confidence": confidence,
                    },
                )
                unique_stations_upserted += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Import complete:\n"
                f"  rows read: {rows_read}\n"
                f"  exact duplicates dropped: {exact_duplicates_dropped}\n"
                f"  non-US rows dropped: {non_us_dropped}\n"
                f"  unique stations upserted: {unique_stations_upserted}\n"
                f"  geocode misses: {geocode_misses}"
            )
        )

    @staticmethod
    def _load_geocode_cache(geocode_cache_path: str) -> dict:
        """
        Load the geocode cache CSV into a dict keyed by
        (city.strip().upper(), state.strip().upper()) ->
        (latitude, longitude, confidence).
        """
        cache: dict[tuple[str, str], tuple[Decimal, Decimal, str]] = {}
        try:
            cache_file = open(geocode_cache_path, newline="", encoding="utf-8")
        except OSError as exc:
            raise CommandError(
                f"Could not open geocode cache file '{geocode_cache_path}': {exc}"
            )

        with cache_file:
            reader = csv.DictReader(cache_file)
            for row in reader:
                key = (row["city"].strip().upper(), row["state"].strip().upper())
                cache[key] = (
                    Decimal(row["latitude"]),
                    Decimal(row["longitude"]),
                    row["confidence"],
                )
        return cache
