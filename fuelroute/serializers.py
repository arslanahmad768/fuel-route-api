"""
Serializers for the fuel route optimization API.

`RouteRequestSerializer` validates the two accepted request shapes (free-text
`start`/`finish`, or explicit `start_lat`/`start_lng`/`finish_lat`/
`finish_lng` coordinates). The response is built as a plain dict by
`RouteFuelPlanningService.plan()`.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(required=False)
    finish = serializers.CharField(required=False)
    start_lat = serializers.FloatField(required=False)
    start_lng = serializers.FloatField(required=False)
    finish_lat = serializers.FloatField(required=False)
    finish_lng = serializers.FloatField(required=False)

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        has_text = bool(data.get("start")) and bool(data.get("finish"))
        has_coords = all(
            data.get(field) is not None
            for field in ("start_lat", "start_lng", "finish_lat", "finish_lng")
        )
        if not has_text and not has_coords:
            raise serializers.ValidationError(
                "Provide either 'start' and 'finish', or all of "
                "'start_lat', 'start_lng', 'finish_lat', 'finish_lng'."
            )
        return data

