"""Rule-based findings from a run's saved observations. Nothing here reads the file again."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class Evidence(Protocol):
    """An observation as saved: what a finding can cite."""

    @property
    def id(self) -> str: ...
    @property
    def field_id(self) -> str | None: ...
    @property
    def metric(self) -> str: ...
    @property
    def value(self) -> Any: ...


@dataclass(frozen=True)
class DraftFinding:
    kind: str
    observation_ids: tuple[str, ...]
    interpretation: str
    why_it_matters: str
    recommended_action: str


# The spec's fixed order. Real prioritisation is M5.
KINDS = (
    "possibly_sensitive",
    "not_fitting",
    "duplicate_rows",
    "missing_values",
    "possible_identifier",
    "single_value",
)
# What a kind of finding says. It's saved, so it holds no column name, value or number (AC-25): the
# screen shows what was observed from the observations it cites.
_TEXTS = {
    "not_fitting": (
        "Values that don't fit the column's type",
        "They may be mistakes, or the column may hold more than one kind of value.",
        "Check the values that don't fit, and whether the column's type is right.",
    ),
    "duplicate_rows": (
        "Rows that are exact copies of another row",
        "Copies can count the same thing twice in totals and averages.",
        "Check whether the copies are expected, and remove them where the file comes from if not.",
    ),
    "missing_values": (
        "Significant missing values",
        "Counts and averages from this column may leave out a large part of the data.",
        "Find out why values are missing, and whether this column should always have one.",
    ),
    "possible_identifier": (
        "Possible identifier",
        "It may be the key that tells each row apart.",
        "Confirm whether this column identifies each row.",
    ),
    "single_value": (
        "Single-value column",
        "A column with one value tells rows apart by nothing, and may be left from a filter.",
        "Check whether the column is needed, or whether the file was filtered.",
    ),
}


def _draft(kind: str, cited: Sequence[Evidence]) -> DraftFinding:
    return DraftFinding(kind, tuple(evidence.id for evidence in cited), *_TEXTS[kind])


def findings_for(observations: Sequence[Evidence]) -> list[DraftFinding]:
    """Findings in the fixed order by kind, then in the order their columns were saved."""
    of_the_file = {e.metric: e for e in observations if e.field_id is None}
    rows_read = of_the_file["rows_read"].value
    columns: dict[str, dict[str, Evidence]] = {}
    for evidence in observations:
        if evidence.field_id is not None:
            columns.setdefault(evidence.field_id, {})[evidence.metric] = evidence
    found = []
    # Only a full profile counts them.
    duplicates = of_the_file.get("duplicate_rows")
    if duplicates is not None and duplicates.value >= 1:
        found.append(_draft("duplicate_rows", [duplicates]))
    for metrics in columns.values():
        not_fitting = metrics["not_fitting_count"]
        if not_fitting.value >= 1:
            found.append(_draft("not_fitting", [not_fitting]))
        missing = metrics["missing_count"]
        if rows_read >= 1 and missing.value * 100 >= rows_read * 20:
            found.append(_draft("missing_values", [missing, metrics["missing_percent"]]))
        present = rows_read - missing.value
        distinct = metrics["distinct_count"]
        # At least two values (spec revision 4), as one alone is always 100% distinct.
        if present >= 2 and distinct.value * 100 >= present * 99:
            found.append(_draft("possible_identifier", [distinct, metrics["distinct_percent"]]))
        # At least two, as one value alone says nothing about the rest.
        if present >= 2 and distinct.value == 1:
            found.append(_draft("single_value", [distinct]))
    # Stable, so columns keep their order within a kind.
    return sorted(found, key=lambda finding: KINDS.index(finding.kind))
