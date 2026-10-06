"""The frontend's Capability type must list exactly the capabilities the backend defines."""

from __future__ import annotations

import re
from pathlib import Path

from apps.accounts.permissions import VENDEDOR_CAPABILITIES, Capability

API_CLIENT = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "api.ts"
TEST_SESSION = Path(__file__).resolve().parents[2] / "frontend" / "tests" / "session.ts"


def _frontend_capabilities() -> set[str]:
    source = API_CLIENT.read_text()
    block = re.search(r"export type Capability =(.*?);", source, flags=re.S)
    assert block, "frontend/lib/api.ts must export `type Capability`"
    return set(re.findall(r'"([a-z_]+)"', block.group(1)))


def test_the_frontend_capability_type_matches_the_backend_enum() -> None:
    assert _frontend_capabilities() == {capability.value for capability in Capability}


def test_the_frontend_test_session_mirrors_the_backend_role_grants() -> None:
    source = TEST_SESSION.read_text()
    seller = re.search(r"SELLER_CAPABILITIES: Capability\[\] = \[(.*?)\]", source, flags=re.S)
    assert seller
    seller_names = set(re.findall(r'"([a-z_]+)"', seller.group(1)))
    assert seller_names == {capability.value for capability in VENDEDOR_CAPABILITIES}
    # An admin holds every capability: the seller ones (spread in) plus the listed extras.
    admin = re.search(r"ADMIN_CAPABILITIES: Capability\[\] = \[(.*?)\]", source, flags=re.S)
    assert admin
    admin_names = seller_names | set(re.findall(r'"([a-z_]+)"', admin.group(1)))
    assert admin_names == {capability.value for capability in Capability}
