from django.db import migrations


class Migration(migrations.Migration):
    """Retained as a no-op so the migration graph stays valid for databases that applied it.

    This revision used to rewrite the text seeded by 0011 into the generic initial message.
    0011 now seeds the generic text directly, so there is nothing left to rewrite.
    """

    dependencies = [("configuration", "0017_style_only_automatic_reply_prompt")]

    operations: list[migrations.operations.base.Operation] = []
