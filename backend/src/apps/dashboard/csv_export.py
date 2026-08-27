from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence

from django.http import HttpResponse

_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def spreadsheet_safe(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_DANGEROUS_PREFIXES) else text


def csv_download(
    *,
    filename: str,
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(spreadsheet_safe(value) for value in row)
    return response
