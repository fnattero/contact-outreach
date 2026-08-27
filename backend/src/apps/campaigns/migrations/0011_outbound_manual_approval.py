from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


SUBJECT_PREFIX = "PUBLICIDAD - "
UNSUBSCRIBE_FOOTER = "\nSi no querés recibir más mensajes, respondé BAJA."


def prepare_existing_drafts(apps, schema_editor) -> None:
    del schema_editor
    OutboundMessage = apps.get_model("campaigns", "OutboundMessage")
    drafts = OutboundMessage.objects.filter(
        kind="FIRST_CONTACT",
        state__in=("PREPARED", "REVIEW_READY", "QUEUED"),
    )
    for message in drafts.iterator():
        changed = False
        if message.subject.startswith(SUBJECT_PREFIX):
            message.subject = message.subject[len(SUBJECT_PREFIX) :].lstrip()
            changed = True
        if message.body_text.endswith(UNSUBSCRIBE_FOOTER):
            message.body_text = message.body_text[: -len(UNSUBSCRIBE_FOOTER)].rstrip()
            changed = True
        update_fields = ["subject", "body_text"] if changed else []
        if message.delivery_mode == "LIVE" and message.state in {"PREPARED", "QUEUED"}:
            message.state = "REVIEW_READY"
            message.next_attempt_at = None
            update_fields.extend(("state", "next_attempt_at"))
        if changed:
            message.content_revision = 2
            update_fields.append("content_revision")
        if update_fields:
            message.save(update_fields=tuple(dict.fromkeys(update_fields)))


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("campaigns", "0010_overture_campaign_snapshots"),
    ]

    operations = [
        migrations.AddField(
            model_name="outboundmessage",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="approved_outbound_messages",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="content_revision",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="last_edited_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="last_edited_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="edited_outbound_messages",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(prepare_existing_drafts, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="outboundmessage",
            constraint=models.CheckConstraint(
                condition=(
                    Q(approved_at__isnull=True, approved_by__isnull=True)
                    | Q(approved_at__isnull=False, approved_by__isnull=False)
                ),
                name="message_approval_fields_consistent",
            ),
        ),
    ]
