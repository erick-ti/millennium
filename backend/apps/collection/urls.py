from rest_framework.routers import DefaultRouter

from apps.collection.views import CollectionItemViewSet, CollectionLotViewSet

app_name = "collection"

router = DefaultRouter()
router.register("items", CollectionItemViewSet, basename="collectionitem")
router.register("lots", CollectionLotViewSet, basename="collectionlot")

urlpatterns = router.urls
