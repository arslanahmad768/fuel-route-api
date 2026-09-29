"""
Route + fuel planning orchestration service.

`RouteFuelPlanningService.plan()` composes the routing client, route
matching, and fuel optimizer services (in that order) into a single response
dict matching the API contract. It does not
reimplement any logic from those services — it only resolves inputs,
sequences the calls, and assembles the output shape.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from fuelroute.constants import ROUTE_CORRIDOR_BUFFER_MILES
from fuelroute.services import fuel_optimizer, route_matching, routing_client
from fuelroute.services.routing_client import LocationNotFound

logger = logging.getLogger(__name__)

START_FIELDS = ("start_lat", "start_lng", "start")
FINISH_FIELDS = ("finish_lat", "finish_lng", "finish")


def _has_coordinates(request_data: dict, lat_field: str, lng_field: str) -> bool:
    return request_data.get(lat_field) is not None and request_data.get(lng_field) is not None


def _resolve_endpoints(
    request_data: dict,
) -> tuple[tuple[tuple[float, float], str], tuple[tuple[float, float], str]]:
    """Resolve start and finish, geocoding both at once when both need it.

    The two lookups are independent, so running them concurrently saves one
    full ORS round trip. If both fail, the start's error is raised first,
    matching what a caller would see from resolving them in order.
    """
    start_needs_geocoding = not _has_coordinates(request_data, *START_FIELDS[:2])
    finish_needs_geocoding = not _has_coordinates(request_data, *FINISH_FIELDS[:2])

    if start_needs_geocoding and finish_needs_geocoding:
        with ThreadPoolExecutor(max_workers=2) as executor:
            start_future = executor.submit(_resolve_endpoint, request_data, *START_FIELDS)
            finish_future = executor.submit(_resolve_endpoint, request_data, *FINISH_FIELDS)
            return start_future.result(), finish_future.result()

    return (
        _resolve_endpoint(request_data, *START_FIELDS),
        _resolve_endpoint(request_data, *FINISH_FIELDS),
    )


def _resolve_endpoint(
    request_data: dict, lat_field: str, lng_field: str, text_field: str
) -> tuple[tuple[float, float], str]:
    """Resolve one endpoint (start or finish) to ((lat, lon), echo_label).

    Coordinates take precedence over free text when both are given, per the
    spec. Skips geocoding entirely when coordinates are supplied.
    """
    if _has_coordinates(request_data, lat_field, lng_field):
        lat = request_data[lat_field]
        lng = request_data[lng_field]
        label = f"{lat:.4f},{lng:.4f}"
        return (lat, lng), label

    text = request_data.get(text_field)
    try:
        coords = routing_client.geocode(text)
    except LocationNotFound:
        # Re-raise with just the offending query text as the message, so the
        # view can format the exact error body
        # ("Could not resolve location: <value>") without depending on the
        # routing client's own (differently-worded) exception message.
        raise LocationNotFound(text) from None
    return coords, text


class RouteFuelPlanningService:
    """Orchestrates routing, corridor matching, and fuel-stop planning."""

    @staticmethod
    def plan(request_data: dict) -> dict:
        (start_coords, start_label), (finish_coords, finish_label) = _resolve_endpoints(
            request_data
        )

        route = routing_client.get_route(start_coords, finish_coords)

        candidate_stations = route_matching.find_candidate_stations(
            route.geometry, ROUTE_CORRIDOR_BUFFER_MILES
        )
        projected = route_matching.project_stations_onto_route(
            candidate_stations, route.geometry["coordinates"]
        )

        fuel_stops = fuel_optimizer.plan_fuel_stops(projected, route.distance_miles)

        fuel_stop_dicts = [
            {
                "station_name": stop.station.name,
                "city": stop.station.city,
                "state": stop.station.state,
                "distance_from_origin_miles": round(stop.distance_from_origin_miles, 1),
                "price_per_gallon": float(stop.station.retail_price),
                "gallons_purchased": float(stop.gallons_purchased),
                "cost": float(stop.cost),
            }
            for stop in fuel_stops
        ]

        total_fuel_cost = sum((stop.cost for stop in fuel_stops), Decimal("0"))
        total_gallons = sum((stop.gallons_purchased for stop in fuel_stops), Decimal("0"))

        logger.info(
            "Fuel optimizer produced %d stop(s), total_fuel_cost=%s",
            len(fuel_stops),
            total_fuel_cost,
        )

        return {
            "start": start_label,
            "finish": finish_label,
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_hours, 1),
            "route_geometry": route.geometry,
            "fuel_stops": fuel_stop_dicts,
            "total_fuel_cost": float(total_fuel_cost),
            "total_gallons": float(total_gallons),
        }
