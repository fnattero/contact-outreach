"""Enum-valued response fields must be declared as choices so the API schema lists their values."""

from __future__ import annotations

import pytest
from rest_framework import serializers

from apps.api.configuration import MessageTemplateSerializer, SearchZoneSerializer
from apps.api.contacts import (
    ContactEmailSerializer,
    ContactListSerializer,
    RestrictionSerializer,
)
from apps.configuration.models import SearchZone, WorkspaceMessageTemplateRevision
from apps.contacts.models import CommunicationRestriction, Contact, EmailAddress


@pytest.mark.parametrize(
    ("serializer", "field", "model_enum"),
    [
        (ContactEmailSerializer, "validity", EmailAddress.Validity),
        (ContactListSerializer, "status", Contact.Status),
        (RestrictionSerializer, "scope", CommunicationRestriction.Scope),
        (RestrictionSerializer, "kind", CommunicationRestriction.Kind),
        (SearchZoneSerializer, "level", SearchZone.Level),
        (MessageTemplateSerializer, "kind", WorkspaceMessageTemplateRevision.Kind),
    ],
)
def test_enum_valued_fields_expose_exactly_the_model_choices(
    serializer: type[serializers.Serializer], field: str, model_enum: type
) -> None:
    declared = serializer().fields[field]

    assert isinstance(declared, serializers.ChoiceField)
    assert set(declared.choices) == set(model_enum.values)  # type: ignore[attr-defined]
