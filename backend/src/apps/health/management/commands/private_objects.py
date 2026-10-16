from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import tempfile
from collections.abc import Iterator
from typing import Any, BinaryIO, Protocol

from django.core.files import File
from django.core.files.storage import Storage
from django.core.management.base import BaseCommand, CommandError

from apps.catalogs.storage import private_catalog_storage

OBJECTS_PREFIX = "objects/"
MANIFEST_NAME = "manifest.tsv"
SPOOL_MEMORY_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


def _walk(storage: Storage, prefix: str = "") -> Iterator[str]:
    directories, files = storage.listdir(prefix)
    for name in sorted(files):
        yield f"{prefix}{name}"
    for name in sorted(directories):
        yield from _walk(storage, f"{prefix}{name}/")


def _validate_key(key: str) -> str:
    parts = key.split("/")
    if (
        not key
        or key.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or any(character in key for character in "\t\r\n\x00")
    ):
        raise CommandError("El Bucket contiene una clave que no se puede respaldar con seguridad.")
    return key


class _Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class _HashingReader:
    def __init__(self, source: _Readable) -> None:
        self._source = source
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self._source.read(size)
        self.digest.update(chunk)
        return chunk


def _manifest_bytes(entries: dict[str, tuple[str, int]]) -> bytes:
    lines = [f"{digest}\t{size}\t{key}\n" for key, (digest, size) in sorted(entries.items())]
    return "".join(lines).encode("utf-8")


def _parse_manifest(raw: bytes) -> dict[str, tuple[str, int]]:
    entries: dict[str, tuple[str, int]] = {}
    for line in raw.decode("utf-8").splitlines():
        digest, size, key = line.split("\t", 2)
        entries[key] = (digest, int(size))
    return entries


class Command(BaseCommand):
    help = (
        "Stream the private Bucket as a tar archive with a key/size/sha256 manifest (export), or "
        "replace the Bucket content from such an archive (import). Binary data goes through "
        "stdout/stdin, so nothing else may be written there."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("action", choices=("export", "import"))

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        storage = private_catalog_storage.wrapped
        if options["action"] == "export":
            self._export(storage, sys.stdout.buffer)
        else:
            self._import(storage, sys.stdin.buffer)

    def _export(self, storage: Storage, destination: BinaryIO) -> None:
        entries: dict[str, tuple[str, int]] = {}
        with tarfile.open(fileobj=destination, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            for key in _walk(storage):
                _validate_key(key)
                size = storage.size(key)
                info = tarfile.TarInfo(f"{OBJECTS_PREFIX}{key}")
                info.size = size
                info.mode = 0o600
                with storage.open(key, "rb") as stored:
                    reader = _HashingReader(stored)
                    archive.addfile(info, reader)
                entries[key] = (reader.digest.hexdigest(), size)
            manifest = _manifest_bytes(entries)
            info = tarfile.TarInfo(MANIFEST_NAME)
            info.size = len(manifest)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(manifest))
        self.stderr.write(f"Objetos exportados: {len(entries)}")

    def _import(self, storage: Storage, source: BinaryIO) -> None:
        # The archive is a stream, so the manifest at its end can only be checked after the
        # objects were written. The caller has already verified the archive checksum.
        for key in list(_walk(storage)):
            storage.delete(key)
        restored: dict[str, tuple[str, int]] = {}
        manifest: dict[str, tuple[str, int]] | None = None
        with tarfile.open(fileobj=source, mode="r|") as archive:
            for member in archive:
                handle = archive.extractfile(member) if member.isreg() else None
                if handle is None:
                    raise CommandError("El archivo de objetos contiene una entrada no válida.")
                if member.name == MANIFEST_NAME:
                    manifest = _parse_manifest(handle.read())
                    continue
                if not member.name.startswith(OBJECTS_PREFIX):
                    raise CommandError("El archivo de objetos contiene una entrada inesperada.")
                key = _validate_key(member.name[len(OBJECTS_PREFIX) :])
                digest = hashlib.sha256()
                with tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES) as spool:
                    while chunk := handle.read(CHUNK_BYTES):
                        digest.update(chunk)
                        spool.write(chunk)
                    spool.seek(0)
                    saved = storage.save(key, File(spool))
                if saved != key:
                    raise CommandError("El Bucket no conservó la clave de un objeto restaurado.")
                restored[key] = (digest.hexdigest(), member.size)
        if manifest is None:
            raise CommandError("El archivo de objetos no incluye el manifiesto.")
        if restored != manifest:
            missing = len(manifest.keys() - restored.keys())
            differing = sum(
                1 for key, value in restored.items() if key in manifest and manifest[key] != value
            )
            extra = len(restored.keys() - manifest.keys())
            raise CommandError(
                f"Los objetos restaurados no coinciden con el manifiesto: {missing} faltantes, "
                f"{differing} con hash o tamaño distinto, {extra} sobrantes."
            )
        self.stderr.write(f"Objetos restaurados y verificados: {len(restored)}")
