"""What a valid contract rule is, and which rules compete (ADR 0003 points 2 and 4)."""

import re
from collections.abc import Callable, Hashable, Sequence
from datetime import date, datetime, time
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

_ONE_FIELD = ("required", "type", "accepted_values", "range")
# The two rule types that say what the one logicalType in an export is (ADR 0003 point 4).
AGREES_WITH = {"range": "type", "type": "range"}


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class _Required(_Params):
    rule_type: Literal["required"]


class _Unique(_Params):
    rule_type: Literal["unique"]


class _Identifier(_Params):
    rule_type: Literal["identifier"]


class _Relationship(_Params):
    rule_type: Literal["relationship"]


class _Type(_Params):
    rule_type: Literal["type"]
    # What ODCS v3.2.0 allows as a property's logicalType.
    logical_type: Literal[
        "string",
        "date",
        "timestamp",
        "time",
        "number",
        "integer",
        "object",
        "array",
        "boolean",
        "map",
        "vector",
    ]


class _AcceptedValues(_Params):
    rule_type: Literal["accepted_values"]
    values: list[str | int | float | bool] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def _unique_text_and_numbers(cls, values: list) -> list:
        # JSON keeps true and 1 apart, and Python would call them equal. 1 and 1.0 are one number.
        if len({(isinstance(value, bool), value) for value in values}) != len(values):
            raise ValueError("Accepted values must be unique.")
        for value in values:
            if isinstance(value, str):
                # A lone surrogate raises here, as a response could never send it back.
                value.encode()
        return values


def _number(end: object) -> object:
    if isinstance(end, int | float):
        return end
    raise ValueError("This range's ends must be numbers.")


def _integer(end: object) -> object:
    if isinstance(end, int):
        return end
    raise ValueError("This range's ends must be whole numbers.")


def _from_text(read: Callable[[str], object]) -> Callable[[object], object]:
    def parse(end: object) -> object:
        if isinstance(end, str):
            return read(end)
        raise ValueError("This range's ends must be ISO 8601 text.")

    return parse


def _timestamp(text: str) -> datetime:
    # fromisoformat takes any one character between the date and the time, which ISO 8601 doesn't.
    date.fromisoformat(re.split("[Tt ]", text, maxsplit=1)[0])
    return datetime.fromisoformat(text)


_READING_ENDS = {
    "number": _number,
    "integer": _integer,
    "date": _from_text(date.fromisoformat),
    "timestamp": _from_text(_timestamp),
    "time": _from_text(time.fromisoformat),
}


class _Range(_Params):
    rule_type: Literal["range"]
    # ODCS rejects a minimum or maximum on a property of any other type.
    logical_type: Literal["number", "integer", "date", "timestamp", "time"]
    minimum: int | float | str | None = None
    maximum: int | float | str | None = None

    @model_validator(mode="after")
    def _ends_fit(self) -> Self:
        read = _READING_ENDS[self.logical_type]
        ends = [read(end) for end in (self.minimum, self.maximum) if end is not None]
        if not ends:
            raise ValueError("A range needs a minimum, a maximum or both.")
        try:
            backwards = len(ends) == 2 and ends[0] > ends[1]
        except TypeError:  # A timestamp with a time zone and one without.
            raise ValueError("This range's ends can't be compared.") from None
        if backwards:
            raise ValueError("A range's minimum can't be above its maximum.")
        return self


_RULE = TypeAdapter(
    Annotated[
        _Required
        | _Type
        | _Unique
        | _AcceptedValues
        | _Range
        | _Identifier
        | _Relationship,
        Field(discriminator="rule_type"),
    ]
)


def fields_with_role(fields: Sequence[tuple[str, str]], role: str) -> list[str]:
    return [field for field_role, field in fields if field_role == role]


def check_fields(rule_type: str, fields: Sequence[tuple[str, str]]) -> None:
    """ADR 0003 point 4, the Target column. A rule's fields never change, so this is the one check."""
    subjects = fields_with_role(fields, "subject")
    targets = fields_with_role(fields, "target")
    if len(subjects) + len(targets) != len(fields):
        raise ValueError("A rule's field is either its subject or its target.")
    if len(set(subjects)) != len(subjects) or len(set(targets)) != len(targets):
        raise ValueError("A rule can't list a field twice.")
    if rule_type == "relationship":
        if not subjects or len(subjects) != len(targets):
            raise ValueError(
                "A relationship needs as many target fields as subject fields, at least one."
            )
    elif targets:
        raise ValueError("Only a relationship has target fields.")
    elif rule_type in _ONE_FIELD and len(subjects) != 1:
        raise ValueError(f"This {rule_type} rule must be about exactly one field.")
    elif not subjects:
        raise ValueError(f"This {rule_type} rule needs at least one field.")


def _params_fit(rule_type: str, params: dict[str, Any]) -> bool:
    if "rule_type" in params:
        return False
    try:
        _RULE.validate_python({**params, "rule_type": rule_type})
    except ValueError:  # Which a ValidationError is.
        return False
    return True


def check_params(rule_type: str, params: dict[str, Any]) -> None:
    """ADR 0003 point 2. Nothing from `params` goes in the refusal, as it can be source data.

    Pydantic's error repeats its input, so it's kept off this exception's traceback and context.
    """
    if not _params_fit(rule_type, params):
        raise ValueError(f"This rule's params don't fit its type, {rule_type}.")


def slot(rule_type: str, fields: Sequence[tuple[str, str]]) -> Hashable:
    """Two Defined rules of one type can't share this, as ODCS has one place for them."""
    subjects = fields_with_role(fields, "subject")
    match rule_type:
        case "identifier":
            return ()
        case "unique":
            return frozenset(subjects)
        case "relationship":
            return frozenset(zip(subjects, fields_with_role(fields, "target")))
        case _:
            return subjects[0]
