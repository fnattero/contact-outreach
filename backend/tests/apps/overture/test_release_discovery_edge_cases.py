from __future__ import annotations

import json

import pytest
from django.core.exceptions import ValidationError

from apps.overture.releases import (
    MAX_CATALOG_BYTES,
    MAX_RELEASES,
    OFFICIAL_STAC_URL,
    ReleaseDiscoveryError,
    _NoRedirectHandler,
    _official_transport,
    _parse_catalog,
    _release_from_child_href,
    discover_release,
    discover_releases,
    validate_release_id,
)


def _catalog(
    *,
    child_href: str = "./2026-07-22.0/catalog.json",
    latest: str = "2026-07-22.0",
    extra_links: list[object] | None = None,
) -> bytes:
    links: list[object] = list(extra_links or [])
    links.append({"rel": "child", "href": child_href})
    return json.dumps({"latest": latest, "links": links}).encode()


def _release_catalog(
    *, release_id: str = "2026-07-22.0", schema_version: object = "v1.18.0"
) -> bytes:
    return json.dumps(
        {"type": "Catalog", "id": release_id, "schema:version": schema_version}
    ).encode()


def test_no_redirect_handler_swallows_the_redirect_and_returns_none() -> None:
    handler = _NoRedirectHandler()

    result = handler.redirect_request(
        object(), object(), 302, "Found", object(), "https://attacker.example/"
    )

    assert result is None


@pytest.mark.parametrize("value", [None, 123, ["2026-07-22.0"]])
def test_validate_release_id_rejects_non_string_values(value: object) -> None:
    with pytest.raises(ValidationError, match="identificador de release"):
        validate_release_id(value)


def test_validate_release_id_rejects_a_malformed_pattern() -> None:
    with pytest.raises(ValidationError, match="identificador de release"):
        validate_release_id("not-a-release-id")


class _Response:
    def __init__(
        self,
        *,
        geturl: str,
        status: int = 200,
        payload: object = b"{}",
    ) -> None:
        self._geturl = geturl
        self.status = status
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def geturl(self) -> str:
        return self._geturl

    def read(self, size: int) -> object:
        del size
        return self._payload


def _patched_opener(monkeypatch: pytest.MonkeyPatch, response: object) -> None:
    from types import SimpleNamespace

    from apps.overture import releases as releases_module

    opener = SimpleNamespace(open=lambda request, timeout: response)
    monkeypatch.setattr(releases_module.urllib.request, "build_opener", lambda handler: opener)


def test_official_transport_rejects_a_url_that_changed_after_redirect_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patched_opener(monkeypatch, _Response(geturl="https://stac.overturemaps.org/other.json"))

    with pytest.raises(ReleaseDiscoveryError, match="redirigir"):
        _official_transport(OFFICIAL_STAC_URL, timeout_seconds=2, max_bytes=64)


def test_official_transport_rejects_a_non_200_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _patched_opener(monkeypatch, _Response(geturl=OFFICIAL_STAC_URL, status=500))

    with pytest.raises(ReleaseDiscoveryError, match="respondió con un error"):
        _official_transport(OFFICIAL_STAC_URL, timeout_seconds=2, max_bytes=64)


def test_official_transport_rejects_non_bytes_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _patched_opener(monkeypatch, _Response(geturl=OFFICIAL_STAC_URL, payload="not-bytes"))

    with pytest.raises(ReleaseDiscoveryError, match="bytes inválidos"):
        _official_transport(OFFICIAL_STAC_URL, timeout_seconds=2, max_bytes=64)


def test_official_transport_rejects_a_payload_larger_than_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patched_opener(monkeypatch, _Response(geturl=OFFICIAL_STAC_URL, payload=b"x" * 100))

    with pytest.raises(ReleaseDiscoveryError, match="supera el tamaño"):
        _official_transport(OFFICIAL_STAC_URL, timeout_seconds=2, max_bytes=64)


def test_official_transport_wraps_urllib_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    from types import SimpleNamespace

    from apps.overture import releases as releases_module

    def raise_timeout(request: object, timeout: float) -> None:
        del request, timeout
        raise urllib.error.URLError("boom")

    opener = SimpleNamespace(open=raise_timeout)
    monkeypatch.setattr(releases_module.urllib.request, "build_opener", lambda handler: opener)

    with pytest.raises(ReleaseDiscoveryError, match="No se pudo consultar"):
        _official_transport(OFFICIAL_STAC_URL, timeout_seconds=2, max_bytes=64)


@pytest.mark.parametrize("href", [None, "", "   "])
def test_release_from_child_href_rejects_missing_values(href: object) -> None:
    with pytest.raises(ReleaseDiscoveryError, match="enlace child inválido"):
        _release_from_child_href(href)


def test_release_from_child_href_rejects_a_path_without_catalog_json() -> None:
    with pytest.raises(ReleaseDiscoveryError, match="enlace child inválido"):
        _release_from_child_href("./catalog.json")


def test_release_from_child_href_rejects_an_invalid_release_segment() -> None:
    with pytest.raises(ReleaseDiscoveryError, match="release inválido"):
        _release_from_child_href("./not-a-release/catalog.json")


