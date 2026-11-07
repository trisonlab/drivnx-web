"""The rules that turn a run's observations into findings, CFX-APP-csv-profile AC-21 and AC-22."""

from dataclasses import dataclass
from typing import Any

import pytest
from profiling.findings import findings_for


@dataclass(frozen=True)
class Seen:
    id: str
    field_id: str | None
    metric: str
    value: Any


def column(field_id: str, **metrics: Any) -> list[Seen]:
    """A column's observations. Every column saves how many values don't fit, as a run does."""
    metrics = {"not_fitting_count": 0, **metrics}
    return [Seen(f"{field_id}.{metric}", field_id, metric, value) for metric, value in metrics.items()]


def run(rows_read: int, *columns: list[Seen], **of_the_file: Any) -> list[Seen]:
    return [
        Seen("rows_read", None, "rows_read", rows_read),
        *(Seen(metric, None, metric, value) for metric, value in of_the_file.items()),
        *(seen for found in columns for seen in found),
    ]


def kinds(observations: list[Seen]) -> list[tuple[str, tuple[str, ...]]]:
    return [(found.kind, found.observation_ids) for found in findings_for(observations)]


@pytest.mark.parametrize(("distinct", "found"), [(99, True), (98, False)])
def test_a_column_at_least_99_percent_distinct_is_a_possible_identifier(
    distinct: int, found: bool
) -> None:
    # CFX-APP-csv-profile/AC-21: 99 of 100 values are distinct, and 98 of 100 aren't enough.
    observations = run(
        100,
        column("id", missing_count=0, distinct_count=distinct, distinct_percent=float(distinct)),
    )

    assert kinds(observations) == (
        [("possible_identifier", ("id.distinct_count", "id.distinct_percent"))] if found else []
    )


def test_a_column_with_no_values_is_no_identifier() -> None:
    # CFX-APP-csv-profile/AC-21: an identifier needs at least one value. Nothing to divide by means
    # no distinct percentage is saved.
    observations = run(
        10, column("note", missing_count=10, missing_percent=100.0, distinct_count=0)
    )

    assert "possible_identifier" not in [kind for kind, _ in kinds(observations)]


@pytest.mark.parametrize(("missing", "found"), [(20, True), (19, False)])
def test_a_column_at_least_20_percent_missing_has_significant_missing_values(
    missing: int, found: bool
) -> None:
    # CFX-APP-csv-profile/AC-21
    observations = run(
        100,
        column(
            "note",
            missing_count=missing,
            missing_percent=float(missing),
            distinct_count=2,
            distinct_percent=2.5,
        ),
    )

    assert kinds(observations) == (
        [("missing_values", ("note.missing_count", "note.missing_percent"))] if found else []
    )


@pytest.mark.parametrize(("rows", "found"), [(3, True), (1, False)])
def test_a_column_with_one_value_in_at_least_two_rows_is_single_value(
    rows: int, found: bool
) -> None:
    # CFX-APP-csv-profile/AC-21: decided in T08's plan. A one-row file is neither single-value nor,
    # since spec revision 4, an identifier.
    observations = run(
        rows,
        column("country", missing_count=0, distinct_count=1, distinct_percent=100 / rows),
    )

    assert kinds(observations) == ([("single_value", ("country.distinct_count",))] if found else [])


@pytest.mark.parametrize(("not_fitting", "found"), [(1, True), (0, False)])
def test_a_column_with_a_value_that_doesnt_fit_its_type_says_so(
    not_fitting: int, found: bool
) -> None:
    # CFX-APP-csv-profile/AC-21: decided in T08's plan, one is enough. Text has none to find.
    observations = run(
        10,
        column(
            "amount",
            missing_count=0,
            distinct_count=5,
            distinct_percent=50.0,
            not_fitting_count=not_fitting,
        ),
    )

    assert kinds(observations) == (
        [("not_fitting", ("amount.not_fitting_count",))] if found else []
    )


@pytest.mark.parametrize(
    ("of_the_file", "found"),
    [({"duplicate_rows": 2}, True), ({"duplicate_rows": 0}, False), ({}, False)],
    ids=["duplicates", "none", "a fast profile counts none"],
)
def test_duplicated_rows_are_a_finding_about_the_file(
    of_the_file: dict[str, int], found: bool
) -> None:
    # CFX-APP-csv-profile/AC-22: only a full profile counts them.
    observations = run(
        10,
        column("id", missing_count=0, distinct_count=5, distinct_percent=50.0),
        **of_the_file,
    )

    assert kinds(observations) == ([("duplicate_rows", ("duplicate_rows",))] if found else [])


def test_findings_come_in_a_fixed_order_by_kind_then_column() -> None:
    # CFX-APP-csv-profile/AC-21: the spec's order is possibly sensitive (T09), values that don't fit
    # the type, duplicate rows, significant missing values, possible identifier, single-value column.
    observations = run(
        100,
        column("id", missing_count=0, distinct_count=100, distinct_percent=100.0),
        column(
            "amount",
            missing_count=30,
            missing_percent=30.0,
            distinct_count=2,
            distinct_percent=2.86,
            not_fitting_count=4,
        ),
        column("code", missing_count=0, distinct_count=99, distinct_percent=99.0),
        duplicate_rows=3,
    )

    assert [ids[0] for _, ids in kinds(observations)] == [
        "amount.not_fitting_count",
        "duplicate_rows",
        "amount.missing_count",
        "id.distinct_count",
        "code.distinct_count",
    ]


def test_a_table_with_no_rows_has_no_findings() -> None:
    # CFX-APP-csv-profile/AC-21: a file that's only a header. Nothing to divide by means no
    # percentages are saved.
    observations = run(0, column("id", missing_count=0, distinct_count=0))

    assert kinds(observations) == []


def test_a_column_of_one_value_among_blanks_is_no_identifier() -> None:
    # CFX-APP-csv-profile/AC-21: spec revision 4, found reviewing T08. One value is 100% distinct.
    observations = run(
        1000,
        column(
            "sparse",
            missing_count=999,
            missing_percent=99.9,
            distinct_count=1,
            distinct_percent=100.0,
        ),
    )

    assert [kind for kind, _ in kinds(observations)] == ["missing_values"]
