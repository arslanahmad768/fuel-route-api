"""
Tests for the fuel-stop optimizer.

Every expected value below was derived by hand (trace in the comment above
each assertion block), not by running the implementation. Notation in the
traces: "@d $p" = station at mile d with price p; fuel is in miles of range.
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from fuelroute.services.fuel_optimizer import (
    FuelStop,
    InfeasiblePlanError,
    plan_fuel_stops,
)


@dataclass(frozen=True)
class FakeStation:
    opis_id: int
    retail_price: Decimal


def cand(opis_id, distance, price):
    return {
        "station": FakeStation(opis_id=opis_id, retail_price=Decimal(price)),
        "distance_from_origin_miles": float(distance),
    }


def summary(stops):
    """(opis_id, distance, gallons, cost) per stop, for exact comparison."""
    return [
        (s.station.opis_id, s.distance_from_origin_miles, s.gallons_purchased, s.cost)
        for s in stops
    ]


def total_cost(stops):
    return sum((s.cost for s in stops), Decimal("0"))


# --- Scenario 1: route shorter than 500 miles -> zero stops -----------------

def test_route_under_range_needs_no_stops():
    # Route 300. Origin(fuel 500) -> @100 (buy 0, fuel 400) -> @200 cheaper
    # (buy 0, fuel 300). At @200 remaining 100 <= fuel 300 -> done.
    candidates = [cand(1, 100, "3.00"), cand(2, 200, "2.00")]
    assert plan_fuel_stops(candidates, 300.0) == []


def test_route_under_range_with_no_stations_needs_no_stops():
    assert plan_fuel_stops([], 499.9) == []


def test_route_exactly_range_needs_no_stops():
    assert plan_fuel_stops([], 500.0) == []


# --- Scenario 2: route slightly over 500 miles -> exactly one stop -----------

def test_counterexample_a_final_leg_is_purchased():
    # Route 520; S1 @100 $3.00, S2 @400 $4.00.
    # Origin -> S1 (buy 0, fuel 400). At S1: S2 not cheaper; remaining 420 >
    # fuel 400 but <= 500 -> buy 20 mi = 2.00 gal x 3.00 = $6.00. One stop.
    candidates = [cand(1, 100, "3.00"), cand(2, 400, "4.00")]
    stops = plan_fuel_stops(candidates, 520.0)
    assert summary(stops) == [(1, 100.0, Decimal("2.00"), Decimal("6.00"))]
    assert total_cost(stops) == Decimal("6.00")


def test_slightly_over_range_buys_at_cheapest_near_end():
    # Route 510; @50 $3.50 (1), @450 $3.00 (2), @480 $3.20 (3).
    # Origin -> @50 (fuel 450). At @50: first strictly cheaper = @450,
    # leg 400 <= fuel -> buy 0, fuel 50. At @450: @480 not cheaper;
    # remaining 60 > fuel 50, <= 500 -> buy 10 mi = 1.00 gal x 3.00 = $3.00.
    candidates = [cand(1, 50, "3.50"), cand(2, 450, "3.00"), cand(3, 480, "3.20")]
    stops = plan_fuel_stops(candidates, 510.0)
    assert summary(stops) == [(2, 450.0, Decimal("1.00"), Decimal("3.00"))]


# --- Scenario 3: cheapest station overall is not the nearest ----------------

def test_cheapest_overall_not_nearest_is_favored():
    # Route 900; @100 $4.00 (1), @250 $3.00 (2), @450 $3.80 (3), @700 $3.50 (4).
    # Origin -> @100 (fuel 400). At @100: first cheaper = @250, buy 0, fuel 250.
    # At @250: window (250,750] = {@450 3.80, @700 3.50}, none cheaper;
    # remaining 650 > 500 -> fill 250 mi = 25.00 gal x 3.00 = $75.00; jump to
    # cheapest @700, fuel 500-450 = 50. At @700: window empty; remaining 200 >
    # 50, <= 500 -> buy 150 mi = 15.00 gal x 3.50 = $52.50. Total $127.50.
    # (Optimality: 400 mi must be bought; max 250 at $3.00, remaining 150 must
    # be bought past mile 750's reach -> only @700 $3.50 qualifies.)
    candidates = [
        cand(1, 100, "4.00"),
        cand(2, 250, "3.00"),
        cand(3, 450, "3.80"),
        cand(4, 700, "3.50"),
    ]
    stops = plan_fuel_stops(candidates, 900.0)
    assert summary(stops) == [
        (2, 250.0, Decimal("25.00"), Decimal("75.00")),
        (4, 700.0, Decimal("15.00"), Decimal("52.50")),
    ]
    assert total_cost(stops) == Decimal("127.50")


# --- Scenario 4: nearest station is more expensive than a farther one --------

def test_counterexample_b_nearest_expensive_station_is_skipped():
    # Route 700; A @50 $5.00, B @300 $3.00.
    # Origin -> A (fuel 450). At A: B cheaper, leg 250 <= 450 -> buy 0, fuel 200.
    # At B: remaining 400 > 200, <= 500 -> buy 200 mi = 20.00 gal x 3 = $60.00.
    candidates = [cand(1, 50, "5.00"), cand(2, 300, "3.00")]
    stops = plan_fuel_stops(candidates, 700.0)
    assert summary(stops) == [(2, 300.0, Decimal("20.00"), Decimal("60.00"))]
    assert total_cost(stops) == Decimal("60.00")


def test_counterexample_d_cheaper_station_beats_early_finish():
    # Route 550; A @100 $5.00, B @150 $3.00.
    # Origin -> A (fuel 400). At A: B cheaper (checked before "done"), leg 50
    # -> buy 0, fuel 350. At B: remaining 400 > 350, <= 500 -> buy 50 mi =
    # 5.00 gal x 3.00 = $15.00 (not $25.00 at A).
    candidates = [cand(1, 100, "5.00"), cand(2, 150, "3.00")]
    stops = plan_fuel_stops(candidates, 550.0)
    assert summary(stops) == [(2, 150.0, Decimal("5.00"), Decimal("15.00"))]


# --- Scenario 5: cheaper future station exists but is out of range -----------

def test_cheap_station_out_of_range_buys_to_intermediate():
    # Route 1200; @300 $4.00 (1), @600 $3.60 (2), @850 $2.00 (3).
    # Origin -> @300 (fuel 200). At @300: window (300,800] = {@600} (@850 is
    # 550 away, out of range); @600 cheaper, leg 300 -> buy 100 mi =
    # 10.00 gal x 4.00 = $40.00, arrive fuel 0. At @600: @850 cheaper, leg 250
    # -> buy 25.00 gal x 3.60 = $90.00, fuel 0. At @850: remaining 350 -> buy
    # 35.00 gal x 2.00 = $70.00. Total $200.00.
    candidates = [cand(1, 300, "4.00"), cand(2, 600, "3.60"), cand(3, 850, "2.00")]
    stops = plan_fuel_stops(candidates, 1200.0)
    assert summary(stops) == [
        (1, 300.0, Decimal("10.00"), Decimal("40.00")),
        (2, 600.0, Decimal("25.00"), Decimal("90.00")),
        (3, 850.0, Decimal("35.00"), Decimal("70.00")),
    ]
    assert total_cost(stops) == Decimal("200.00")


# --- Scenario 6: multiple stops (route > 1000 miles) --------------------------

def test_counterexample_c_multi_stop_fill_then_top_up():
    # Route 1200; A @100 $3.00, B @400 $3.50, C @800 $3.20.
    # Origin -> A (fuel 400). At A: B not cheaper, remaining 1100 > 500 ->
    # fill 100 mi = 10.00 gal x 3.00 = $30.00, jump to B, fuel 500-300 = 200.
    # At B: C cheaper, leg 400 -> buy 200 mi = 20.00 gal x 3.50 = $70.00,
    # fuel 0. At C: remaining 400 -> 40.00 gal x 3.20 = $128.00. Total $228.00.
    candidates = [cand(1, 100, "3.00"), cand(2, 400, "3.50"), cand(3, 800, "3.20")]
    stops = plan_fuel_stops(candidates, 1200.0)
    assert summary(stops) == [
        (1, 100.0, Decimal("10.00"), Decimal("30.00")),
        (2, 400.0, Decimal("20.00"), Decimal("70.00")),
        (3, 800.0, Decimal("40.00"), Decimal("128.00")),
    ]
    assert total_cost(stops) == Decimal("228.00")


def test_candidates_are_sorted_defensively():
    # Same as counterexample C but shuffled input; plus a station at mile 0
    # and one past the destination, both of which must be ignored.
    candidates = [
        cand(3, 800, "3.20"),
        cand(9, 1300, "0.01"),
        cand(1, 100, "3.00"),
        cand(8, 0, "0.01"),
        cand(2, 400, "3.50"),
    ]
    stops = plan_fuel_stops(candidates, 1200.0)
    assert [s.station.opis_id for s in stops] == [1, 2, 3]
    assert total_cost(stops) == Decimal("228.00")


def test_fill_tie_break_farthest_then_lowest_opis_id():
    # Route 1000; @100 $3.00 (10), @300 $3.50 (5), @400 $3.50 (7),
    # @400 $3.50 (3), @800 $3.40 (8).
    # Origin -> @100 (fuel 400). At @100: nothing cheaper, remaining 900 > 500
    # -> fill 100 mi = 10.00 gal x 3.00 = $30.00. Cheapest in window is $3.50,
    # tie -> farthest (400) -> lowest opis_id (3). fuel 500-300 = 200.
    # At @400(3): @800 cheaper, leg 400 -> buy 200 mi = 20.00 gal x 3.50 =
    # $70.00, fuel 0. At @800: remaining 200 -> 20.00 gal x 3.40 = $68.00.
    candidates = [
        cand(10, 100, "3.00"),
        cand(5, 300, "3.50"),
        cand(7, 400, "3.50"),
        cand(3, 400, "3.50"),
        cand(8, 800, "3.40"),
    ]
    stops = plan_fuel_stops(candidates, 1000.0)
    assert summary(stops) == [
        (10, 100.0, Decimal("10.00"), Decimal("30.00")),
        (3, 400.0, Decimal("20.00"), Decimal("70.00")),
        (8, 800.0, Decimal("20.00"), Decimal("68.00")),
    ]
    assert total_cost(stops) == Decimal("168.00")


def test_strictly_decreasing_prices_buy_leg_by_leg():
    # Route 1100; stations @50k for k=1..21, price 5.00 - 0.10k.
    # Every next station is strictly cheaper, so each step targets the next
    # station. Arriving fuel at @50k = 500 - 50k, which hits 0 at k=10.
    # From k=10..20 each stop buys the 50-mi leg (5.00 gal); at k=21 (@1050)
    # the final 50 mi (5.00 gal). 12 stops x 5 gal = 60 gal = (1100-500)/10.
    # Cost = 5 * sum_{k=10}^{21}(5 - 0.1k) = 5 * (60 - 0.1*186) = $207.00.
    candidates = [
        cand(k, 50 * k, str(Decimal("5.00") - Decimal("0.10") * k)) for k in range(1, 22)
    ]
    stops = plan_fuel_stops(candidates, 1100.0)
    assert [s.station.opis_id for s in stops] == list(range(10, 22))
    assert all(s.gallons_purchased == Decimal("5.00") for s in stops)
    assert total_cost(stops) == Decimal("207.00")


# --- Scenario 7: no feasible plan ---------------------------------------------

def test_gap_between_stations_raises_with_gap_size():
    # Route 1000; @200, @750. Origin -> @200 (fuel 300). At @200: window
    # (200,700] empty, remaining 800 > 500 -> gap = 750 - 200 = 550 near 200.
    candidates = [cand(1, 200, "3.00"), cand(2, 750, "3.00")]
    with pytest.raises(InfeasiblePlanError) as exc:
        plan_fuel_stops(candidates, 1000.0)
    assert exc.value.gap_miles == pytest.approx(550.0)
    assert exc.value.near_mile == pytest.approx(200.0)
    assert str(exc.value) == (
        "No feasible fuel stop plan: gap of 550.0 miles near mile 200.0 "
        "exceeds vehicle range"
    )


def test_no_station_within_first_range_raises():
    # Route 800; only @600. Origin window (0,500] empty -> gap 600 near 0.
    with pytest.raises(InfeasiblePlanError, match="gap of 600.0 miles near mile 0.0"):
        plan_fuel_stops([cand(1, 600, "3.00")], 800.0)


def test_final_gap_to_destination_raises():
    # Route 1200; only @400. At @400: window (400,900] empty, remaining 800
    # -> gap to destination = 800 near 400.
    with pytest.raises(InfeasiblePlanError, match="gap of 800.0 miles near mile 400.0"):
        plan_fuel_stops([cand(1, 400, "3.00")], 1200.0)


# --- Scenario 8: long route, many closely-spaced stations --------------------

def test_many_close_stations_terminates_with_correct_stops():
    # Route 2000; stations every 10 mi from 10..1990, all $3.00 (ids = mile).
    # Origin -> @10 (fuel 490). At @10: nothing strictly cheaper, remaining
    # 1990 > 500 -> fill 10 mi = 1.00 gal ($3.00), jump to farthest equal-price
    # station in (10,510] = @510, fuel 0. @510: fill 50.00 gal ($150.00) ->
    # @1010. @1010: fill 50.00 gal ($150.00) -> @1510. @1510: remaining 490
    # <= 500 -> buy 49.00 gal ($147.00). 4 stops, 150 gal, $450.00.
    candidates = [cand(m, m, "3.00") for m in range(10, 2000, 10)]
    stops = plan_fuel_stops(candidates, 2000.0)
    assert summary(stops) == [
        (10, 10.0, Decimal("1.00"), Decimal("3.00")),
        (510, 510.0, Decimal("50.00"), Decimal("150.00")),
        (1010, 1010.0, Decimal("50.00"), Decimal("150.00")),
        (1510, 1510.0, Decimal("49.00"), Decimal("147.00")),
    ]
    assert total_cost(stops) == Decimal("450.00")


def test_dense_one_mile_spacing_terminates():
    # Route 3000; a station every mile, all $3.00. Total fuel bought must be
    # exactly (3000 - 500)/10 = 250 gallons -> $750.00 regardless of split.
    candidates = [cand(m, m, "3.00") for m in range(1, 3000)]
    stops = plan_fuel_stops(candidates, 3000.0)
    assert sum(s.gallons_purchased for s in stops) == Decimal("250.00")
    assert total_cost(stops) == Decimal("750.00")


# --- Money handling -----------------------------------------------------------

def test_gallons_and_cost_are_quantized_half_up():
    # Route 523.456; @100 $3.333. Arrive fuel 400, need 423.456 -> buy
    # 23.456 mi = 2.3456 gal -> 2.35; cost 2.35 x 3.333 = 7.83255 -> 7.83.
    stops = plan_fuel_stops([cand(1, 100, "3.333")], 523.456)
    assert len(stops) == 1
    assert isinstance(stops[0], FuelStop)
    assert stops[0].gallons_purchased == Decimal("2.35")
    assert stops[0].cost == Decimal("7.83")
