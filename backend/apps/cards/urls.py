from rest_framework.routers import DefaultRouter

from apps.cards.views import CardPrintingViewSet, CardViewSet

app_name = "cards"

router = DefaultRouter()
router.register("cards", CardViewSet, basename="card")
router.register("printings", CardPrintingViewSet, basename="cardprinting")

urlpatterns = router.urls
