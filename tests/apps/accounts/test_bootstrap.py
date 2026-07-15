from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth.hashers import identify_hasher
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command


@pytest.mark.django_db
def test_bootstrap_creates_rotates_and_does_not_print_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OWNER_USERNAME", "owner")
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.invalid")
    monkeypatch.setenv("OWNER_PASSWORD", "first-strong-password")
    output = StringIO()
    call_command("bootstrap_owner", stdout=output)

    user = User.objects.get(username="owner")
    assert user.check_password("first-strong-password")
    assert identify_hasher(user.password).algorithm == "argon2"
    assert not user.is_staff
    assert "first-strong-password" not in output.getvalue()
    assert "created" in output.getvalue()

    output = StringIO()
    call_command("bootstrap_owner", stdout=output)
    assert "unchanged" in output.getvalue()

    monkeypatch.setenv("OWNER_PASSWORD", "second-strong-password")
    output = StringIO()
    call_command("bootstrap_owner", stdout=output)
    user.refresh_from_db()
    assert user.check_password("second-strong-password")
    assert "updated" in output.getvalue()
    assert "second-strong-password" not in output.getvalue()


@pytest.mark.django_db
def test_bootstrap_if_configured_can_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OWNER_USERNAME", raising=False)
    monkeypatch.delenv("OWNER_PASSWORD", raising=False)
    output = StringIO()
    call_command("bootstrap_owner", if_configured=True, stdout=output)
    assert "skipped" in output.getvalue()
    assert not User.objects.exists()


@pytest.mark.django_db
def test_bootstrap_refuses_a_different_active_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    User.objects.create_user(username="existing", password="safe-password")
    monkeypatch.setenv("OWNER_USERNAME", "other")
    monkeypatch.setenv("OWNER_PASSWORD", "another-safe-password")
    with pytest.raises(CommandError, match="different username"):
        call_command("bootstrap_owner")
    assert User.objects.filter(is_active=True).count() == 1


@pytest.mark.django_db
def test_demo_data_is_explicit_and_development_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_DEMO_DATA", "true")
    with pytest.raises(CommandError, match="only when APP_ENV=development"):
        call_command("load_demo_data")

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_DEMO_DATA", "false")
    with pytest.raises(CommandError, match="explicitly"):
        call_command("load_demo_data")

    monkeypatch.setenv("ALLOW_DEMO_DATA", "true")
    monkeypatch.setenv("DEMO_OWNER_USERNAME", "demo")
    monkeypatch.setenv("DEMO_OWNER_PASSWORD", "demo-only-password")
    output = StringIO()
    call_command("load_demo_data", stdout=output)
    assert User.objects.get(username="demo").check_password("demo-only-password")
    assert "demo-only-password" not in output.getvalue()
