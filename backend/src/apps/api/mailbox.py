from __future__ import annotations

import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import Capability, has_capability, workspace_for_user
from apps.api.permissions import (
    SendRepliesPermission,
    ViewContactsPermission,
    ViewSentMessagesPermission,
    authenticated_user,
)
from apps.campaigns.models import OutboundMessage
from apps.dashboard.queries import outbound_queryset, outbound_workspace_filter, response_queryset
from apps.integrations.contracts import ProviderError
from apps.mailbox.manual import authorize_manual_reply
from apps.mailbox.models import InboundMessage
from apps.mailbox.tasks import deliver_manual_reply_task


class MessageQuerySerializer(serializers.Serializer[dict[str, Any]]):
    q = serializers.CharField(required=False, allow_blank=True, max_length=150)
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    classification = serializers.ChoiceField(
        required=False,
        choices=InboundMessage.Classification.choices,
    )


class ManualReplySerializer(serializers.Serializer[dict[str, Any]]):
    body_text = serializers.CharField(max_length=10_000, trim_whitespace=True)
    idempotency_key = serializers.UUIDField()


def _page(
    queryset: QuerySet[Any], *, page: int, page_size: int
) -> tuple[list[Any], dict[str, int]]:
    total = queryset.count()
    start = (page - 1) * page_size
    return list(queryset[start : start + page_size]), {
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def _inbound_data(message: InboundMessage, *, include_body: bool = False) -> dict[str, object]:
    data: dict[str, object] = {
        "id": str(message.pk),
        "external_at": message.external_at.isoformat(),
        "sender": message.sender,
        "subject": message.subject,
        "classification": message.classification,
        "classification_label": message.get_classification_display(),
        "is_human": message.is_human,
        "is_read": message.is_read,
        "gmail_thread_id": message.gmail_thread_id,
        "campaign_id": (
            str(message.related_outbound.campaign_id)
            if message.related_outbound and message.related_outbound.campaign_id
            else None
        ),
    }
    if include_body:
        data["body_text"] = message.body_text
    else:
        data["body_preview"] = message.body_text[:280]
    return data


def _outbound_data(message: OutboundMessage, *, include_admin: bool) -> dict[str, object]:
    data: dict[str, object] = {
        "id": str(message.pk),
        "created_at": message.created_at.isoformat(),
        "recipient": message.recipient_normalized,
        "subject": message.subject,
        "kind": message.kind,
        "kind_label": message.get_kind_display(),
        "state": message.state,
        "state_label": message.get_state_display(),
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "simulated_at": message.simulated_at.isoformat() if message.simulated_at else None,
        "campaign_id": str(message.campaign_id) if message.campaign_id else None,
        "body_text": message.body_text,
    }
    if include_admin:
        data.update(
            {
                "approved_at": message.approved_at.isoformat() if message.approved_at else None,
                "error": message.error or None,
                "message_id": message.message_id or None,
                "gmail_thread_id": message.gmail_thread_id or None,
                "attachments": [
                    {
                        "filename": attachment.filename,
                        "position": attachment.position,
                        "byte_size": attachment.byte_size,
                    }
                    for attachment in message.attachments.order_by("position", "created_at")
                ],
            }
        )
    return data


class InboundMessageListView(APIView):
    permission_classes = (IsAuthenticated, ViewContactsPermission)

    def get(self, request: Request) -> Response:
        query = MessageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        workspace = workspace_for_user(authenticated_user(request), Capability.VIEW_CONTACTS)
        messages = response_queryset(request.query_params, workspace_id=workspace.pk)
        if values.get("classification"):
            messages = messages.filter(classification=values["classification"])
        rows, meta = _page(messages, page=values["page"], page_size=values["page_size"])
        return Response({"data": [_inbound_data(message) for message in rows], "meta": meta})


class InboundMessageThreadView(APIView):
    permission_classes = (IsAuthenticated, ViewContactsPermission)

    def get(self, request: Request, inbound_id: uuid.UUID) -> Response:
        workspace = workspace_for_user(authenticated_user(request), Capability.VIEW_CONTACTS)
        try:
            inbound = InboundMessage.objects.select_related(
                "connection",
                "related_outbound__campaign",
            ).get(pk=inbound_id, connection__workspace=workspace)
        except InboundMessage.DoesNotExist as exc:
            raise serializers.ValidationError({"inbound_id": "La respuesta no existe."}) from exc
        inbound_items = InboundMessage.objects.filter(
            connection=inbound.connection,
            gmail_thread_id=inbound.gmail_thread_id,
        ).order_by("external_at", "created_at")
        outbound_items = (
            OutboundMessage.objects.filter(
                outbound_workspace_filter(workspace.pk),
                gmail_thread_id=inbound.gmail_thread_id,
            )
            .distinct()
            .order_by("sent_at", "created_at")
        )
        timeline: list[dict[str, object]] = [
            {
                "direction": "inbound",
                "at": item.external_at.isoformat(),
                "sender": item.sender,
                "body_text": item.body_text,
                "classification": item.classification,
            }
            for item in inbound_items
        ]
        timeline.extend(
            {
                "direction": "outbound",
                "at": (item.sent_at or item.simulated_at or item.created_at).isoformat(),
                "sender": inbound.connection.email,
                "body_text": item.body_text,
                "classification": item.kind,
            }
            for item in outbound_items
        )
        timeline.sort(key=lambda item: str(item["at"]))
        return Response(
            {
                "data": {
                    "inbound": _inbound_data(inbound, include_body=True),
                    "timeline": timeline,
                }
            }
        )


class InboundManualReplyView(APIView):
    permission_classes = (IsAuthenticated, SendRepliesPermission)

    def post(self, request: Request, inbound_id: uuid.UUID) -> Response:
        serializer = ManualReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            message, created = authorize_manual_reply(
                actor=authenticated_user(request),
                inbound_id=inbound_id,
                body_text=str(serializer.validated_data["body_text"]),
                request_key=serializer.validated_data["idempotency_key"],
            )
        except (InboundMessage.DoesNotExist, ValidationError, ProviderError) as exc:
            raise serializers.ValidationError(str(exc)) from exc

        if created:
            transaction.on_commit(lambda: deliver_manual_reply_task.delay(str(message.pk)))

        return Response(
            {
                "data": {
                    "created": created,
                    "message": _outbound_data(message, include_admin=True),
                }
            },
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


class OutboundMessageListView(APIView):
    permission_classes = (IsAuthenticated, ViewSentMessagesPermission)

    def get(self, request: Request) -> Response:
        query = MessageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        user = authenticated_user(request)
        workspace = workspace_for_user(user, Capability.VIEW_SENT_MESSAGES)
        is_admin = has_capability(user, Capability.MANAGE_CAMPAIGNS)
        messages = (
            outbound_queryset(request.query_params)
            .filter(outbound_workspace_filter(workspace.pk))
            .distinct()
        )
        if not is_admin:
            messages = messages.filter(state=OutboundMessage.State.SENT)
        rows, meta = _page(messages, page=values["page"], page_size=values["page_size"])
        return Response(
            {
                "data": [_outbound_data(message, include_admin=is_admin) for message in rows],
                "meta": meta,
            }
        )


class OutboundMessageDetailView(APIView):
    permission_classes = (IsAuthenticated, ViewSentMessagesPermission)

    def get(self, request: Request, message_id: uuid.UUID) -> Response:
        user = authenticated_user(request)
        workspace = workspace_for_user(user, Capability.VIEW_SENT_MESSAGES)
        is_admin = has_capability(user, Capability.MANAGE_CAMPAIGNS)
        messages = OutboundMessage.objects.filter(
            outbound_workspace_filter(workspace.pk)
        ).distinct()
        if not is_admin:
            messages = messages.filter(state=OutboundMessage.State.SENT)
        try:
            message = messages.prefetch_related("attachments").get(pk=message_id)
        except OutboundMessage.DoesNotExist as exc:
            raise serializers.ValidationError({"message_id": "El mensaje no existe."}) from exc
        return Response({"data": _outbound_data(message, include_admin=is_admin)})
