from __future__ import annotations

from typing import NoReturn, cast
from uuid import UUID

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import PermissionDenied as ApiPermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import Capability, has_capability
from apps.api.concurrency import add_etag, require_if_match
from apps.api.permissions import (
    ManageContactsPermission,
    ViewContactsPermission,
    authenticated_user,
)
from apps.contacts.models import CommunicationRestriction, Contact, EmailAddress
from apps.contacts.queries import contact_queryset, conversation_timelines
from apps.contacts.services import (
    add_contact_email_address,
    create_manual_contact,
    create_manual_restriction,
    queue_contact_email_validation,
    revoke_manual_restriction,
    set_contact_preferred_email,
)


class ContactCreateSerializer(serializers.Serializer[dict[str, object]]):
    email = serializers.EmailField()
    organization_name = serializers.CharField(max_length=300, required=False, allow_blank=True)
    contact_name = serializers.CharField(max_length=200, required=False, allow_blank=True)


class ContactPatchSerializer(serializers.Serializer[dict[str, object]]):
    name = serializers.CharField(max_length=200, required=False, allow_blank=True)


class ContactEmailCreateSerializer(serializers.Serializer[dict[str, object]]):
    email = serializers.EmailField()
    label = serializers.CharField(  # type: ignore[assignment]
        max_length=120, required=False, allow_blank=True
    )
    make_preferred = serializers.BooleanField(default=False)


class RestrictionCreateSerializer(serializers.Serializer[dict[str, object]]):
    scope = serializers.ChoiceField(choices=CommunicationRestriction.Scope.choices)
    reason = serializers.CharField(max_length=1000)
    email_address_id = serializers.UUIDField(required=False)


class RestrictionRevocationSerializer(serializers.Serializer[dict[str, object]]):
    reason = serializers.CharField(max_length=1000)


class ContactEmailSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    original_email = serializers.EmailField()
    label = serializers.CharField()  # type: ignore[assignment]
    is_preferred = serializers.BooleanField()
    validity = serializers.CharField()
    validated_at = serializers.DateTimeField(allow_null=True)
    invalid_reason = serializers.CharField()
    active_restriction_count = serializers.IntegerField()


class ContactListSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    organization_name = serializers.CharField()
    preferred_email = serializers.EmailField(allow_null=True)
    status = serializers.CharField()
    last_interaction_at = serializers.DateTimeField(allow_null=True)
    open_task_count = serializers.IntegerField()
    next_follow_up_at = serializers.DateTimeField(allow_null=True)


class RestrictionSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    scope = serializers.CharField()
    kind = serializers.CharField()
    evidence = serializers.CharField()
    revoked_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


def _workspace_contacts(actor: User) -> QuerySet[Contact]:
    return Contact.objects.filter(workspace_id=actor.membership.workspace_id).select_related(
        "organization", "preferred_email"
    )


def _contact(actor: User, contact_id: UUID) -> Contact:
    try:
        return _workspace_contacts(actor).get(pk=contact_id)
    except Contact.DoesNotExist as exc:
        raise NotFound from exc


def _contact_list_data(contact: Contact) -> dict[str, object]:
    preferred = contact.preferred_email
    return {
        "id": contact.pk,
        "name": contact.name,
        "organization_name": contact.organization.name,
        "preferred_email": preferred.original_email if preferred else None,
        "status": contact.status,
        "last_interaction_at": contact.last_interaction_at,
        "open_task_count": int(getattr(contact, "open_task_count", 0)),
        "next_follow_up_at": getattr(contact, "next_follow_up_at", None),
    }


def _validation_error(exc: ValidationError) -> serializers.ValidationError:
    if hasattr(exc, "message_dict"):
        return serializers.ValidationError(exc.message_dict)
    return serializers.ValidationError(str(exc))


def _permission_error(exc: PermissionDenied) -> ApiPermissionDenied:
    del exc
    return ApiPermissionDenied("El recurso no está disponible.")


def _raise_domain_error(exc: ValidationError | PermissionDenied) -> NoReturn:
    if isinstance(exc, ValidationError):
        raise _validation_error(exc) from exc
    raise _permission_error(exc) from exc


