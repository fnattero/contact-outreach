from __future__ import annotations

import importlib
import math
from collections.abc import Iterable, Iterator, Mapping
from typing import Protocol, cast

from django.core.exceptions import ValidationError

from apps.overture.releases import validate_release_id

BBox = tuple[float, float, float, float]
REQUIRED_PLACE_FIELDS = frozenset(
    {"id", "geometry", "names", "basic_category", "taxonomy", "sources"}
)


class OvertureReaderError(RuntimeError):
    pass


class OverturePlaceReader(Protocol):
    def iter_places(self, *, release_id: str, bbox: BBox) -> Iterator[Mapping[str, object]]: ...


class BatchReaderFactory(Protocol):
    def __call__(
        self,
        overture_type: str,
        *,
        bbox: BBox,
        release: str,
        connect_timeout: int,
        request_timeout: int,
        stac: bool,
    ) -> Iterable[object] | None: ...


def validate_bbox(value: object) -> BBox:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise ValidationError("El bbox Overture debe tener cuatro coordenadas.")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValidationError("El bbox Overture debe contener números.")
    bbox = tuple(float(item) for item in value)
    west, south, east, north = bbox
    if not all(math.isfinite(item) for item in bbox):
        raise ValidationError("El bbox Overture debe contener números finitos.")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValidationError("El bbox Overture está fuera de rango.")
    return bbox  # type: ignore[return-value]


class OfficialOverturePlaceReader:
    def __init__(
        self,
        *,
        batch_reader_factory: BatchReaderFactory | None = None,
        connect_timeout: int = 20,
        request_timeout: int = 120,
    ) -> None:
        if connect_timeout <= 0 or request_timeout <= 0:
            raise ValidationError("Los timeouts Overture deben ser positivos.")
        self._factory = batch_reader_factory
        self._connect_timeout = connect_timeout
        self._request_timeout = request_timeout

    def _record_batch_reader(self) -> BatchReaderFactory:
        if self._factory is not None:
            return self._factory
        try:
            module = importlib.import_module("overturemaps")
            factory = module.__dict__.get("record_batch_reader")
            if not callable(factory):
                raise AttributeError
        except (ImportError, AttributeError) as exc:
            raise OvertureReaderError(
                "El cliente oficial overturemaps no está instalado correctamente."
            ) from exc
        return cast(BatchReaderFactory, factory)

    def iter_places(self, *, release_id: str, bbox: BBox) -> Iterator[Mapping[str, object]]:
        release = validate_release_id(release_id)
        validated_bbox = validate_bbox(bbox)
        reader = self._record_batch_reader()(
            "place",
            bbox=validated_bbox,
            release=release,
            connect_timeout=self._connect_timeout,
            request_timeout=self._request_timeout,
            stac=True,
        )
        if reader is None:
            raise OvertureReaderError("El cliente oficial Overture no pudo abrir el dataset.")
        self._validate_arrow_schema(reader)
        for batch in reader:
            if isinstance(batch, Mapping):
                yield batch
                continue
            self._validate_arrow_schema(batch)
            to_pylist = getattr(batch, "to_pylist", None)
            if not callable(to_pylist):
                raise OvertureReaderError("El cliente Overture devolvió un batch inválido.")
            rows = to_pylist()
            if not isinstance(rows, list):
                raise OvertureReaderError("El cliente Overture devolvió filas inválidas.")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise OvertureReaderError("El cliente Overture devolvió una fila inválida.")
                if not all(isinstance(key, str) for key in row):
                    raise OvertureReaderError("El cliente Overture devolvió claves inválidas.")
                yield row

    @staticmethod
    def _validate_arrow_schema(value: object) -> None:
        """Fail before row parsing when Arrow exposes an incompatible Places schema."""

        schema = getattr(value, "schema", None)
        raw_names = getattr(schema, "names", None)
        if raw_names is None:
            return
        try:
            names = {str(name) for name in raw_names}
        except TypeError as exc:
            raise OvertureReaderError("El cliente Overture devolvió un esquema inválido.") from exc
        missing = REQUIRED_PLACE_FIELDS.difference(names)
        if missing:
            raise OvertureReaderError(
                "El release Overture no contiene taxonomy y basic_category compatibles."
            )
