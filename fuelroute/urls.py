from django.urls import path

from fuelroute.views import RouteView

urlpatterns = [
    path("route/", RouteView.as_view(), name="route"),
]