class ContactListView(APIView):
    permission_classes = (IsAuthenticated, ViewContactsPermission)

    def get(self, request: Request) -> Response:
        actor = authenticated_user(request)
        try:
            requested_page_size = int(request.query_params.get("page_size", 25))
        except (TypeError, ValueError) as exc:
            raise serializers.ValidationError({"page_size": "Indicá un número válido."}) from exc
        page_size = min(max(requested_page_size, 1), 100)
        page = Paginator(
            contact_queryset(request.query_params, workspace_id=actor.membership.workspace_id),
            page_size,
        ).get_page(request.query_params.get("page", 1))
        data = [
            ContactListSerializer(_contact_list_data(contact)).data for contact in page.object_list
        ]
        return Response(
            {
                "data": data,
                "meta": {
                    "page": page.number,
                    "page_size": page_size,
                    "total": page.paginator.count,
                },
            }
        )

    def post(self, request: Request) -> Response:
        # A separate class-level permission is not possible for GET and POST
        # on one APIView, so mutation is checked explicitly here as well.
        actor = authenticated_user(request)
        if not has_capability(actor, Capability.MANAGE_CONTACTS):
            raise ApiPermissionDenied
        serializer = ContactCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            contact = create_manual_contact(
                actor=actor,
                email=cast(str, serializer.validated_data["email"]),
                organization_name=cast(str, serializer.validated_data.get("organization_name", "")),
                contact_name=cast(str, serializer.validated_data.get("contact_name", "")),
            )
        except (ValidationError, PermissionDenied) as exc:
            _raise_domain_error(exc)
        return Response(
            {"data": ContactListSerializer(_contact_list_data(contact)).data},
            status=status.HTTP_201_CREATED,
        )


class ContactDetailView(APIView):
    permission_classes = (IsAuthenticated, ViewContactsPermission)

    def get(self, request: Request, contact_id: UUID) -> Response:
        contact = _contact(authenticated_user(request), contact_id)
        emails = list(
            EmailAddress.objects.filter(organization=contact.organization)
            .prefetch_related("restrictions")
            .order_by("-is_preferred", "provider_order", "created_at")
        )
        restrictions = CommunicationRestriction.objects.filter(
            workspace_id=contact.workspace_id,
        ).filter(contact=contact) | CommunicationRestriction.objects.filter(
            workspace_id=contact.workspace_id,
            email_address__organization=contact.organization,
        )
        email_data = []
        for email in emails:
            email_data.append(
                ContactEmailSerializer(
                    {
                        "id": email.pk,
                        "original_email": email.original_email,
                        "label": email.label,
                        "is_preferred": email.is_preferred,
                        "validity": email.validity,
                        "validated_at": email.validated_at,
                        "invalid_reason": email.invalid_reason,
                        "active_restriction_count": sum(
                            1 for restriction in email.restrictions.all() if restriction.is_active
                        ),
                    }
                ).data
            )
        restriction_data = [
            RestrictionSerializer(
                {
                    "id": item.pk,
                    "scope": item.scope,
                    "kind": item.kind,
                    "evidence": item.evidence,
                    "revoked_at": item.revoked_at,
                    "created_at": item.created_at,
                }
            ).data
            for item in restrictions.select_related("email_address").order_by("-created_at")
        ]
        timelines = conversation_timelines(
            contact,
            include_simulations=authenticated_user(request).membership.role == "ADMIN",
        )
        timeline_data = [
            {
                "subject": timeline.subject,
                "first_at": timeline.first_at,
                "last_at": timeline.last_at,
                "automation_label": timeline.automation_label,
                "open_task_count": timeline.open_task_count,
                "items": [
                    {
                        "direction": item.direction,
                        "happened_at": item.happened_at,
                        "sender": item.sender,
                        "recipient": item.recipient,
                        "subject": item.subject,
                        "body": item.body,
                        "outcome": item.outcome,
                        "simulated": item.simulated,
                        "needs_attention": item.needs_attention,
                    }
                    for item in timeline.items
                ],
            }
            for timeline in timelines
        ]
        return add_etag(
            Response(
                {
                    "data": {
                        **_contact_list_data(contact),
                        "organization_id": contact.organization_id,
                        "emails": email_data,
                        "restrictions": restriction_data,
                        "timelines": timeline_data,
                    }
                }
            ),
            contact,
        )

    def patch(self, request: Request, contact_id: UUID) -> Response:
        actor = authenticated_user(request)
        if not has_capability(actor, Capability.MANAGE_CONTACTS):
            raise ApiPermissionDenied
        serializer = ContactPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        contact = _contact(actor, contact_id)
        require_if_match(request, contact)
        if "name" in serializer.validated_data:
            contact.name = cast(str, serializer.validated_data["name"]).strip()
            contact.save(update_fields=("name", "updated_at"))
        contact.refresh_from_db()
        return add_etag(
            Response({"data": ContactListSerializer(_contact_list_data(contact)).data}), contact
        )


