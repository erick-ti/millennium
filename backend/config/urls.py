from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

# schema/ and docs/ inherit DEFAULT_PERMISSION_CLASSES (IsAuthenticated). The
# OpenAPI schema is recon material for a private app — log into /admin/ first.
api_patterns: list[URLPattern | URLResolver] = [
    path("", include("apps.core.urls")),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(api_patterns)),
]
