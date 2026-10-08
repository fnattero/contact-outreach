from __future__ import annotations

import importlib

import pytest

from apps.accounts.models import Workspace
from apps.configuration.models import SearchCategory, SearchCategoryRule

migration = importlib.import_module(
    "apps.configuration.migrations.0021_refresh_default_search_categories"
)


def _workspace() -> Workspace:
    return Workspace.objects.get(singleton_key=1)


def _old_seed(name: str, rules: tuple[tuple[str, tuple[str, ...]], ...]) -> SearchCategory:
    category = SearchCategory.objects.create(
        workspace=_workspace(), name=name, normalized_name=name.casefold(), active=True
    )
    for order, (code, terms) in enumerate(rules):
        SearchCategoryRule.objects.create(
            category=category, taxonomy_code=code, name_terms=list(terms), sort_order=order
        )
    return category


def _refresh() -> None:
    migration.refresh_default_categories(SearchCategory, SearchCategoryRule, Workspace)


@pytest.mark.django_db
def test_the_new_defaults_are_in_place_and_refreshing_twice_adds_nothing() -> None:
    names = set(
        SearchCategory.objects.filter(archived_at__isnull=True).values_list("name", flat=True)
    )
    assert names == {name for name, _ in migration.NEW_DEFAULTS}
    assert "Service de aspiradoras" not in names
    assert "Reparación de lavarropas" not in names

    _refresh()

    assert SearchCategory.objects.filter(archived_at__isnull=True).count() == len(
        migration.NEW_DEFAULTS
    )


@pytest.mark.django_db
def test_an_untouched_original_example_is_replaced_and_an_edited_one_is_kept() -> None:
    untouched = _old_seed(
        "Service de aspiradoras",
        (
            ("lifestyle_services", ("aspiradora*",)),
            ("", ("aspiradora*",)),
            ("", ("service de aspiradoras",)),
        ),
    )
    edited = _old_seed(
        "Reparación de lavarropas",
        (
            ("lifestyle_services", ("lavarropa*",)),
            ("", ("lavarropa*",)),
            ("", ("lavadora*",)),
            ("", ("mi propia variante",)),
        ),
    )
    own = _old_seed("Mi rubro propio", (("", ("algo",)),))

    _refresh()

    assert not SearchCategory.objects.filter(pk=untouched.pk).exists()
    assert SearchCategory.objects.filter(pk=edited.pk, archived_at__isnull=True).exists()
    assert SearchCategory.objects.filter(pk=own.pk, archived_at__isnull=True).exists()


@pytest.mark.django_db
def test_every_new_default_has_plain_words_to_search_for() -> None:
    for category in SearchCategory.objects.filter(archived_at__isnull=True):
        terms = [term for rule in category.rules.all() for term in rule.name_terms]
        assert terms, category.name
        assert not any("*" in term for term in terms), category.name