def test_parse_catalog_rejects_a_payload_larger_than_the_limit() -> None:
    huge = b" " * (MAX_CATALOG_BYTES + 1)
    with pytest.raises(ReleaseDiscoveryError, match="supera el tamaño"):
        _parse_catalog(huge)


def test_parse_catalog_rejects_invalid_json() -> None:
    with pytest.raises(ReleaseDiscoveryError, match="JSON válido"):
        _parse_catalog(b"not json")


def test_parse_catalog_rejects_a_non_mapping_root() -> None:
    with pytest.raises(ReleaseDiscoveryError, match="estructura inválida"):
        _parse_catalog(b"[]")


def test_parse_catalog_rejects_missing_links() -> None:
    with pytest.raises(ReleaseDiscoveryError, match="enlaces válidos"):
        _parse_catalog(json.dumps({"latest": "2026-07-22.0"}).encode())


def test_parse_catalog_skips_non_child_links() -> None:
    catalog = _parse_catalog(_catalog(extra_links=[{"rel": "self", "href": "./catalog.json"}]))

    assert catalog.releases == ("2026-07-22.0",)


def test_parse_catalog_rejects_more_than_the_maximum_releases() -> None:
    links = [
        {"rel": "child", "href": f"./2020-01-{(index % 27) + 1:02d}.0/catalog.json"}
        for index in range(MAX_RELEASES + 5)
    ]
    payload = json.dumps({"latest": "2020-01-01.0", "links": links}).encode()

    with pytest.raises(ReleaseDiscoveryError, match="demasiados releases"):
        _parse_catalog(payload)


def test_parse_catalog_rejects_duplicate_releases() -> None:
    payload = _catalog(extra_links=[{"rel": "child", "href": "./2026-07-22.0/catalog.json"}])

    with pytest.raises(ReleaseDiscoveryError, match="releases inválidos"):
        _parse_catalog(payload)


def test_parse_catalog_rejects_a_missing_or_invalid_latest() -> None:
    payload = json.dumps(
        {"links": [{"rel": "child", "href": "./2026-07-22.0/catalog.json"}]}
    ).encode()

    with pytest.raises(ReleaseDiscoveryError, match="latest válido"):
        _parse_catalog(payload)


def test_parse_catalog_rejects_a_latest_not_present_among_releases() -> None:
    payload = _catalog(latest="2020-01-01.0")

    with pytest.raises(ReleaseDiscoveryError, match="no aparece entre los releases"):
        _parse_catalog(payload)


@pytest.mark.parametrize("timeout", [0, -1, 61])
def test_discover_releases_rejects_out_of_range_timeouts(timeout: float) -> None:
    with pytest.raises(ValidationError, match="timeout de Overture"):
        discover_releases(transport=lambda **kwargs: b"{}", timeout_seconds=timeout)


def test_discover_releases_rejects_a_non_bytes_transport_result() -> None:
    def transport(url: str, *, timeout_seconds: float, max_bytes: int) -> object:
        del url, timeout_seconds, max_bytes
        return "not-bytes"

    with pytest.raises(ReleaseDiscoveryError, match="respuesta inválida"):
        discover_releases(transport=transport)  # type: ignore[arg-type]


def test_discover_release_rejects_a_release_not_in_the_catalog() -> None:
    def transport(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        del timeout_seconds, max_bytes
        return _release_catalog() if url != OFFICIAL_STAC_URL else _catalog()

    with pytest.raises(ReleaseDiscoveryError, match="no está en el catálogo"):
        discover_release("2020-01-01.0", transport=transport)


def test_discover_release_rejects_an_oversized_release_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.overture import releases as releases_module

    monkeypatch.setattr(releases_module, "MAX_RELEASE_CATALOG_BYTES", 4)

    def transport(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        del timeout_seconds, max_bytes
        return _release_catalog() if url != OFFICIAL_STAC_URL else _catalog()

    with pytest.raises(ReleaseDiscoveryError, match="catálogo del release Overture es inválido"):
        discover_release(transport=transport)


def test_discover_release_rejects_invalid_json() -> None:
    def transport(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        del timeout_seconds, max_bytes
        return b"not json" if url != OFFICIAL_STAC_URL else _catalog()

    with pytest.raises(ReleaseDiscoveryError, match="no contiene JSON"):
        discover_release(transport=transport)


def test_discover_release_rejects_a_mismatched_release_document() -> None:
    def transport(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        del timeout_seconds, max_bytes
        if url == OFFICIAL_STAC_URL:
            return _catalog()
        return _release_catalog(release_id="2026-06-17.0")

    with pytest.raises(ReleaseDiscoveryError, match="no coincide con el release solicitado"):
        discover_release(transport=transport)


def test_discover_release_rejects_a_malformed_declared_schema_version() -> None:
    def transport(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        del timeout_seconds, max_bytes
        if url != OFFICIAL_STAC_URL:
            return _release_catalog(schema_version="not-a-version")
        return _catalog()

    with pytest.raises(ReleaseDiscoveryError, match="versión de esquema inválida"):
        discover_release(transport=transport)
