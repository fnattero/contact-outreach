from __future__ import annotations

from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.permissions import Capability, workspace_for_user
from apps.api.permissions import (
    ManageAutomationPermission,
    ManageContactsPermission,
    authenticated_user,
)
from apps.api.schema import SchemaAPIView
from apps.automation.models import ContactCommunicationPlan, FollowUpTopic, ScheduledContactAttempt
from apps.automation.scheduled import (
    authorize_scheduled_contact_attempt,
    edit_scheduled_contact_draft,
    save_contact_communication_plan,
    save_follow_up_topic,
    set_contact_communication_plan_state,
    snooze_contact_communication_plan,
)
from apps.mailbox.tasks import deliver_message_task


class FollowUpTopicInputSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=160)
    objective = serializers.CharField(max_length=1000)
    instructions = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    cadence_days = serializers.IntegerField(min_value=7, max_value=365)
    mode = serializers.ChoiceField(choices=FollowUpTopic.Mode.choices)
    next_due_at = serializers.DateTimeField(required=False, allow_null=True)
    active = serializers.BooleanField(default=True)


class PlanInputSerializer(serializers.Serializer[dict[str, Any]]):
    preferred_email_id = serializers.UUIDField()
    purpose = serializers.ChoiceField(choices=ContactCommunicationPlan.Purpose.choices)
    goal_text = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    cadence_days = serializers.IntegerField(min_value=7, max_value=365)
    mode = serializers.ChoiceField(choices=FollowUpTopic.Mode.choices)
    enabled = serializers.BooleanField(default=True)
    next_due_at = serializers.DateTimeField(required=False, allow_null=True)


class PlanStateSerializer(serializers.Serializer[dict[str, Any]]):
    state = serializers.ChoiceField(choices=ContactCommunicationPlan.State.choices)


class SnoozeSerializer(serializers.Serializer[dict[str, Any]]):
    until = serializers.DateTimeField()


class DraftSerializer(serializers.Serializer[dict[str, Any]]):
    subject = serializers.CharField(max_length=255)
    body_text = serializers.CharField(max_length=10_000)


def _topic_data(topic: FollowUpTopic) -> dict[str, object]:
    return {
        "id": str(topic.pk),
        "name": topic.name,
        "objective": topic.objective,
        "instructions": topic.instructions,
        "cadence_days": topic.cadence_days,
        "mode": topic.mode,
        "next_due_at": topic.next_due_at.isoformat() if topic.next_due_at else None,
        "active": topic.active,
    }


def _plan_data(plan: ContactCommunicationPlan) -> dict[str, object]:
    return {
        "id": str(plan.pk),
        "contact_id": str(plan.contact_id),
        "topic_id": str(plan.topic_id),
        "topic_name": plan.topic.name,
        "preferred_email_id": str(plan.preferred_email_id),
        "state": plan.state,
        "state_label": plan.get_state_display(),
        "mode": plan.topic.mode,
        "next_due_at": plan.next_due_at.isoformat() if plan.next_due_at else None,
        "snoozed_until": plan.snoozed_until.isoformat() if plan.snoozed_until else None,
    }


def _attempt_data(attempt: ScheduledContactAttempt) -> dict[str, object]:
    outbound = attempt.outbound_message
    return {
        "id": str(attempt.pk),
        "plan_id": str(attempt.plan_id),
        "due_at": attempt.due_at.isoformat(),
        "state": attempt.state,
        "state_label": attempt.get_state_display(),
        "reason": attempt.reason,
        "outbound_message_id": str(outbound.pk) if outbound else None,
        "subject": outbound.subject if outbound else "",
        "body_text": outbound.body_text if outbound else "",
    }


class FollowUpTopicListView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageAutomationPermission)

    def get(self, request: Request) -> Response:
        workspace = workspace_for_user(authenticated_user(request), Capability.MANAGE_AUTOMATION)
        return Response(
            {
                "data": [
                    _topic_data(topic)
                    for topic in FollowUpTopic.objects.filter(workspace=workspace)
                ]
            }
        )

    def post(self, request: Request) -> Response:
        serializer = FollowUpTopicInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            topic = save_follow_up_topic(
                actor=authenticated_user(request), **serializer.validated_data
            )
        except (ValidationError, PermissionDenied) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": _topic_data(topic)}, status=status.HTTP_201_CREATED)


class FollowUpTopicDetailView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageAutomationPermission)

    def patch(self, request: Request, topic_id: UUID) -> Response:
        serializer = FollowUpTopicInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            topic = save_follow_up_topic(
                actor=authenticated_user(request), topic_id=topic_id, **serializer.validated_data
            )
        except (ValidationError, PermissionDenied, FollowUpTopic.DoesNotExist) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": _topic_data(topic)})


class ContactPlanView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageContactsPermission)

    def get(self, request: Request, contact_id: UUID) -> Response:
        actor = authenticated_user(request)
        workspace_for_user(actor, Capability.MANAGE_CONTACTS)
        plans = ContactCommunicationPlan.objects.filter(
            contact_id=contact_id, contact__workspace_id=actor.membership.workspace_id
        ).select_related("topic")
        return Response({"data": [_plan_data(plan) for plan in plans]})

    def post(self, request: Request, contact_id: UUID) -> Response:
        serializer = PlanInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            plan = save_contact_communication_plan(
                actor=authenticated_user(request),
                contact_id=contact_id,
                **serializer.validated_data,
            )
        except (ValidationError, PermissionDenied) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": _plan_data(plan)}, status=status.HTTP_201_CREATED)


class ContactPlanStateView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageContactsPermission)

    @extend_schema(operation_id="contact_communication_plan_action")
    def post(self, request: Request, contact_id: UUID, plan_id: UUID, action: str) -> Response:
        actor = authenticated_user(request)
        if not ContactCommunicationPlan.objects.filter(
            pk=plan_id, contact_id=contact_id, contact__workspace_id=actor.membership.workspace_id
        ).exists():
            raise NotFound
        if action == "snooze":
            serializer = SnoozeSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            function = snooze_contact_communication_plan
            kwargs = {
                "actor": actor,
                "plan_id": plan_id,
                "until": serializer.validated_data["until"],
            }
        else:
            serializer = PlanStateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            function = set_contact_communication_plan_state
            kwargs = {
                "actor": actor,
                "plan_id": plan_id,
                "state": serializer.validated_data["state"],
            }
        try:
            plan = function(**kwargs)
        except (ValidationError, PermissionDenied, ContactCommunicationPlan.DoesNotExist) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": _plan_data(plan)})


class ScheduledAttemptActionView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageContactsPermission)

    def patch(self, request: Request, attempt_id: UUID) -> Response:
        serializer = DraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            attempt = edit_scheduled_contact_draft(
                actor=authenticated_user(request),
                attempt_id=attempt_id,
                **serializer.validated_data,
            )
        except (ValidationError, PermissionDenied, ScheduledContactAttempt.DoesNotExist) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": _attempt_data(attempt)})

    def post(self, request: Request, attempt_id: UUID, action: str) -> Response:
        if action != "authorize":
            raise serializers.ValidationError({"action": "La acción no existe."})
        try:
            attempt = authorize_scheduled_contact_attempt(
                actor=authenticated_user(request), attempt_id=attempt_id
            )
        except (ValidationError, PermissionDenied, ScheduledContactAttempt.DoesNotExist) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        if attempt.outbound_message_id:
            from django.db import transaction

            transaction.on_commit(
                lambda: deliver_message_task.delay(str(attempt.outbound_message_id))
            )
        return Response({"data": _attempt_data(attempt)})
