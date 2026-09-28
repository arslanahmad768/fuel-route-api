"""
Fuel-stop optimization: minimum-cost refueling along a fixed route with a
bounded tank.

Decoupled from the ORM: candidates are plain dicts of the shape produced by
``route_matching.project_stations_onto_route``::

    {"station": <obj with .retail_price (Decimal) and .opis_id>,
     "distance_from_origin_miles": float}

Greedy exchange-argument strategy, tracking fuel on board:

* If a strictly cheaper station is reachable on a full tank, buy only the
  shortfall needed to reach the first such station (buying more here would
  mean paying more for fuel that could be bought cheaper later).
* Otherwise, if the destination is reachable, buy only the shortfall to get
  there.
* Otherwise fill the tank completely (nothing within range is cheaper, so
  every mile bought here is at least as cheap as any alternative) and move to
  the cheapest station in range.
"""

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fuelroute.constants import VEHICLE_MPG, VEHICLE_RANGE_MILES

CENTS = Decimal("0.01")


@dataclass
class FuelStop:
    station: Any
    distance_from_origin_miles: float
    gallons_purchased: Decimal
    cost: Decimal


class InfeasiblePlanError(Exception):
    """No station (or the destination) is reachable on a full tank."""

    def __init__(self, gap_miles: float, near_mile: float):
        self.gap_miles = gap_miles
        self.near_mile = near_mile
        super().__init__(
            f"No feasible fuel stop plan: gap of {gap_miles:.1f} miles "
            f"near mile {near_mile:.1f} exceeds vehicle range"
        )


def _purchase(station: Any, distance: float, miles: float) -> FuelStop | None:
    """Build a FuelStop for buying `miles` of range at `station`, or None if 0."""
    if miles <= 0:
        return None
    gallons = Decimal(str(miles / VEHICLE_MPG)).quantize(CENTS, rounding=ROUND_HALF_UP)
    # Intentional: float leftovers under 0.05 mi round to 0.00 gal and are
    # dropped rather than reported as a $0.00 stop.
    if gallons <= 0:
        return None
    cost = (gallons * station.retail_price).quantize(CENTS, rounding=ROUND_HALF_UP)
    return FuelStop(
        station=station,
        distance_from_origin_miles=distance,
        gallons_purchased=gallons,
        cost=cost,
    )


def plan_fuel_stops(candidates: list[dict], route_distance_miles: float) -> list[FuelStop]:
    """Return the minimum-cost list of fuel purchases for the route.

    The vehicle starts full (the origin is never a paid stop) and needs no
    reserve at the destination. Raises InfeasiblePlanError if some stretch
    longer than VEHICLE_RANGE_MILES has no station.
    """
    stations = sorted(
        (
            c
            for c in candidates
            if 0 < c["distance_from_origin_miles"] <= route_distance_miles
        ),
        # Tie-break equal distances by price then opis_id: city-level geocoding
        # gives every station in a city the same distance, and step 2's "first
        # strictly cheaper" scan must see the cheapest of them first.
        key=lambda c: (
            c["distance_from_origin_miles"],
            c["station"].retail_price,
            c["station"].opis_id,
        ),
    )

    pos = 0.0
    fuel = float(VEHICLE_RANGE_MILES)
    price: Decimal | float = math.inf
    current_station = None  # None while still at the origin
    stops: list[FuelStop] = []
    start = 0  # index of the first station beyond `pos`

    while True:
        while start < len(stations) and stations[start]["distance_from_origin_miles"] <= pos:
            start += 1
        horizon = min(pos + VEHICLE_RANGE_MILES, route_distance_miles)
        window = []
        for c in stations[start:]:
            if c["distance_from_origin_miles"] > horizon:
                break
            window.append(c)

        # Step 2: first strictly cheaper station within full-tank range.
        cheaper = next((c for c in window if c["station"].retail_price < price), None)
        if cheaper is not None:
            d = cheaper["distance_from_origin_miles"]
            leg = d - pos
            if current_station is not None:
                stop = _purchase(current_station, pos, leg - fuel)
                if stop:
                    stops.append(stop)
            pos, fuel = d, max(0.0, fuel - leg)
            current_station, price = cheaper["station"], cheaper["station"].retail_price
            continue

        remaining = route_distance_miles - pos
        # Step 3: current fuel already reaches the destination.
        if remaining <= fuel:
            return stops
        # Step 4: destination reachable after topping up here.
        if remaining <= VEHICLE_RANGE_MILES:
            stop = _purchase(current_station, pos, remaining - fuel)
            if stop:
                stops.append(stop)
            return stops
        # Step 5: nothing reachable.
        if not window:
            next_point = (
                stations[start]["distance_from_origin_miles"]
                if start < len(stations)
                else route_distance_miles
            )
            raise InfeasiblePlanError(gap_miles=next_point - pos, near_mile=pos)
        # Step 6: fill up, jump to cheapest in range (farthest, then lowest opis_id).
        target = min(
            window,
            key=lambda c: (
                c["station"].retail_price,
                -c["distance_from_origin_miles"],
                c["station"].opis_id,
            ),
        )
        stop = _purchase(current_station, pos, VEHICLE_RANGE_MILES - fuel)
        if stop:
            stops.append(stop)
        d = target["distance_from_origin_miles"]
        pos, fuel = d, VEHICLE_RANGE_MILES - (d - pos)
        current_station, price = target["station"], target["station"].retail_price
