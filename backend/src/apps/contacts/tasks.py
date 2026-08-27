from __future__ import annotations

from celery import Task, shared_task
from django.conf import settings

from apps.contacts.models import EmailAddress
from apps.contacts.services import validate_contact_email_address
from apps.prospects.email_validation import DNSMXResolver, MockMXResolver


@shared_task(
    bind=True,
    name="contacts.validate_email",
    max_retries=3,
    default_retry_delay=300,
)  # type: ignore[untyped-decorator]
def validate_contact_email_task(self: Task, email_address_id: str) -> str:
    resolver = MockMXResolver() if settings.CONTACT_EMAIL_MX_RESOLVER == "mock" else DNSMXResolver()
    state = validate_contact_email_address(email_address_id, resolver=resolver)
    if state == EmailAddress.Validity.TRANSIENT:
        raise self.retry(exc=RuntimeError("La validación MX falló temporalmente."))
    return state
