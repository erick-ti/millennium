from django.urls import path

from apps.core.views import (
    CsrfView,
    DemoLoginView,
    HealthView,
    LoginView,
    LogoutView,
    MeView,
)

app_name = "core"

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("csrf/", CsrfView.as_view(), name="csrf"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/demo-login/", DemoLoginView.as_view(), name="auth-demo-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
]