class ContactEmailCreateView(APIView):
    permission_classes = (IsAuthenticated, ManageContactsPermission)

    def post(self, request: Request, contact_id: UUID) -> Response:
        serializer = ContactEmailCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            email = add_contact_email_address(
                actor=authenticated_user(request),
                contact_id=contact_id,
                email=cast(str, serializer.validated_data["email"]),
                label=cast(str, serializer.validated_data.get("label", "")),
                make_preferred=cast(bool, serializer.validated_data.get("make_preferred", False)),
            )
        except (ValidationError, PermissionDenied) as exc:
            _raise_domain_error(exc)
        return Response(
            {
                "data": ContactEmailSerializer(
                    {
                        "id": email.pk,
                        "original_email": email.original_email,
                        "label": email.label,
                        "is_preferred": email.is_preferred,
                        "validity": email.validity,
                        "validated_at": email.validated_at,
                        "invalid_reason": email.invalid_reason,
                        "active_restriction_count": 0,
                    }
                ).data
            },
            status=status.HTTP_201_CREATED,
        )


class ContactEmailValidateView(APIView):
    permission_classes = (IsAuthenticated, ManageContactsPermission)

    def post(self, request: Request, contact_id: UUID, email_id: UUID) -> Response:
        actor = authenticated_user(request)
        contact = _contact(actor, contact_id)
        email = EmailAddress.objects.filter(
            pk=email_id,
            workspace_id=contact.workspace_id,
            organization_id=contact.organization_id,
        ).first()
        if email is None:
            raise NotFound
        try:
            queue_contact_email_validation(actor=actor, email_address_id=email.pk)
        except (ValidationError, PermissionDenied) as exc:
            _raise_domain_error(exc)
        return Response({"data": {"status": "queued"}}, status=status.HTTP_202_ACCEPTED)


class ContactPreferredEmailView(APIView):
    permission_classes = (IsAuthenticated, ManageContactsPermission)

    def patch(self, request: Request, contact_id: UUID, email_id: UUID) -> Response:
        try:
            contact = set_contact_preferred_email(
                actor=authenticated_user(request), contact_id=contact_id, email_address_id=email_id
            )
        except (ValidationError, PermissionDenied) as exc:
            _raise_domain_error(exc)
        return Response({"data": ContactListSerializer(_contact_list_data(contact)).data})


class ContactRestrictionView(APIView):
    permission_classes = (IsAuthenticated, ManageContactsPermission)

    def post(self, request: Request, contact_id: UUID) -> Response:
        serializer = RestrictionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        actor = authenticated_user(request)
        contact = _contact(actor, contact_id)
        scope = cast(str, serializer.validated_data["scope"])
        email_address_id = cast(UUID | None, serializer.validated_data.get("email_address_id"))
        if scope == CommunicationRestriction.Scope.EMAIL:
            if email_address_id is None:
                raise serializers.ValidationError(
                    {"email_address_id": "Elegí el email que querés restringir."}
                )
            email = EmailAddress.objects.filter(
                pk=email_address_id,
                workspace_id=contact.workspace_id,
                organization_id=contact.organization_id,
            ).first()
            if email is None:
                raise NotFound
            service_contact_id: UUID | None = None
        else:
            service_contact_id = contact.pk
        try:
            restriction = create_manual_restriction(
                actor=actor,
                scope=scope,
                reason=cast(str, serializer.validated_data["reason"]),
                contact_id=service_contact_id,
                email_address_id=email_address_id,
            )
        except (ValidationError, PermissionDenied) as exc:
            _raise_domain_error(exc)
        return Response(
            {
                "data": RestrictionSerializer(
                    {
                        "id": restriction.pk,
                        "scope": restriction.scope,
                        "kind": restriction.kind,
                        "evidence": restriction.evidence,
                        "revoked_at": restriction.revoked_at,
                        "created_at": restriction.created_at,
                    }
                ).data
            },
            status=status.HTTP_201_CREATED,
        )


class RestrictionRevokeView(APIView):
    permission_classes = (IsAuthenticated, ManageContactsPermission)

    def post(self, request: Request, contact_id: UUID, restriction_id: UUID) -> Response:
        actor = authenticated_user(request)
        contact = _contact(actor, contact_id)
        restriction_exists = (
            CommunicationRestriction.objects.filter(
                pk=restriction_id,
                workspace_id=contact.workspace_id,
            )
            .filter(Q(contact=contact) | Q(email_address__organization_id=contact.organization_id))
            .exists()
        )
        if not restriction_exists:
            raise NotFound
        serializer = RestrictionRevocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            restriction = revoke_manual_restriction(
                actor=actor,
                restriction_id=restriction_id,
                reason=cast(str, serializer.validated_data["reason"]),
            )
        except (ValidationError, PermissionDenied) as exc:
            _raise_domain_error(exc)
        return Response(
            {
                "data": RestrictionSerializer(
                    {
                        "id": restriction.pk,
                        "scope": restriction.scope,
                        "kind": restriction.kind,
                        "evidence": restriction.evidence,
                        "revoked_at": restriction.revoked_at,
                        "created_at": restriction.created_at,
                    }
                ).data
            }
        )
