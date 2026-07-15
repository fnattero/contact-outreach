from __future__ import annotations

from django.contrib.auth.views import LogoutView
from django.urls import path

from apps.accounts.views import ThrottledLoginView
from apps.dashboard.views import dashboard
from apps.health.views import liveness, readiness

urlpatterns = [
    path("login/", ThrottledLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("", dashboard, name="dashboard"),
    path("health/", liveness, name="health"),
    path("health/live/", liveness, name="health-live"),
    path("health/ready/", readiness, name="health-ready"),
]
