"""Rules for fields that may hold personal data: a column's name, and the shape of its values. No AI
is involved (Section 9 of the direction doc), and having no match doesn't mean there's no personal
data."""

import re

# What a name suggests, in the order they're tried, so "email_address" suggests an email address.
# Each phrase is whole words in order.
_NAMES = (
    ("an email address", ("email",), ("mail",)),
    ("a phone number", ("phone",), ("telephone",), ("tel",), ("mobile",), ("cell",), ("fax",)),
    (
        "a payment card number",
        ("card", "number"),
        ("card", "no"),
        ("cc", "number"),
        ("credit", "card"),
        ("debit", "card"),
        ("pan",),
    ),
    (
        "a national or tax ID",
        ("ssn",),
        ("social", "security"),
        ("national", "id"),
        ("national", "insurance"),
        ("nin",),
        ("tax", "id"),
        ("tin",),
        ("passport",),
        ("driver", "licence"),
        ("driver", "license"),
    ),
    (
        "a date of birth",
        ("dob",),
        ("date", "of", "birth"),
        ("birth", "date"),
        ("birthdate",),
        ("birthday",),
    ),
    (
        "a postal address",
        ("address",),
        ("street",),
        ("postcode",),
        ("post", "code"),
        ("postal", "code"),
        ("zip",),
    ),
    (
        "a person's name",
        *(
            (first, "name")
            for first in (
                "first",
                "last",
                "full",
                "given",
                "family",
                "middle",
                "maiden",
                "customer",
                "contact",
                "person",
                "user",
                "employee",
                "patient",
            )
        ),
        ("surname",),
        ("forename",),
    ),
)


def _words(name: str) -> list[str]:
    """`firstName`, `first_name` and `First Name` are all `first`, `name`. A number is no word, as
    a file numbers repeated columns, so `email2` is `email`."""
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", split)
    split = re.sub(r"[0-9]+", " ", split)
    return [word for word in re.split(r"[\W_]+", split.lower()) if word]


def _has(words: list[str], phrase: tuple[str, ...]) -> bool:
    return any(
        tuple(words[start : start + len(phrase)]) == phrase
        for start in range(len(words) - len(phrase) + 1)
    )


def name_suggests(name: str) -> str | None:
    """What kind of personal data a column's name suggests, if any."""
    words = _words(name)
    # "name" alone is a person's, but not with any other word, such as "product_name".
    if words == ["name"]:
        return "a person's name"
    for suggests, *phrases in _NAMES:
        if any(_has(words, phrase) for phrase in phrases):
            return suggests
    return None


# Masked shapes: letters are `A` and digits `9`, and punctuation stays. A pattern that was cut is
# only part of a value, so it's never matched.
_EMAIL = re.compile(r"[A9._%+\-]+@[A9\-]+(\.[A9\-]+)*\.A{2,}")
# Groups of digits split by spaces or dashes, after an optional country code and area code. Dots
# aren't taken, as decimals, versions and IP addresses use them.
_PHONE = re.compile(r"(\+9{1,3}[ \-]?)?(\(9{1,5}\)[ \-]?)?9+([ \-]9+)*")

# Fours split by spaces or dashes, as printed on most cards, or 4-6-5. Alone, only 15 or 16 digits,
# as 13 is a time in milliseconds and 17 to 19 a generated ID as often as a card.
_CARD = re.compile(r"9{4}([ \-]9{4}){2,3}([ \-]9{1,4})?|9{4}([ \-])9{6}\3?9{5}|9{15,16}")


def _email(pattern: str) -> bool:
    return _EMAIL.fullmatch(pattern) is not None


def _phone(pattern: str) -> bool:
    if _PHONE.fullmatch(pattern) is None or not 7 <= pattern.count("9") <= 15:
        return False
    if pattern.startswith("+") or "(" in pattern:
        return True
    groups = [len(digits) for digits in re.findall(r"9+", pattern)]
    # Dates, with the year first or last.
    if groups in ([4, 2, 2], [2, 2, 4]):
        return False
    # Two groups are too few to tell from other numbers unless they're long. A run of digits alone
    # could be any number.
    return len(groups) >= 3 or (len(groups) == 2 and pattern.count("9") >= 10)


def _card(pattern: str) -> bool:
    return _CARD.fullmatch(pattern) is not None and 13 <= pattern.count("9") <= 19


# A card is tried before a phone, as both can be groups of digits.
_SHAPES = (
    ("email addresses", _email),
    ("payment card numbers", _card),
    ("phone numbers", _phone),
)


def values_shaped_like(patterns: list[dict]) -> str | None:
    """What kind of personal data any of a column's kept masked patterns is shaped like, if any."""
    for shaped_like, shape in _SHAPES:
        if any(not kept["cut"] and shape(kept["pattern"]) for kept in patterns):
            return shaped_like
    return None
