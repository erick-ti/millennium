from rest_framework.routers import DefaultRouter

from apps.pricing.views import PriceSnapshotViewSet

app_name = "pricing"

router = DefaultRouter()
router.register("snapshots", PriceSnapshotViewSet, basename="pricesnapshot")

urlpatterns = router.urls
