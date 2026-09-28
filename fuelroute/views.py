import logging
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from fuelroute.serializers import RouteRequestSerializer
from fuelroute.services.fuel_optimizer import InfeasiblePlanError
from fuelroute.services.route_planning import RouteFuelPlanningService
from fuelroute.services.routing_client import (
    LocationNotFound,
    RoutingRateLimited,
    RoutingUnavailable,
)

logger = logging.getLogger(__name__)


class RouteView(APIView):
    """POST /api/v1/route/ — resolve a route and its optimal fuel stops."""

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = RouteFuelPlanningService.plan(serializer.validated_data)
        except LocationNotFound as exc:
            return Response(
                {"error": f"Could not resolve location: {exc}"}, status=400
            )
        except RoutingRateLimited:
            return Response(
                {"error": "Routing provider rate limit exceeded"}, status=503
            )
        except RoutingUnavailable:
            return Response(
                {"error": "Routing provider is unavailable"}, status=502
            )
        except InfeasiblePlanError as exc:
            return Response({"error": str(exc)}, status=422)
        except Exception:
            logger.exception("Unexpected error while planning route")
            return Response({"error": "Internal server error"}, status=500)

        return Response(result, status=200)
