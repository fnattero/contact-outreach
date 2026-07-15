from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateCatalogStorage(FileSystemStorage):
    def __init__(self) -> None:
        super().__init__(location=settings.PRIVATE_STORAGE_ROOT, base_url=None)


private_catalog_storage = PrivateCatalogStorage()
