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
from collections.abc import Iterable

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


# Side length (degrees) of the spatial grid used to bucket route segments.
# ~0.05 deg is ~3.5 mi of latitude: comparable to the corridor width, so a
# station's cell only holds segments that are genuinely near it.
GRID_CELL_DEGREES = 0.05

# Safety factor applied to the corridor buffer when expanding segment
# bounding boxes. The miles-to-degrees bounds in `_expanded_bbox` are exact
# up to a sine-vs-angle correction far below 0.1% at a few miles, so 1.25 is
# a very generous margin: a segment is only ever skipped if it is provably
# farther than the buffer.
_BBOX_SAFETY_FACTOR = 1.25

_MILES_PER_DEGREE = EARTH_RADIUS_MILES * math.pi / 180.0


def _expanded_bbox(
    lat_a: float, lon_a: float, lat_b: float, lon_b: float, buffer_miles: float
) -> tuple[float, float, float, float]:
    """Axis-aligned (lat, lon) box around a segment, expanded conservatively.

    Any point whose haversine distance to *some point on the segment* is at
    most `buffer_miles` is guaranteed to lie inside the returned box:

    * latitude: from the haversine formula, `d >= 2R * |sin(dlat / 2)|`,
      i.e. `d >= R * |dlat|` (radians) up to a negligible sine correction,
      so `|dlat| <= buffer / miles_per_degree`;
    * longitude: likewise `d >= 2R * sqrt(cos(lat1) * cos(lat2)) *
      |sin(dlon / 2)|`, and both latitudes are no more extreme than the
      box's own (already lat-expanded) latitude bounds, so dividing the
      latitude margin by the cosine of the most extreme latitude is safe.

    `_BBOX_SAFETY_FACTOR` covers the sine corrections with a wide margin.
    The nearest point `_nearest_point_on_segment` returns always lies on the
    straight (lat, lon) segment (a clamped linear interpolation), which is
    what makes "distance to some point on the segment" the right premise.

    Returns (min_lat, max_lat, min_lon, max_lon).
    """
    lat_margin = _BBOX_SAFETY_FACTOR * buffer_miles / _MILES_PER_DEGREE
    min_lat = min(lat_a, lat_b) - lat_margin
    max_lat = max(lat_a, lat_b) + lat_margin
    extreme_lat = min(89.0, max(abs(min_lat), abs(max_lat)))
    lon_margin = lat_margin / math.cos(math.radians(extreme_lat))
    return (
        min_lat,
        max_lat,
        min(lon_a, lon_b) - lon_margin,
        max(lon_a, lon_b) + lon_margin,
    )


def _cell_of(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / GRID_CELL_DEGREES), math.floor(lon / GRID_CELL_DEGREES))


def _build_segment_grid(
    route_points: list[tuple[float, float]], buffer_miles: float
) -> tuple[dict[tuple[int, int], list[int]], list[tuple[float, float, float, float]]]:
    """Bucket route segments into grid cells for fast nearby-segment lookup.

    Returns `(grid, segment_boxes)`: `grid` maps a cell to the ascending list
    of segment indices whose buffer-expanded footprint touches that cell, and
    `segment_boxes[i]` is segment `i`'s expanded bounding box (used as a cheap
    per-segment pre-check).

    Correctness guarantee: if a station is within `buffer_miles` of segment
    `i`, then `i` is in the station's own cell's list. Long segments (sparse
    geometry) are split into sub-pieces no longer than one cell per axis
    before being expanded and registered, so a long diagonal segment only
    touches the cells along its path, not its whole bounding rectangle.
    """
    grid: dict[tuple[int, int], list[int]] = {}
    segment_boxes: list[tuple[float, float, float, float]] = []

    for i in range(len(route_points) - 1):
        lat1, lon1 = route_points[i]
        lat2, lon2 = route_points[i + 1]
        segment_boxes.append(_expanded_bbox(lat1, lon1, lat2, lon2, buffer_miles))

        # Split into `pieces` equal sub-pieces, each spanning at most one grid
        # cell in each axis. Every point on the segment lies on (and hence in
        # the bounding box of) some sub-piece.
        span = max(abs(lat2 - lat1), abs(lon2 - lon1))
        pieces = max(1, math.ceil(span / GRID_CELL_DEGREES))
        cells: set[tuple[int, int]] = set()
        prev_lat, prev_lon = lat1, lon1
        for k in range(1, pieces + 1):
            if k == pieces:
                next_lat, next_lon = lat2, lon2
            else:
                frac = k / pieces
                next_lat = lat1 + (lat2 - lat1) * frac
                next_lon = lon1 + (lon2 - lon1) * frac
            b_min_lat, b_max_lat, b_min_lon, b_max_lon = _expanded_bbox(
                prev_lat, prev_lon, next_lat, next_lon, buffer_miles
            )
            row_lo, col_lo = _cell_of(b_min_lat, b_min_lon)
            row_hi, col_hi = _cell_of(b_max_lat, b_max_lon)
            for row in range(row_lo, row_hi + 1):
                for col in range(col_lo, col_hi + 1):
                    cells.add((row, col))
            prev_lat, prev_lon = next_lat, next_lon

        # Segments are visited in ascending index order, and each is added
        # at most once per cell, so every cell's list stays sorted ascending.
        for cell in cells:
            grid.setdefault(cell, []).append(i)

    return grid, segment_boxes


def project_stations_onto_route(
    candidate_stations: Iterable[FuelStation],
    route_coordinates: list[tuple[float, float]],
) -> list[dict]:
    """Project candidate stations onto the route and filter to the corridor.

    `route_coordinates` is a list of `(longitude, latitude)` pairs (GeoJSON
    convention). For each station, finds the nearest route segment, computes
    the perpendicular distance to it, and keeps only stations within
    `ROUTE_CORRIDOR_BUFFER_MILES`.

    `distance_from_origin_miles` is the cumulative route length up to the
    nearest segment's start point, plus the station's projected offset
    along that segment.

    Performance: real ORS polylines have thousands of points, so testing
    every station against every segment is far too slow. Segments are first
    bucketed into a coarse spatial grid (`_build_segment_grid`); each station
    is only tested against the segments registered in its own grid cell, and
    each of those is first rejected by a cheap bounding-box check before any
    projection/haversine math runs. Both filters are conservative — they only
    ever skip segments provably farther than the corridor buffer — and the
    surviving segments are visited in ascending index order with the same
    strict `<` comparison, so the matched set, the chosen nearest segment
    (including ties) and the computed distances are identical to the
    exhaustive all-segments scan.

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

    grid, segment_boxes = _build_segment_grid(route_points, ROUTE_CORRIDOR_BUFFER_MILES)

    results = []

    for station in candidate_stations:
        station_point = (float(station.latitude), float(station.longitude))
        station_lat, station_lon = station_point

        best_distance = None
        best_offset_from_origin = None

        for i in grid.get(_cell_of(station_lat, station_lon), ()):
            min_lat, max_lat, min_lon, max_lon = segment_boxes[i]
            if not (
                min_lat <= station_lat <= max_lat and min_lon <= station_lon <= max_lon
            ):
                # Provably farther than the corridor buffer: cannot be the
                # nearest segment of a station that ends up being kept.
                continue

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
