from rest_framework.routers import DefaultRouter

from apps.imports.views import ImportBatchViewSet, ImportRowViewSet

app_name = "imports"

router = DefaultRouter()
router.register("batches", ImportBatchViewSet, basename="importbatch")
router.register("rows", ImportRowViewSet, basename="importrow")

urlpatterns = router.urls
