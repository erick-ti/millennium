from django.urls import path

from apps.core.views import CsrfView, HealthView

app_name = "core"

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("csrf/", CsrfView.as_view(), name="csrf"),
]
