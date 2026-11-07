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
    return [(found.kind, found.observation_ids) for found in findings_of(observations)]


def findings_of(observations: list[Seen]) -> list:
    """Each column is named by its field ID here."""
    names = {seen.field_id: seen.field_id for seen in observations if seen.field_id is not None}
    return findings_for(observations, names)


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
    # CFX-APP-csv-profile/AC-21: the spec's order is possibly sensitive, values that don't fit the
    # type, duplicate rows, significant missing values, possible identifier, single-value column.
    observations = run(
        100,
        column("id", missing_count=0, distinct_count=100, distinct_percent=100.0),
        column("email", missing_count=0, distinct_count=50, distinct_percent=50.0),
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
        "email.missing_count",
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


def sensitive(observations: list[Seen]) -> list[tuple[tuple[str, ...], str]]:
    """Each possibly-sensitive finding's evidence and what it says was found."""
    return [
        (found.observation_ids, found.interpretation)
        for found in findings_of(observations)
        if found.kind == "possibly_sensitive"
    ]


def plain(name: str, **metrics: Any) -> list[Seen]:
    """A column that meets no other finding's condition in a run of 10 rows."""
    return column(name, missing_count=0, distinct_count=5, distinct_percent=50.0, **metrics)


@pytest.mark.parametrize(
    ("name", "suggests"),
    [
        ("email", "an email address"),
        ("Customer E-mail", "an email address"),
        ("mail", "an email address"),
        ("phone", "a phone number"),
        ("mobileNumber", "a phone number"),
        ("TEL", "a phone number"),
        ("fax", "a phone number"),
        ("card_number", "a payment card number"),
        ("CreditCard", "a payment card number"),
        ("cc-number", "a payment card number"),
        ("PAN", "a payment card number"),
        ("ssn", "a national or tax ID"),
        ("national_insurance_no", "a national or tax ID"),
        ("taxId", "a national or tax ID"),
        ("passport", "a national or tax ID"),
        ("driver license", "a national or tax ID"),
        ("DOB", "a date of birth"),
        ("date_of_birth", "a date of birth"),
        ("birthdate", "a date of birth"),
        ("home_address", "a postal address"),
        ("ZIPCode", "a postal address"),
        ("post code", "a postal address"),
        ("street", "a postal address"),
        ("first_name", "a person's name"),
        ("lastName", "a person's name"),
        ("surname", "a person's name"),
        ("Name", "a person's name"),
        ("customer_name", "a person's name"),
        ("patient name", "a person's name"),
        # A number attached, as a file numbers repeated columns.
        ("email2", "an email address"),
        ("Email1", "an email address"),
        ("Phone1", "a phone number"),
        ("Address1", "a postal address"),
        ("name2", "a person's name"),
    ],
)
def test_a_column_whose_name_matches_a_rule_is_possibly_sensitive(name: str, suggests: str) -> None:
    # CFX-APP-csv-profile/AC-27: by its name alone, with no AI. A name isn't an observation, so the
    # finding cites the column's missing count, which every column saves, to point at the column.
    observations = run(10, plain(name))

    assert sensitive(observations) == [
        ((f"{name}.missing_count",), f"Possibly sensitive field: its name suggests {suggests}")
    ]


@pytest.mark.parametrize(
    "name",
    [
        *("emailed", "product_name", "file name", "amount", "telemetry", "pancake", "card_type"),
        *("id", "emailed2", "product_name2", "mp3"),
    ],
)
def test_a_column_whose_name_matches_no_rule_isnt_flagged_by_its_name(name: str) -> None:
    # CFX-APP-csv-profile/AC-21 and CFX-APP-csv-profile/AC-27: whole words only, and "name" only
    # alone or with a word for a person.
    assert sensitive(run(10, plain(name))) == []


def shaped(name: str, *patterns: str) -> list[Seen]:
    """A column whose kept masked patterns are these, one value each."""
    return plain(
        name,
        masked_patterns=[{"pattern": found, "count": 1, "cut": False} for found in patterns],
    )


@pytest.mark.parametrize(
    "pattern",
    ["AAAA.AAA@AAAAAAA.AAA", "AAA9@AAA.AA.AA", "A_A+A-A%A@A-A.AAAA", "AAAA@99AAA.AA"],
)
def test_a_column_with_a_value_shaped_like_an_email_address_is_possibly_sensitive(
    pattern: str,
) -> None:
    # CFX-APP-csv-profile/AC-27: one of its kept patterns is enough, among others that aren't.
    observations = run(10, shaped("contact", "AAAA", pattern, "99"))

    assert sensitive(observations) == [
        (
            ("contact.masked_patterns",),
            "Possibly sensitive field: its values are shaped like email addresses",
        )
    ]


@pytest.mark.parametrize(
    "pattern", ["AAAA@AAAA", "@AAA.AAA", "AAA@.AAA", "AAA@AAA.99", "AAA AAA@AAA.AAA", "AAA@@AA.AA"]
)
def test_a_value_only_partly_like_an_email_address_isnt_flagged(pattern: str) -> None:
    # CFX-APP-csv-profile/AC-21 and CFX-APP-csv-profile/AC-27
    assert sensitive(run(10, shaped("contact", pattern))) == []


@pytest.mark.parametrize(
    "pattern",
    [
        "+99 999 999 9999",
        "(999) 999-9999",
        "999-999-9999",
        "+99999999999",
        "+99 (9) 9999 999999",
        "99999 999999",
    ],
)
def test_a_column_with_a_value_shaped_like_a_phone_number_is_possibly_sensitive(
    pattern: str,
) -> None:
    # CFX-APP-csv-profile/AC-27
    observations = run(10, shaped("contact", "AAAA", pattern))

    assert sensitive(observations) == [
        (
            ("contact.masked_patterns",),
            "Possibly sensitive field: its values are shaped like phone numbers",
        )
    ]


@pytest.mark.parametrize(
    "pattern",
    [
        "9999-99-99",
        "99-99-9999",
        "999-9999",
        "9999999999",
        "9999.99",
        "999.999.9999",
        "99:99:99",
        "+99 99",
        "+99 999 999 9999 9999",
        "AAA-999-9999",
    ],
    ids=[
        "a date",
        "a date, day first",
        "too short to tell",
        "only digits",
        "a decimal",
        "dots, as in versions and addresses",
        "a time",
        "too few digits",
        "too many digits",
        "letters",
    ],
)
def test_a_value_only_partly_like_a_phone_number_isnt_flagged(pattern: str) -> None:
    # CFX-APP-csv-profile/AC-21 and CFX-APP-csv-profile/AC-27
    assert sensitive(run(10, shaped("reading", pattern))) == []


@pytest.mark.parametrize(
    "pattern",
    [
        "9999 9999 9999 9999",
        "9999-9999-9999-9999",
        "9999999999999999",
        "999999999999999",
        "9999 999999 99999",
        "9999 9999 9999 9",
        "9999 9999 9999 9999 999",
    ],
    ids=["spaces", "dashes", "16 digits", "15 digits", "4-6-5", "13 digits", "19 digits"],
)
def test_a_column_with_a_value_shaped_like_a_payment_card_number_is_possibly_sensitive(
    pattern: str,
) -> None:
    # CFX-APP-csv-profile/AC-27: only the shape, as checking a card's digits would need its value.
    observations = run(10, shaped("reference", "AAAA", pattern))

    assert sensitive(observations) == [
        (
            ("reference.masked_patterns",),
            "Possibly sensitive field: its values are shaped like payment card numbers",
        )
    ]


@pytest.mark.parametrize(
    "pattern",
    [
        "9999999999999",
        "999999999999999999",
        "9999 9999",
        "9999-9999-9999-9999-9999",
        "9999 AAAA 9999 9999",
    ],
    ids=[
        "13 digits alone, as a time in milliseconds",
        "18 digits alone, as a generated ID",
        "too few digits",
        "too many digits",
        "letters",
    ],
)
def test_a_value_only_partly_like_a_payment_card_number_isnt_flagged(pattern: str) -> None:
    # CFX-APP-csv-profile/AC-21 and CFX-APP-csv-profile/AC-27
    assert sensitive(run(10, shaped("reference", pattern))) == []


def test_a_column_matching_by_name_and_pattern_gets_one_finding_saying_both() -> None:
    # CFX-APP-csv-profile/AC-21 and CFX-APP-csv-profile/AC-27: one finding for each column
    # concerned. It cites both, so the screen can say both, with the patterns first for its link.
    observations = run(10, shaped("Phone", "+99 999 999 9999"))

    assert sensitive(observations) == [
        (
            ("Phone.masked_patterns", "Phone.missing_count"),
            (
                "Possibly sensitive field: its name suggests a phone number, and its values are"
                " shaped like phone numbers"
            ),
        )
    ]
