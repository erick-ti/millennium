from rest_framework.routers import DefaultRouter

from apps.decks.views import DeckMembershipViewSet, DeckViewSet

app_name = "decks"

router = DefaultRouter()
# Explicit basename: both viewsets define get_queryset (not a `queryset` attribute), so
# the router can't infer one (the alerts/movers precedent).
router.register("decks", DeckViewSet, basename="deck")
router.register("memberships", DeckMembershipViewSet, basename="deckmembership")

urlpatterns = router.urls
