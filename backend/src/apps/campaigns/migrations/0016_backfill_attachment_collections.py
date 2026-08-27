from django.db import migrations


def backfill_attachment_collections(apps, schema_editor):
    Campaign = apps.get_model("campaigns", "Campaign")
    CampaignAttachment = apps.get_model("campaigns", "CampaignAttachment")
    OutboundMessage = apps.get_model("campaigns", "OutboundMessage")
    OutboundAttachment = apps.get_model("campaigns", "OutboundAttachment")

    CampaignAttachment.objects.bulk_create(
        [
            CampaignAttachment(campaign_id=campaign_id, catalog_id=catalog_id, position=0)
            for campaign_id, catalog_id in Campaign.objects.exclude(catalog_id=None).values_list(
                "pk", "catalog_id"
            )
        ],
        ignore_conflicts=True,
    )
    rows = []
    messages = OutboundMessage.objects.exclude(catalog_id=None).values_list(
        "pk",
        "catalog_id",
        "catalog_version",
        "catalog__file",
        "catalog__original_filename",
        "catalog__byte_size",
        "catalog__sha256",
    )
    for (
        message_id,
        catalog_id,
        catalog_version,
        storage_key,
        filename,
        byte_size,
        digest,
    ) in messages:
        rows.append(
            OutboundAttachment(
                message_id=message_id,
                catalog_id=catalog_id,
                position=0,
                catalog_version=catalog_version,
                storage_key=storage_key,
                filename=filename,
                byte_size=byte_size,
                sha256=digest,
            )
        )
    OutboundAttachment.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [("campaigns", "0015_campaignattachment_campaigndeliveryreservation_and_more")]

    operations = [
        migrations.RunPython(backfill_attachment_collections, migrations.RunPython.noop),
    ]
