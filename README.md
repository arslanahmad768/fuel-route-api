# Fuel Route API

A Django REST API that, given a start and finish location in the USA, returns
the driving route plus a cost-optimal set of fuel stops — computed against a
real fuel-price dataset, subject to a vehicle with a 500-mile range and 10
MPG efficiency.

## Problem

Given `start` and `finish` (free text like `"Chicago, IL"`, or explicit
lat/lng coordinates), return:

- the route (distance, duration, geometry) between the two points,
- the set of fuel stops along that route that minimizes total fuel cost,
  respecting the vehicle's 500-mile range,
- the total fuel cost and total gallons purchased for the trip.

The routing/mapping side must use a free API, and — per the assignment's
explicit constraint — the whole request should make close to one call to
that external API (never one call per candidate station), since the
optimization itself has to run over ~8,000 candidate fuel stations.

## Architecture

```
                     POST /api/v1/route/
                            |
                            v
                    +-----------------+
                    |   RouteView     |   (fuelroute/views.py)
                    | - validates in  |
                    | - maps domain   |
                    |   exceptions to |
                    |   HTTP errors   |
                    +-----------------+
                            |
                            v
              +---------------------------+
              | RouteFuelPlanningService  |   (services/route_planning.py)
              | orchestrates the 3        |
              | services below, in order  |
              +---------------------------+
                 |         |          |
                 v         v          v
        +-----------+ +-----------+ +------------------+
        |  Routing  | |  Route    | |  Fuel Stop       |
        |  Client   | |  Matching | |  Optimizer       |
        |(ORS HTTP) | |(corridor  | |(greedy exchange- |
        |           | | project.) | | argument algo)   |
        +-----------+ +-----------+ +------------------+
              |             |               |
              v             v               v
          (external)   +------------------------+
           ORS API      |  FuelStation (ORM)     |
                        |  data/*.csv import     |
                        +------------------------+
```

- **Routing Client** (`fuelroute/services/routing_client.py`): the only
  module that talks to OpenRouteService (ORS). Wraps geocoding + directions
  HTTP calls and translates transport failures into typed exceptions
  (`LocationNotFound`, `RoutingUnavailable`, `RoutingRateLimited`).
- **Route Matching** (`fuelroute/services/route_matching.py`): given the
  route's GeoJSON geometry, does a coarse bounding-box prefilter against the
  `FuelStation` table (in-process, no extra external calls), then computes
  each candidate station's perpendicular distance to the route polyline and
  its cumulative distance-from-origin, keeping only stations within
  `ROUTE_CORRIDOR_BUFFER_MILES` (2.0 mi) of the route.
- **Fuel Stop Optimizer** (`fuelroute/services/fuel_optimizer.py`): pure
  function over a 1-D list of (station, distance) pairs — no ORM, no I/O —
  that returns the minimum-cost sequence of fuel purchases.
- **Data access**: a single `FuelStation` Django model
  (`fuelroute/models.py`), populated once via the `import_fuel_prices`
  management command. No repository layer, no caching layer, no async task
  queue — deliberately kept simple per the project's scope.

## Tech choices

| Choice | Why |
|---|---|
| Django 6.1 + DRF 3.18 | The assignment asks for the latest stable Django; 6.1.1 is the newest release as of this writing (`pyproject.toml` pins `django>=6.1,<6.2`), and DRF 3.18.1 officially supports it. DRF gives request validation (serializers) and clean JSON responses for a single-endpoint API without extra machinery. |
| SQLite | ~8,000 rows, read-mostly, no concurrent-write concerns — Postgres/PostGIS would be pure overhead for this dataset size. See "Future improvements" for when that changes. |
| `uv` | Fast, reproducible dependency management with a committed lockfile (`uv.lock`); no need to hand-manage a virtualenv. |
| OpenRouteService (ORS) | Free tier, no credit card required, returns geometry + distance + duration from a single directions call (see "Routing API choice" below). |
| pytest + pytest-django | More ergonomic fixtures/parametrization than `unittest`/Django's test runner; `responses` mocks ORS's HTTP calls in tests without hitting the network. |

