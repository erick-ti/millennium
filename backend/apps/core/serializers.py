from __future__ import annotations

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from rest_framework import serializers

from apps.core.permissions import is_demo_user


class UserSerializer(serializers.ModelSerializer[User]):
    """The current user, least-disclosure (Phase 5 auth slice).

    Only ``id``/``username``/``email`` plus the ``is_demo`` capability flag — never
    ``is_staff``/``is_superuser``/``last_login``/permissions. The schema is treated as
    recon material (Invariant 7 posture), so we expose the minimum the SPA needs.

    ``is_demo`` is that minimum for the read-only-demo feature: the SPA hides write
    affordances for the demo session, and it must learn that from the server rather than
    string-matching a hard-coded demo username (a hand-synced literal that silently
    drifts). It is a NON-sensitive session-capability flag, NOT a privilege flag — it
    says "this session is the read-only showcase", which the recruiter already knows."""

    is_demo = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "is_demo"]
        read_only_fields = ["id", "username", "email"]

    def get_is_demo(self, obj: User) -> bool:
        return is_demo_user(obj)


class LoginSerializer(serializers.Serializer[dict[str, object]]):
    """Validate credentials and resolve the user (Phase 5 auth slice).

    The credential check lives here (not the view) so a failure raises
    ``ValidationError`` → **400**, not 401: the ``LoginView`` runs with
    ``authentication_classes = []``, so DRF has no authenticator to mint a
    ``WWW-Authenticate`` header and would *downgrade* an ``AuthenticationFailed``
    401 to 403 — colliding with the "no session" 403 the SPA treats as "sign in".
    A single generic message for both wrong-username and wrong-password avoids
    username enumeration."""

    username = serializers.CharField(write_only=True)
    # trim_whitespace=False: never silently alter a legitimately space-padded
    # password before hashing.
    password = serializers.CharField(
        write_only=True, trim_whitespace=False, style={"input_type": "password"}
    )

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        request = self.context.get("request")
        user = authenticate(
            request=getattr(request, "_request", None),
            username=attrs["username"],
            password=attrs["password"],
        )
        if user is None or not user.is_active:
            raise serializers.ValidationError(
                "Unable to log in with the provided credentials.",
                code="authorization",
            )
        attrs["user"] = user
        return attrs
