from __future__ import annotations

from apps.integrations.contracts import WebsiteRequest
from apps.integrations.website import (
    MAX_EMAIL_CANDIDATES_PER_PAGE,
    HttpWebsiteFetcher,
    PinnedHTTPResponse,
)


class StaticResolver:
    def __init__(self, addresses: dict[str, tuple[str, ...]]) -> None:
        self.addresses = addresses
        self.timeouts: list[float] = []

    def resolve(self, hostname: str, *, timeout_seconds: float) -> tuple[str, ...]:
        self.timeouts.append(timeout_seconds)
        return self.addresses[hostname]


class StaticTransport:
    def __init__(self, responses: dict[str, PinnedHTTPResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def get(
        self,
        *,
        url: str,
        hostname: str,
        resolved_ip: str,
        port: int,
        timeout_seconds: float,
        max_bytes: int,
    ) -> PinnedHTTPResponse:
        del hostname, port, timeout_seconds, max_bytes
        self.calls.append((url, resolved_ip))
        return self.responses[url]


class SequenceTransport(StaticTransport):
    def __init__(self, responses: list[PinnedHTTPResponse]) -> None:
        super().__init__({})
        self.sequence = responses

    def get(
        self,
        *,
        url: str,
        hostname: str,
        resolved_ip: str,
        port: int,
        timeout_seconds: float,
        max_bytes: int,
    ) -> PinnedHTTPResponse:
        del hostname, port, timeout_seconds, max_bytes
        self.calls.append((url, resolved_ip))
        return self.sequence.pop(0)


def _html(body: str, *, status: int = 200) -> PinnedHTTPResponse:
    return PinnedHTTPResponse(
        status_code=status,
        headers={"content-type": "text/html; charset=utf-8"},
        body=body.encode(),
    )


def test_private_url_is_rejected_before_transport() -> None:
    transport = StaticTransport({})
    fetcher = HttpWebsiteFetcher(
        resolver=StaticResolver({"private.example": ("10.0.0.8",)}),
        transport=transport,
    )

    result = fetcher.fetch(WebsiteRequest(url="http://private.example/", correlation_id="ssrf"))

    assert result.pages == ()
    assert "no pública" in (result.error or "")
    assert transport.calls == []


def test_redirect_to_private_address_is_revalidated_and_rejected() -> None:
    transport = StaticTransport(
        {
            "https://public.example/": PinnedHTTPResponse(
                status_code=302,
                headers={"location": "http://private.example/admin"},
                body=b"",
            )
        }
    )
    fetcher = HttpWebsiteFetcher(
        resolver=StaticResolver(
            {
                "public.example": ("93.184.216.34",),
                "private.example": ("127.0.0.1",),
            }
        ),
        transport=transport,
    )

    result = fetcher.fetch(WebsiteRequest(url="https://public.example", correlation_id="redirect"))

    assert result.pages == ()
    assert len(transport.calls) == 1


def test_fetches_home_and_three_useful_pages_and_removes_active_noise() -> None:
    home = """
    <html><body><nav>Menú repetido</nav><script>ignore me</script>
    <main>Reparamos motores y bombas.</main>
    <a href="/servicios">Servicios</a><a href="/productos">Productos</a>
    <a href="/nosotros">Nosotros</a><a href="/contacto">Contacto</a>
    </body></html>
    """
    responses = {
        "https://public.example/": _html(home),
        "https://public.example/servicios": _html("<main>Bobinados y reparación.</main>"),
        "https://public.example/productos": _html("<main>Motores y repuestos.</main>"),
        "https://public.example/nosotros": _html("<main>Empresa familiar.</main>"),
        "https://public.example/contacto": _html("<main>Contacto.</main>"),
    }
    transport = StaticTransport(responses)
    fetcher = HttpWebsiteFetcher(
        resolver=StaticResolver({"public.example": ("93.184.216.34",)}),
        transport=transport,
    )

    result = fetcher.fetch(WebsiteRequest(url="https://public.example", correlation_id="bounded"))

    assert len(result.pages) == 4
    assert len(transport.calls) == 4
    assert "Reparamos motores" in result.pages[0].text
    assert "Menú repetido" not in result.pages[0].text
    assert "ignore me" not in result.pages[0].text
    assert all(page.byte_count <= 2 * 1024 * 1024 for page in result.pages)


def test_transient_server_error_gets_only_one_retry() -> None:
    transport = SequenceTransport(
        [
            PinnedHTTPResponse(
                status_code=503,
                headers={"content-type": "text/html"},
                body=b"unavailable",
            ),
            _html("<main>Reparación de motores.</main>"),
        ]
    )
    fetcher = HttpWebsiteFetcher(
        resolver=StaticResolver({"public.example": ("93.184.216.34",)}),
        transport=transport,
    )

    result = fetcher.fetch(WebsiteRequest(url="https://public.example", correlation_id="retry"))

    assert len(result.pages) == 1
    assert len(transport.calls) == 2


def test_dns_resolution_receives_remaining_total_budget() -> None:
    resolver = StaticResolver({"public.example": ("93.184.216.34",)})
    fetcher = HttpWebsiteFetcher(
        resolver=resolver,
        transport=StaticTransport(
            {"https://public.example/": _html("<main>Servicio técnico.</main>")}
        ),
    )

    result = fetcher.fetch(
        WebsiteRequest(
            url="https://public.example",
            correlation_id="dns-deadline",
            timeout_seconds=0.25,
        )
    )

    assert len(result.pages) == 1
    assert len(resolver.timeouts) == 1
    assert 0 < resolver.timeouts[0] <= 0.25


def test_public_suffix_boundary_blocks_unrelated_multilabel_domain() -> None:
    home = (
        '<main>Servicios.</main><a href="https://servicios.business.com.br/productos">'
        'Productos</a><a href="https://attacker.com.br/servicios">Servicios externos</a>'
    )
    transport = StaticTransport(
        {
            "https://business.com.br/": _html(home),
            "https://servicios.business.com.br/productos": _html(
                "<main>Reparación de motores.</main>"
            ),
        }
    )
    fetcher = HttpWebsiteFetcher(
        resolver=StaticResolver(
            {
                "business.com.br": ("93.184.216.34",),
                "servicios.business.com.br": ("93.184.216.35",),
            }
        ),
        transport=transport,
    )

    result = fetcher.fetch(WebsiteRequest(url="https://business.com.br", correlation_id="psl"))

    assert len(result.pages) == 2
    assert all("attacker.com.br" not in url for url, _ in transport.calls)


def test_extracts_bounded_visible_and_mailto_emails_with_page_provenance() -> None:
    body = """
    <main>
      Escribinos a ventas@public.example.
      <a href="mailto:contacto%40public.example?subject=Consulta">Contacto</a>
      ventas@public.example
    </main>
    <footer>
      <a href="mailto:soporte@public.example">Soporte</a>
      administracion@public.example
    </footer>
    <script>secret@attacker.example</script>
    """
    fetcher = HttpWebsiteFetcher(
        resolver=StaticResolver({"public.example": ("93.184.216.34",)}),
        transport=StaticTransport({"https://public.example/": _html(body)}),
    )

    result = fetcher.fetch(
        WebsiteRequest(url="https://public.example", correlation_id="email-provenance")
    )

    page = result.pages[0]
    assert [candidate.value for candidate in page.email_candidates] == [
        "contacto@public.example",
        "soporte@public.example",
        "ventas@public.example",
        "administracion@public.example",
    ]
    assert [candidate.source for candidate in page.email_candidates] == [
        "mailto",
        "mailto",
        "visible_text",
        "visible_text",
    ]
    assert all(candidate.page_url == page.final_url for candidate in page.email_candidates)
    assert all(
        candidate.page_content_hash == page.content_hash for candidate in page.email_candidates
    )
    assert "<main>" not in page.text
    assert "secret@attacker.example" not in page.text


def test_ignores_mailto_values_nested_inside_non_visible_markup() -> None:
    body = """
    <main><a href="mailto:visible@public.example">Contacto visible</a></main>
    <svg><a href="mailto:hidden-svg@attacker.example">hidden</a></svg>
    <noscript><a href="mailto:hidden-noscript@attacker.example">hidden</a></noscript>
    """
    fetcher = HttpWebsiteFetcher(
        resolver=StaticResolver({"public.example": ("93.184.216.34",)}),
        transport=StaticTransport({"https://public.example/": _html(body)}),
    )

    result = fetcher.fetch(
        WebsiteRequest(url="https://public.example", correlation_id="hidden-email")
    )

    assert [candidate.value for candidate in result.pages[0].email_candidates] == [
        "visible@public.example"
    ]


def test_visible_email_candidates_are_capped_per_page() -> None:
    body = " ".join(f"contacto{index}@public.example" for index in range(60))
    fetcher = HttpWebsiteFetcher(
        resolver=StaticResolver({"public.example": ("93.184.216.34",)}),
        transport=StaticTransport(
            {
                "https://public.example/": PinnedHTTPResponse(
                    status_code=200,
                    headers={"content-type": "text/plain; charset=utf-8"},
                    body=body.encode(),
                )
            }
        ),
    )

    result = fetcher.fetch(
        WebsiteRequest(url="https://public.example", correlation_id="email-bound")
    )

    assert len(result.pages[0].email_candidates) == MAX_EMAIL_CANDIDATES_PER_PAGE