## Setup

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
# then edit .env and fill in ORS_API_KEY and DJANGO_SECRET_KEY
```

### Environment variables

Set in `.env` (see `.env.example`). `config/settings.py` loads the
project-root `.env` via `python-dotenv` before reading any setting; a
variable already exported in your shell takes precedence over the `.env`
value.

| Variable | Required | Notes |
|---|---|---|
| `ORS_API_KEY` | Yes, to call ORS | Free key from https://openrouteservice.org/dev/#/signup. Sent as the raw `Authorization` header value (per ORS's convention, not a `Bearer` prefix). If it's missing or wrong, ORS rejects the call and the API answers `502 {"error": "Routing provider is unavailable"}`. |
| `DJANGO_SECRET_KEY` | Yes, for every command | No baked-in default, by design: if it's unset or empty, `config/settings.py` raises `ImproperlyConfigured` as soon as settings are loaded, so every `manage.py` command (`check`, `migrate`, `import_fuel_prices`, `runserver`, …) refuses to start. |
| `DEBUG` | No (default `True`) | Set to `False` for anything beyond local development. |

## Dataset setup

The raw dataset (`data/fuel-prices-for-be-assessment.csv`, ~8,150 rows) has
no coordinates and no street-level addresses (just highway-exit descriptors
like `"I-8, EXIT 119 & SR-85"`), so geocoding is done at **city+state**
granularity, not per-station.

That geocoding is a **one-time, offline** step, already run and committed as
`data/city_geocode_cache.csv` (columns: `city,state,latitude,longitude,confidence`).
`scripts/geocode_cities.py` is the standalone script that produced it (calls
a geocoding service once per unique city/state pair) — it is **not** part of
the running application and does not need to be re-run. The `confidence`
column is `"city"` when the city itself resolved, or `"state"` when the
script fell back to a state centroid because the city couldn't be resolved.

To load the dataset into the database:

```bash
uv run python manage.py migrate
uv run python manage.py import_fuel_prices
```

This command (`fuelroute/management/commands/import_fuel_prices.py`):

1. Reads the raw CSV, dropping exact full-row duplicates and any non-US
   state rows (Canadian province codes like `ON`, `BC`, `QC`, etc.).
2. Collapses rows sharing an OPIS Truckstop ID down to one canonical row:
   the **minimum retail price** among them, keeping the **first-seen name**.
3. Joins each canonical row to the geocode cache on
   `(city.strip().upper(), state.strip().upper())`, attaching
   `latitude`/`longitude`/`geocode_confidence` (or leaving them null on a
   cache miss).
4. Upserts into `FuelStation` by `opis_id` — safe to re-run.

Optional flags: `--csv-path` and `--geocode-cache` to point at alternate
files.

## Running the server

```bash
uv run python manage.py runserver
```

The API is then available at `http://localhost:8000/api/v1/route/`.

## Example request/response

```bash
curl -X POST http://localhost:8000/api/v1/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Atlanta, GA", "finish": "Washington, DC"}'
```

