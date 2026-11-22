"""What the role a database is profiled with could change in the chosen objects, and the warning
the user confirms before it is (DRIVN-45 AC-16, DRIVN-52). DrivnX itself only reads: every
session it opens is read-only."""

from artifacts.sources import ChosenObject
from connectors.postgres import Discovery
from fastapi import HTTPException


def write_access(discovery: Discovery, chosen: tuple[ChosenObject, ...]) -> dict:
    """The role, whether it's a superuser, each chosen object it could change and with what, and
    every chosen object, as `schema.object`, from a discovery made as the run starts."""
    found = {(each.schema, each.name): each for each in discovery.objects}
    return {
        "role": discovery.role,
        "superuser": discovery.superuser,
        "objects": [
            {"object": f"{each.schema}.{each.name}", "privileges": list(privileges)}
            for each in chosen
            if (privileges := found[(each.schema, each.name)].write_privileges)
        ],
        "chosen": [f"{each.schema}.{each.name}" for each in chosen],
    }


def can_write(access: dict) -> bool:
    """Whether the role could change any of the chosen objects."""
    return access["superuser"] or bool(access["objects"])


def refusal(access: dict) -> HTTPException:
    """The start refused until the user confirms: what the role could change, and the access the
    client sends back to confirm it. A superuser could change anything: its role is the reason."""
    if access["superuser"]:
        message = (
            f"{access['role']} is a superuser, so it can change anything in this database. DrivnX"
            " opens every session read-only and only reads it. Confirm to profile it anyway."
        )
    else:
        changed = ", ".join(
            f"{each['object']} ({', '.join(privilege.lower() for privilege in each['privileges'])})"
            for each in access["objects"]
        )
        message = (
            f"{access['role']} can change these chosen objects: {changed}. DrivnX opens every"
            " session read-only and only reads them. Confirm to profile them anyway."
        )
    return HTTPException(status_code=409, detail={"message": message, "write_access": access})
