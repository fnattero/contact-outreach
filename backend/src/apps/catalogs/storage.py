from __future__ import annotations

from typing import Any, cast

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible
from django.utils.functional import cached_property
from storages.backends.s3 import S3Storage


@deconstructible
class PrivateCatalogStorage(Storage):
    """Stable model-facing storage that selects filesystem or private S3 by environment."""

    @cached_property
    def wrapped(self) -> Storage:
        if settings.PRIVATE_STORAGE_BACKEND == "filesystem":
            return FileSystemStorage(location=settings.PRIVATE_STORAGE_ROOT, base_url=None)
        if settings.PRIVATE_STORAGE_BACKEND == "s3":
            return cast(
                Storage,
                S3Storage(
                    access_key=settings.S3_ACCESS_KEY_ID,
                    secret_key=settings.S3_SECRET_ACCESS_KEY,
                    bucket_name=settings.S3_BUCKET_NAME,
                    endpoint_url=settings.S3_ENDPOINT_URL or None,
                    region_name=settings.S3_REGION_NAME or None,
                    default_acl=None,
                    file_overwrite=False,
                    querystring_auth=True,
                    addressing_style=settings.S3_ADDRESSING_STYLE,
                ),
            )
        raise RuntimeError("Unsupported private storage backend")

    @property
    def is_local(self) -> bool:
        return settings.PRIVATE_STORAGE_BACKEND == "filesystem"

    @property
    def location(self) -> str:
        wrapped_location = getattr(self.wrapped, "location", None)
        if wrapped_location is None:
            raise NotImplementedError("Object storage has no local filesystem location")
        return str(wrapped_location)

    def _open(self, name: str, mode: str = "rb") -> File[Any]:
        return self.wrapped.open(name, mode)

    def _save(self, name: str, content: File[Any]) -> str:
        return self.wrapped.save(name, content)

    def delete(self, name: str) -> None:
        self.wrapped.delete(name)

    def exists(self, name: str) -> bool:
        return self.wrapped.exists(name)

    def size(self, name: str) -> int:
        return self.wrapped.size(name)

    def path(self, name: str) -> str:
        return self.wrapped.path(name)

    def get_available_name(self, name: str, max_length: int | None = None) -> str:
        return self.wrapped.get_available_name(name, max_length=max_length)

    def get_modified_time(self, name: str) -> Any:
        return self.wrapped.get_modified_time(name)


private_catalog_storage = PrivateCatalogStorage()
