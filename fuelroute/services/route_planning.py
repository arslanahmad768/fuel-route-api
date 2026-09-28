"""
Route + fuel planning orchestration service.

`RouteFuelPlanningService.plan()` composes the routing client, route
matching, and fuel optimizer services (in that order) into a single response
dict matching the API contract. It does not
reimplement any logic from those services — it only resolves inputs,
sequences the calls, and assembles the output shape.
"""

from __future__ import annotations

from decimal import Decimal

from fuelroute.constants import ROUTE_CORRIDOR_BUFFER_MILES
from fuelroute.services import fuel_optimizer, route_matching, routing_client
from fuelroute.services.routing_client import LocationNotFound


def _resolve_endpoint(
    request_data: dict, lat_field: str, lng_field: str, text_field: str
) -> tuple[tuple[float, float], str]:
    """Resolve one endpoint (start or finish) to ((lat, lon), echo_label).

    Coordinates take precedence over free text when both are given, per the
    spec. Skips geocoding entirely when coordinates are supplied.
    """
    lat = request_data.get(lat_field)
    lng = request_data.get(lng_field)
    if lat is not None and lng is not None:
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
        start_coords, start_label = _resolve_endpoint(
            request_data, "start_lat", "start_lng", "start"
        )
        finish_coords, finish_label = _resolve_endpoint(
            request_data, "finish_lat", "finish_lng", "finish"
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
