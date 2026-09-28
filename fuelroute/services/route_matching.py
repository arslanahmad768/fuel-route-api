"""
Route corridor matching service.

Given a route's GeoJSON geometry and the full `FuelStation` queryset, finds
only the stations within a corridor buffer of the route and annotates each
with its distance travelled along the route. This lets a later fuel-stop
algorithm treat the trip as a 1-D line of stations by distance, entirely
in-process — no external API calls, no per-station queries.

GeoJSON convention: route coordinates are `(longitude, latitude)` pairs.
This module is careful to keep that ordering straight everywhere it takes
raw route geometry, while all (lat, lon)-shaped tuples used internally for
distance math follow the more conventional `(latitude, longitude)` order.
"""

from __future__ import annotations

import math

from django.db.models import QuerySet

from fuelroute.constants import EARTH_RADIUS_MILES, ROUTE_CORRIDOR_BUFFER_MILES
from fuelroute.models import FuelStation


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two (lat, lon) points, in miles."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_MILES * c


def _nearest_point_on_segment(
    point: tuple[float, float],
    seg_start: tuple[float, float],
    seg_end: tuple[float, float],
) -> tuple[float, float]:
    """Approximate nearest point on the segment to `point`, as a (lat, lon).

    Projects everything onto a local equirectangular plane (using the
    segment's mean latitude to correct for longitude compression), clamps
    the projection parameter `t` to the [0, 1] segment range, then
    un-projects the result back to (lat, lon).
    """
    lat, lon = point
    lat1, lon1 = seg_start
    lat2, lon2 = seg_end

    mean_lat = math.radians((lat1 + lat2) / 2)
    cos_mean_lat = math.cos(mean_lat)

    # Project to a local planar (x, y) system in "latitude units", where
    # x is longitude scaled by cos(mean_lat) to correct for compression.
    def to_plane(p_lat: float, p_lon: float) -> tuple[float, float]:
        return (p_lon * cos_mean_lat, p_lat)

    x, y = to_plane(lat, lon)
    x1, y1 = to_plane(lat1, lon1)
    x2, y2 = to_plane(lat2, lon2)

    dx = x2 - x1
    dy = y2 - y1
    segment_length_sq = dx * dx + dy * dy

    if segment_length_sq == 0:
        # Degenerate segment (start == end): nearest point is seg_start.
        return (lat1, lon1)

    t = ((x - x1) * dx + (y - y1) * dy) / segment_length_sq
    t = max(0.0, min(1.0, t))
    nearest_x = x1 + t * dx
    nearest_y = y1 + t * dy
    # Un-project back to (lat, lon).
    return (nearest_y, nearest_x / cos_mean_lat)


def point_to_segment_distance_miles(
    point: tuple[float, float],
    seg_start: tuple[float, float],
    seg_end: tuple[float, float],
) -> float:
    """Approximate perpendicular distance from `point` to the segment.

    All inputs are (lat, lon) tuples. Uses an equirectangular projection to
    flatten the segment and point into a local planar coordinate system
    (acceptable at this scale — a few hundred miles at most), finds the
    nearest point on the segment in that plane, then measures the distance
    back from `point` to that projected nearest point with `haversine_miles`.
    """
    lat, lon = point
    nearest_lat, nearest_lon = _nearest_point_on_segment(point, seg_start, seg_end)
    return haversine_miles(lat, lon, nearest_lat, nearest_lon)


def bounding_box(
    coordinates: list[tuple[float, float]], buffer_miles: float
) -> tuple[float, float, float, float]:
    """Compute a lat/lon bounding box around GeoJSON `coordinates`.

    `coordinates` is a list of `(longitude, latitude)` pairs (GeoJSON
    convention). The box is expanded by `buffer_miles` in every direction,
    converting miles to degrees (`buffer_miles / 69.0` for latitude, and
    that same delta corrected by `cos(radians(mean_lat))` for longitude,
    to account for longitude compression at higher latitudes).

    Returns (min_lat, max_lat, min_lon, max_lon).
    """
    lats = [lat for lon, lat in coordinates]
    lons = [lon for lon, lat in coordinates]

    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    mean_lat = (min_lat + max_lat) / 2
    lat_delta = buffer_miles / 69.0
    lon_delta = lat_delta / math.cos(math.radians(mean_lat))

    return (
        min_lat - lat_delta,
        max_lat + lat_delta,
        min_lon - lon_delta,
        max_lon + lon_delta,
    )


def find_candidate_stations(
    route_geometry: dict, buffer_miles: float
) -> QuerySet:
    """Prefilter `FuelStation` rows to those inside the route's bounding box.

    `route_geometry` is a GeoJSON geometry dict (e.g. a LineString) with a
    `"coordinates"` key of `(longitude, latitude)` pairs. This is a coarse
    ORM-level prefilter only — no per-segment distance math happens here.
    Stations with null latitude/longitude are excluded.
    """
    coordinates = route_geometry["coordinates"]
    min_lat, max_lat, min_lon, max_lon = bounding_box(coordinates, buffer_miles)

    return FuelStation.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False,
        latitude__range=(min_lat, max_lat),
        longitude__range=(min_lon, max_lon),
    )


def project_stations_onto_route(
    candidate_stations,
    route_coordinates: list[tuple[float, float]],
) -> list[dict]:
    """Project candidate stations onto the route and filter to the corridor.

    `route_coordinates` is a list of `(longitude, latitude)` pairs (GeoJSON
    convention). For each station, finds the nearest route segment (looping
    over consecutive coordinate pairs), computes the perpendicular distance
    to it, and keeps only stations within `ROUTE_CORRIDOR_BUFFER_MILES`.

    `distance_from_origin_miles` is the cumulative route length up to the
    nearest segment's start point, plus the station's projected offset
    along that segment.

    Returns a list of `{"station": station_obj, "distance_from_origin_miles":
    float}` dicts, sorted ascending by that distance.
    """
    # Convert route coordinates to (lat, lon) and precompute cumulative
    # distance travelled up to the start of each segment.
    route_points = [(lat, lon) for lon, lat in route_coordinates]
    cumulative_distances = [0.0]
    for i in range(1, len(route_points)):
        prev = route_points[i - 1]
        curr = route_points[i]
        cumulative_distances.append(
            cumulative_distances[-1] + haversine_miles(prev[0], prev[1], curr[0], curr[1])
        )

    results = []

    for station in candidate_stations:
        station_point = (float(station.latitude), float(station.longitude))

        best_distance = None
        best_offset_from_origin = None

        for i in range(len(route_points) - 1):
            seg_start = route_points[i]
            seg_end = route_points[i + 1]

            nearest_point = _nearest_point_on_segment(station_point, seg_start, seg_end)
            distance = haversine_miles(
                station_point[0], station_point[1], nearest_point[0], nearest_point[1]
            )

            if best_distance is None or distance < best_distance:
                best_distance = distance

                # Offset (in miles) from this segment's start to the
                # nearest projected point, added to the segment's
                # cumulative start distance.
                offset_along_segment = haversine_miles(
                    seg_start[0], seg_start[1], nearest_point[0], nearest_point[1]
                )
                best_offset_from_origin = cumulative_distances[i] + offset_along_segment

        if best_distance is not None and best_distance <= ROUTE_CORRIDOR_BUFFER_MILES:
            results.append(
                {
                    "station": station,
                    "distance_from_origin_miles": best_offset_from_origin,
                }
            )

    results.sort(key=lambda r: r["distance_from_origin_miles"])
    return results
