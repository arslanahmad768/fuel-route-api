"""
Routing client service — the only module in this codebase that talks to
OpenRouteService (ORS).

Wraps ORS's geocoding and directions HTTP endpoints, translating transport
failures (network errors, timeouts, rate limiting, empty results) into a
small set of domain exceptions that callers can catch without knowing
anything about ORS or `requests`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.conf import settings

from fuelroute.constants import METERS_PER_MILE

logger = logging.getLogger(__name__)

ORS_BASE_URL = "https://api.openrouteservice.org"
REQUEST_TIMEOUT_SECONDS = 10


class RoutingError(Exception):
    """Base exception for all routing-client failures."""


class RoutingUnavailable(RoutingError):
    """Raised when ORS cannot be reached or returns a server error."""


class RoutingRateLimited(RoutingError):
    """Raised when ORS responds with HTTP 429."""


class LocationNotFound(RoutingError):
    """Raised when a geocode query returns zero results."""


@dataclass
class RouteResult:
    """Result of a driving-car route request, in system-native units."""

    distance_miles: float
    duration_hours: float
    geometry: dict


def _auth_headers() -> dict:
    return {"Authorization": settings.ORS_API_KEY}


def geocode(query: str) -> tuple[float, float]:
    """Resolve a free-text location to (latitude, longitude) via ORS.

    Raises:
        LocationNotFound: the query matched zero locations.
        RoutingRateLimited: ORS responded with HTTP 429.
        RoutingUnavailable: a network error, timeout, or 5xx response.
    """
    url = f"{ORS_BASE_URL}/geocode/search"
    params = {"text": query, "size": 1, "boundary.country": "US"}

    logger.info("Calling ORS geocode for query=%r", query)

    try:
        response = requests.get(
            url,
            params=params,
            headers=_auth_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        logger.info("ORS geocode request failed: %s", exc)
        raise RoutingUnavailable(f"Network error contacting ORS geocode: {exc}") from exc

    if response.status_code == 429:
        logger.info("ORS geocode rate limited (429) for query=%r", query)
        raise RoutingRateLimited("ORS rate limit exceeded for geocode request")

    if response.status_code >= 500:
        logger.info(
            "ORS geocode returned server error %s for query=%r",
            response.status_code,
            query,
        )
        raise RoutingUnavailable(
            f"ORS geocode returned server error {response.status_code}"
        )

    if response.status_code != 200:
        logger.info(
            "ORS geocode returned unexpected status %s for query=%r",
            response.status_code,
            query,
        )
        raise RoutingUnavailable(
            f"ORS geocode returned unexpected status {response.status_code}: {response.text}"
        )

    data = response.json()
    features = data.get("features", [])
    if not features:
        logger.info("ORS geocode found no results for query=%r", query)
        raise LocationNotFound(f"No geocoding results for query: {query!r}")

    lon, lat = features[0]["geometry"]["coordinates"]
    logger.info("ORS geocode succeeded for query=%r -> (%s, %s)", query, lat, lon)
    return (lat, lon)


def get_route(
    start: tuple[float, float], finish: tuple[float, float]
) -> RouteResult:
    """Fetch a driving route between two (latitude, longitude) points.

    Args:
        start: (latitude, longitude) of the route origin.
        finish: (latitude, longitude) of the route destination.

    Raises:
        RoutingRateLimited: ORS responded with HTTP 429.
        RoutingUnavailable: a network error, timeout, or 5xx response.
    """
    url = f"{ORS_BASE_URL}/v2/directions/driving-car/geojson"
    start_lat, start_lon = start
    finish_lat, finish_lon = finish
    body = {
        "coordinates": [
            [start_lon, start_lat],
            [finish_lon, finish_lat],
        ]
    }

    logger.info("Calling ORS directions from %s to %s", start, finish)

    try:
        response = requests.post(
            url,
            json=body,
            headers=_auth_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        logger.info("ORS directions request failed: %s", exc)
        raise RoutingUnavailable(f"Network error contacting ORS directions: {exc}") from exc

    if response.status_code == 429:
        logger.info("ORS directions rate limited (429)")
        raise RoutingRateLimited("ORS rate limit exceeded for directions request")

    if response.status_code >= 500:
        logger.info("ORS directions returned server error %s", response.status_code)
        raise RoutingUnavailable(
            f"ORS directions returned server error {response.status_code}"
        )

    if response.status_code != 200:
        logger.info("ORS directions returned unexpected status %s", response.status_code)
        raise RoutingUnavailable(
            f"ORS directions returned unexpected status {response.status_code}: {response.text}"
        )

    data = response.json()
    feature = data["features"][0]
    summary = feature["properties"]["summary"]
    distance_meters = summary["distance"]
    duration_seconds = summary["duration"]
    geometry = feature["geometry"]

    result = RouteResult(
        distance_miles=distance_meters / METERS_PER_MILE,
        duration_hours=duration_seconds / 3600,
        geometry=geometry,
    )
    logger.info(
        "ORS directions succeeded: distance_miles=%.1f duration_hours=%.1f",
        result.distance_miles,
        result.duration_hours,
    )
    return result
