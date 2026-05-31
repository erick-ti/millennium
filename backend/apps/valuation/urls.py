from rest_framework.routers import DefaultRouter

from apps.valuation.views import MoversViewSet

app_name = "valuation"

router = DefaultRouter()
# basename is explicit: MoversViewSet has no `queryset` attribute for the router to
# infer one from (its rows are a computed list, not a model queryset).
router.register("movers", MoversViewSet, basename="mover")

urlpatterns = router.urls
