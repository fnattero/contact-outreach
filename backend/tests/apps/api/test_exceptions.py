from __future__ import annotations

import logging

import pytest
from django.test import RequestFactory

from apps.api.exceptions import api_exception_handler

SECRET = "sk-live-super-secret-value-1234567890"


def _boom() -> None:
    raise RuntimeError(f"upstream rejected key {SECRET}")


def test_an_unexpected_error_is_logged_without_leaking_it_to_the_client(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = RequestFactory().get("/api/v1/contacts/")
    request.correlation_id = "11111111-1111-1111-1111-111111111111"  # type: ignore[attr-defined]
    try:
        _boom()
    except RuntimeError as exc:
        with caplog.at_level(logging.ERROR):
            response = api_exception_handler(exc, {"request": request})

    assert response is not None
    assert response.status_code == 500
    body = str(response.data)
    assert SECRET not in body
    assert "RuntimeError" not in body
    assert response.data["code"] == "internal_error"
    assert response.data["correlation_id"] == "11111111-1111-1111-1111-111111111111"

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "api.unhandled_exception"
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "RuntimeError" in message
    assert "test_exceptions.py" in message
    assert SECRET not in message
    assert records[0].error_code == "internal_error"  # type: ignore[attr-defined]
    assert records[0].path == "/api/v1/contacts/"  # type: ignore[attr-defined]
