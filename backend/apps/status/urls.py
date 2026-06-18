from django.urls import path

from apps.status.views import ChecksStatusView, InfraStatusView, StatusOverviewView

app_name = "status"

urlpatterns = [
    path("overview/", StatusOverviewView.as_view(), name="overview"),
    path("checks/", ChecksStatusView.as_view(), name="checks"),
    path("infra/", InfraStatusView.as_view(), name="infra"),
]