Where this response comes from: it was produced by running the real
`RouteFuelPlanningService` pipeline (real corridor matching, real optimizer)
against the real imported `FuelStation` table, with only the ORS directions
call swapped for a simplified stand-in route — a ~600-point polyline through
Atlanta → Charlotte → Raleigh → Richmond → Washington (distance = that
polyline's own length; duration assumes 60 mph). A live ORS road route
between the same cities is longer and follows different roads, so a live
call will return different stations and numbers. The only edit to the
output is that `route_geometry.coordinates` is cut down to its two
endpoints (a real response has one pair per polyline point, typically
thousands). Every other number is as computed, and they are consistent with
each other (worked through below).

```json
{
  "start": "Atlanta, GA",
  "finish": "Washington, DC",
  "distance_miles": 591.6,
  "duration_hours": 9.9,
  "route_geometry": {
    "type": "LineString",
    "coordinates": [[-84.388, 33.749], [-77.0369, 38.9072]]
  },
  "fuel_stops": [
    {
      "station_name": "SPEEDWAY #6973",
      "city": "Wake Forest",
      "state": "NC",
      "distance_from_origin_miles": 371.5,
      "price_per_gallon": 2.949,
      "gallons_purchased": 9.16,
      "cost": 27.01
    }
  ],
  "total_fuel_cost": 27.01,
  "total_gallons": 9.16
}
```

The arithmetic, under the fuel model (starts with a full 500-mile tank,
10 MPG, no reserve needed at the destination):

```text
Trip length 591.6 mi > 500 mi range, so at least (591.6 - 500) = 91.6 mi
of fuel must be bought en route. Here one stop covers all of it:

Stop at mile 371.5 (Wake Forest, NC; $2.949/gal):
  reachable on the starting tank:  371.5 <= 500
  fuel on arrival:                 500 - 371.5   = 128.5 mi
  still to drive:                  591.6 - 371.5 = 220.1 mi  (<= 500, so no second stop)
  buy the shortfall only:          220.1 - 128.5 = 91.6 mi = 91.6 / 10 = 9.16 gal
  cost:                            9.16 x 2.949  = 27.01284 -> $27.01 (to the cent, half-up)

total_gallons   = 9.16   (= (591.6 - 500) / 10)
total_fuel_cost = 27.01  (single stop)
```

Why only a top-up and not a full tank: nothing reachable further along the
route is priced below this stop's, and the destination is within range
after the top-up, so the optimizer buys exactly the shortfall (step 4 in
"Algorithm, in plain language" below).

Coordinates are also accepted directly (skips the geocoding call), and take
precedence if both shapes are supplied in the same request:

```json
{"start_lat": 33.749, "start_lng": -84.388, "finish_lat": 38.9072, "finish_lng": -77.0369}
```

`start`/`finish` in the response echo back whatever the request supplied —
the free-text string, or `"lat,lng"` rounded to 4 decimals if coordinates
were given.

### Error responses

| Case | Status | Body |
|---|---|---|
| Serializer validation failure (missing/invalid fields) | 400 | DRF default `{"field": ["msg"]}` |
| Free-text location not resolvable | 400 | `{"error": "Could not resolve location: <value>"}` |
| Routing API failure/timeout, or a malformed ORS response | 502 | `{"error": "Routing provider is unavailable"}` |
| Routing API rate-limited (ORS HTTP 429) | 503 | `{"error": "Routing provider rate limit exceeded"}` |
| No feasible fuel stop plan (a gap between stations exceeds the vehicle's range) | 422 | `{"error": "No feasible fuel stop plan: gap of <miles> miles near mile <x> exceeds vehicle range"}` |
| Anything unexpected | 500 | `{"error": "Internal server error"}` (traceback goes to server logs only, never the response body) |

## Running tests

```bash
uv run pytest
```

89 tests currently pass, covering settings loading, the model, the import
command, the routing client (via `responses`-mocked HTTP, including
malformed 200 responses), route corridor matching (including exact
equivalence with a brute-force scan and a dense-polyline speed guard), the
fuel optimizer (including the four hand-worked counterexamples that pinned
down the algorithm's edge cases), the API view, and end-to-end error
handling.

One quirk worth knowing: `pyproject.toml` points `pytest`'s
`DJANGO_SETTINGS_MODULE` at `config.settings_test`, a thin wrapper around
`config.settings` that just seeds a safe dummy `DJANGO_SECRET_KEY` before
Django's settings module is imported. This exists only so the suite passes
in a clean shell with no `.env` present — `config/settings.py` itself still
has no fallback secret key and raises `ImproperlyConfigured` at settings
load if one isn't set outside of tests. `config/settings_test.py` has a longer comment explaining exactly why
a `conftest.py` can't achieve the same thing (import-order reasons specific
to pytest-django).

## Algorithm, in plain language

The optimizer isn't "always refuel at the cheapest station you can see" —
that can strand you paying for fuel you didn't need, or make you pass a
cheap station only to be forced into a much more expensive one later. It's a
greedy strategy with an exchange-argument justification, tracking exactly
how much fuel is in the tank at all times (not just resetting to "500 miles
of visibility" at every stop):

1. From the current position, look at every station reachable on a full
   tank (500 miles) ahead.
2. If any of them is strictly cheaper than the price you last paid: drive to
   the *first* (nearest) such station, buying only the fuel needed to get
   there (topping up from whatever's already in the tank, never more) —
   because paying to fill up further here only means paying today's price
   for miles you could buy at tomorrow's cheaper price.
3. If none is cheaper, but you can already coast to the destination on the
   fuel you're carrying: stop, no purchase needed.
4. If none is cheaper, but the destination is reachable after topping up
   (not on a full tank, just enough range): buy exactly the shortfall and
   finish.
5. If nothing is cheaper and the destination isn't reachable either: fill
   the tank all the way (every mile bought here is at least as good as any
   later option, since nothing closer or cheaper exists) and drive to the
   cheapest station within reach, then repeat from step 1.
6. If step 1 finds literally no stations in range and the destination isn't
   reachable either, the trip is infeasible (a gap wider than 500 miles with
   no station in it) — the API returns a 422 in that case.

See `fuelroute/services/fuel_optimizer.py` for the exact implementation;
the module docstring and the test suite work through the reasoning and four hand-verified
counterexamples in more depth. Money (gallons, cost) is always computed with
`decimal.Decimal`, rounded to the cent with `ROUND_HALF_UP` — floats are
never used for anything that gets compared or summed as currency.

## Assumptions (summary)

The short version:

- **Duplicate OPIS IDs** (678 of them) collapse to one row: the minimum
  retail price among duplicates, keeping the first-seen name.
- **Non-US rows** (620 rows, 9 Canadian province codes) are dropped at
  import — the assignment scopes this to the USA.
- **Geocoding is city+state-level, not street-level** — the source
  addresses are highway-exit descriptors, not resolvable to real street
  addresses, so city-level precision is the realistic ceiling here, computed
  once offline and cached rather than looked up live per station.
- **Vehicle starts full** (500-mile range) and needs **no reserve** at the
  destination.
- **A station counts as "on route"** if it's within 2.0 miles of the
  route's polyline (`ROUTE_CORRIDOR_BUFFER_MILES`).
- Constants: 500-mile range, 10 MPG, single Django app (`fuelroute`), SQLite,
  no repository/factory/caching/async layers.

## Routing API choice & call budget

**OpenRouteService (ORS)** was chosen because it's free (no credit card),
and its `/v2/directions/driving-car/geojson` endpoint returns geometry,
distance, and duration in a single response — meeting the assignment's
"ideally 1 external API call" target directly.

Per request, the routing client makes:
- **0 or up to 2 geocoding calls** — only when the request supplies
  free-text `start`/`finish` instead of coordinates (one call per endpoint
  that needs resolving; supplying coordinates skips this entirely).
- **Exactly 1 directions call** — always, regardless of how many candidate
  fuel stations exist along the route.

Fuel station matching and optimization run entirely against the local
`FuelStation` table (SQLite) and in-process Python — never a per-station
external call, satisfying the assignment's "never per-station" constraint
by construction.

## Performance notes

- Route corridor matching does a coarse bounding-box prefilter at the ORM
  level (indexed on `latitude`/`longitude`), which for a long route still
  leaves 1,000-2,500 candidate stations. Real ORS polylines have thousands
  of points, so testing every candidate against every segment would be tens
  of millions of haversine evaluations (measured: ~54 s for a 10,000-point
  Chicago→Houston-style polyline against the real dataset). Instead, route
  segments are bucketed into a ~0.05° spatial grid once per request, and
  each station is only tested against the segments registered in its own
  grid cell, after a cheap bounding-box rejection. Both filters are
  provably conservative (they only skip segments farther than the 2-mile
  buffer), so results are identical to the exhaustive scan — the test suite
  checks this against a brute-force oracle — and the same case now takes
  ~0.15 s. A regression test pins a 10,000-point / 1,600-station case under
  2 s.
- The whole per-request pipeline (routing call aside) is in-memory list/dict
  work — no N+1 query patterns, no per-station database round-trips.
- The dominant cost per request is the single ORS directions HTTP call
  (network-bound, not computation-bound).

## Known limitations

- **City-level geocoding precision**: station coordinates are a city/state
  centroid, not the station's real location, so `distance_from_origin_miles`
  and the 2.0-mile corridor buffer are approximate — acceptable given the
  source data has no street-level addresses to begin with.
- **Duplicate-ID min-price rule**: when the same OPIS ID appears with
  different prices in the raw CSV (with no timestamp to say which is
  current), the import keeps the lowest price. This is an assumption, not a
  verified fact about which price is actually current.
- **Non-US rows excluded**: Canadian stations (620 rows) are dropped
  entirely at import and can never appear in a route's fuel stops, even for
  a route that plausibly crosses the border.
- **No caching**: every request re-runs corridor matching and optimization
  from scratch, and re-calls ORS for routing/geocoding — there's no
  memoization of repeated start/finish pairs.

## Future improvements

- **PostGIS**: swapping SQLite for PostgreSQL + PostGIS would let corridor
  matching (bounding box + point-to-segment distance) run as spatial SQL
  queries instead of Python loops over ORM-fetched rows — a meaningful win
  once the station count or route length grows well past this dataset's
  scale.
- **Caching**: memoizing geocoding results (most free-text queries repeat
  city names) and/or caching full route responses for identical start/finish
  pairs would cut both ORS calls and recomputation for repeat traffic.
- **Street-level geocoding**: if a richer address dataset became available,
  station-level (not city-level) coordinates would tighten the corridor
  match and stop-distance accuracy.
