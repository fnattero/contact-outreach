from __future__ import annotations

import ast
from pathlib import Path

from django.apps import apps
from django.db.models import Model

SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src"


def _lock_target_models(base: ast.expr) -> list[type[Model]]:
    """Resolve `Model.objects` or a related manager such as `campaign.messages`."""
    if not isinstance(base, ast.Attribute):
        return []
    if base.attr == "objects" and isinstance(base.value, ast.Name):
        return [model for model in apps.get_models() if model.__name__ == base.value.id]
    return [
        relation.related_model
        for model in apps.get_models()
        for relation in model._meta.related_objects
        if relation.get_accessor_name() == base.attr
    ]


def _crosses_outer_join(model: type[Model], path: str) -> bool:
    current: type[Model] | None = model
    for name in path.split("__"):
        assert current is not None
        field = current._meta.get_field(name)
        if not field.is_relation:
            return False
        if field.null or field.auto_created:
            return True
        current = field.related_model
    return False


def _isnull_paths(chain: dict[str, ast.Call]) -> list[str]:
    """Lookups such as `campaign_reminder__isnull=True` also join with LEFT OUTER JOIN."""
    paths = []
    for name in ("filter", "exclude", "get"):
        call = chain.get(name)
        for keyword in call.keywords if call else ():
            if (
                keyword.arg
                and keyword.arg.endswith("__isnull")
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
            ):
                paths.append(keyword.arg.removesuffix("__isnull"))
    return paths


def _unscoped_locks_on_outer_joins() -> tuple[int, list[str]]:
    seen: set[int] = set()
    offenders = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            chain: dict[str, ast.Call] = {}
            current: ast.expr = node  # type: ignore[assignment]
            while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
                chain.setdefault(current.func.attr, current)
                current = current.func.value
            lock = chain.get("select_for_update")
            if lock is None or id(lock) in seen:
                continue
            seen.add(id(lock))
            if any(keyword.arg == "of" for keyword in lock.keywords):
                continue
            related = chain.get("select_related")
            related_paths = [ast.literal_eval(item) for item in related.args] if related else []
            joined_paths = related_paths + _isnull_paths(chain)
            models = _lock_target_models(current)
            if (not models and related_paths) or any(
                _crosses_outer_join(model, item) for model in models for item in joined_paths
            ):
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{lock.lineno}")
    return len(seen), offenders


def test_row_locks_do_not_cover_the_nullable_side_of_outer_joins() -> None:
    # PostgreSQL rejects FOR UPDATE over a LEFT OUTER JOIN, which select_related and
    # `__isnull=True` emit for nullable or reverse relations. SQLite ignores row locks, so
    # only this guard catches it in tests; scope such locks with `of=("self",)`.
    scanned, offenders = _unscoped_locks_on_outer_joins()

    assert scanned > 0
    assert offenders == []
