from rest_framework.routers import DefaultRouter

from apps.portfolio.views import PortfolioValueSnapshotViewSet, PortfolioViewSet

app_name = "portfolio"

router = DefaultRouter()
router.register("portfolios", PortfolioViewSet, basename="portfolio")
router.register("snapshots", PortfolioValueSnapshotViewSet, basename="portfoliovaluesnapshot")

urlpatterns = router.urls
