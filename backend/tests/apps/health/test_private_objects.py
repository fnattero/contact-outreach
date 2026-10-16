from __future__ import annotations

import hashlib
import io
import sys
import tarfile
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def _seed(root: Path) -> dict[str, bytes]:
    objects = {
        "catalogs/a.pdf": b"%PDF-1.7 first",
        "catalogs/deeper/b.pdf": b"%PDF-1.7 second",
        "top-level.bin": b"\x00\x01\x02",
    }
    for key, data in objects.items():
        target = root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return objects


def _archive(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w|") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _manifest(objects: dict[str, bytes]) -> bytes:
    return "".join(
        f"{hashlib.sha256(data).hexdigest()}\t{len(data)}\t{key}\n"
        for key, data in sorted(objects.items())
    ).encode()


def _run_import(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
    call_command("private_objects", "import")


def test_export_streams_every_object_with_a_key_size_hash_manifest(
    private_catalog_dir: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    objects = _seed(private_catalog_dir)

    call_command("private_objects", "export")

    with tarfile.open(fileobj=io.BytesIO(capsysbinary.readouterr().out), mode="r:") as archive:
        names = archive.getnames()
        exported = {
            name.removeprefix("objects/"): archive.extractfile(name).read()  # type: ignore[union-attr]
            for name in names
            if name.startswith("objects/")
        }
        manifest = archive.extractfile("manifest.tsv").read()  # type: ignore[union-attr]
    assert names[-1] == "manifest.tsv"
    assert exported == objects
    assert manifest == _manifest(objects)


def test_export_of_an_empty_bucket_is_a_valid_archive_with_an_empty_manifest(
    private_catalog_dir: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    del private_catalog_dir

    call_command("private_objects", "export")

    with tarfile.open(fileobj=io.BytesIO(capsysbinary.readouterr().out), mode="r:") as archive:
        assert archive.getnames() == ["manifest.tsv"]


def test_import_replaces_the_bucket_with_the_archive_and_verifies_the_manifest(
    private_catalog_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backed_up = {"catalogs/a.pdf": b"from the backup", "nested/dir/b.pdf": b"also backed up"}
    (private_catalog_dir / "stale.pdf").write_bytes(b"created after the backup")
    (private_catalog_dir / "catalogs").mkdir()
    (private_catalog_dir / "catalogs" / "a.pdf").write_bytes(b"newer version")
    payload = _archive(
        {**{f"objects/{key}": data for key, data in backed_up.items()}}
        | {"manifest.tsv": _manifest(backed_up)}
    )

    _run_import(monkeypatch, payload)

    assert not (private_catalog_dir / "stale.pdf").exists()
    on_disk = {
        path.relative_to(private_catalog_dir).as_posix(): path.read_bytes()
        for path in private_catalog_dir.rglob("*")
        if path.is_file()
    }
    assert on_disk == backed_up


def test_import_fails_when_an_object_does_not_match_the_manifest(
    private_catalog_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del private_catalog_dir
    payload = _archive(
        {
            "objects/a.pdf": b"tampered",
            "manifest.tsv": _manifest({"a.pdf": b"original"}),
        }
    )

    with pytest.raises(CommandError, match="no coinciden con el manifiesto: 0 faltantes, 1 con"):
        _run_import(monkeypatch, payload)


def test_import_fails_when_the_archive_has_no_manifest(
    private_catalog_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del private_catalog_dir

    with pytest.raises(CommandError, match="no incluye el manifiesto"):
        _run_import(monkeypatch, _archive({"objects/a.pdf": b"data"}))


@pytest.mark.parametrize(
    "name",
    ["objects/../escape.pdf", "objects//double.pdf", "objects/a/./b.pdf", "other/a.pdf"],
)
def test_import_refuses_unsafe_or_unexpected_entries(
    private_catalog_dir: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    with pytest.raises(CommandError):
        _run_import(monkeypatch, _archive({name: b"data"}))

    assert not (private_catalog_dir.parent / "escape.pdf").exists()


def test_import_refuses_an_entry_that_is_not_a_regular_file(
    private_catalog_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del private_catalog_dir
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w|") as archive:
        link = tarfile.TarInfo("objects/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)

    with pytest.raises(CommandError, match="entrada no válida"):
        _run_import(monkeypatch, buffer.getvalue())


@pytest.mark.parametrize("key", ["with\ttab.pdf", "with\nnewline.pdf"])
def test_export_refuses_keys_that_would_corrupt_the_manifest(
    private_catalog_dir: Path, capsysbinary: pytest.CaptureFixture[bytes], key: str
) -> None:
    (private_catalog_dir / key).write_bytes(b"data")

    with pytest.raises(CommandError, match="clave que no se puede respaldar"):
        call_command("private_objects", "export")
    capsysbinary.readouterr()
