from rest_framework.routers import DefaultRouter

from apps.alerts.views import AlertEventViewSet, AlertRuleViewSet

app_name = "alerts"

router = DefaultRouter()
# Explicit basename: both viewsets define get_queryset (not a `queryset` attribute) so the
# router can't infer one (the MoversViewSet precedent).
router.register("events", AlertEventViewSet, basename="alertevent")
router.register("rules", AlertRuleViewSet, basename="alertrule")

urlpatterns = router.urls
