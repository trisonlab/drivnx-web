"""`/api/sources` for PostgreSQL: testing a connection, saving it, and its password after a restart.

The password is kept only in this process's memory, never in the workspace (CFX-APP-postgres AC-05).
"""

import threading
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from artifacts.sources import (
    StoredPostgresSource,
    add_postgres_source,
    find_source,
    find_source_by_connection,
)
from artifacts.workspace import Workspace
from connectors.postgres import ConnectFailure, ConnectionDetails, ServerFacts, check
from fastapi import APIRouter, HTTPException, Response
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)


@dataclass(frozen=True)
class Kept:
    password: str
    facts: ServerFacts


class KeptPasswords:
    """Each source's password and what the server said, in this process's memory only."""

    def __init__(self) -> None:
        self._kept: dict[str, Kept] = {}
        self._lock = threading.Lock()

    def keep(self, source_id: str, password: str, facts: ServerFacts) -> None:
        with self._lock:
            self._kept[source_id] = Kept(password, facts)

    def get(self, source_id: str) -> Kept | None:
        with self._lock:
            return self._kept.get(source_id)


def _text(value: str) -> str:
    """Refuse what can't be stored or sent as it was typed: a lone surrogate, which DuckDB can't
    take, and control characters, which libpq would cut a value at or a log would show."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("must be valid text") from None
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError("must not contain control characters")
    return value


def _secret_text(value: SecretStr) -> SecretStr:
    _text(value.get_secret_value())
    return value


def _absolute(value: str) -> str:
    if not Path(value).is_absolute():
        raise ValueError("must be the full path to the file")
    return value


# A host name or an IP address. Not a URL, a list of hosts, or a socket folder, which libpq would
# also accept, and nothing that could add a setting to the connection string.
Host = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{1,253}$")]
# PostgreSQL cuts names longer than 63 bytes.
Name = Annotated[str, StringConstraints(min_length=1, max_length=63), AfterValidator(_text)]


Password = Annotated[
    SecretStr, Field(min_length=1, max_length=1000), AfterValidator(_secret_text)
]


class PasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: Password


class ConnectionRequest(BaseModel):
    # Only these fields, so there is no way to give a connection string or URL (AC-01).
    model_config = ConfigDict(extra="forbid")

    host: Host
    port: Annotated[int, Field(ge=1, le=65535)]
    database: Name
    user: Name
    password: Password
    tls_mode: Literal["prefer", "require", "verify-full"]
    ca_path: (
        Annotated[
            str,
            StringConstraints(min_length=1, max_length=4096),
            AfterValidator(_text),
            AfterValidator(_absolute),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def _ca_for_verify_full(self) -> "ConnectionRequest":
        # verify-full needs the CA that signed the server's certificate, and only it uses one.
        if (self.tls_mode == "verify-full") != (self.ca_path is not None):
            raise ValueError("a CA certificate file goes with verify-full, and only with it")
        return self

    def details(self) -> ConnectionDetails:
        return ConnectionDetails(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            tls_mode=self.tls_mode,
            ca_path=self.ca_path,
        )


# How long a password is kept, which the screen tells the user (AC-05).
UNTIL_STOP = "until_stop"


class ServerResponse(BaseModel):
    server_version: str
    encrypted: bool


class PostgresSourceResponse(BaseModel):
    id: str
    kind: Literal["postgres"]
    name: str
    host: str
    port: int
    database: str
    user: str
    tls_mode: str
    ca_path: str | None
    # Until DRIVN-48 adds the OS credential store, a password lasts until DrivnX stops.
    password_kept: Literal["until_stop"]
    # After a restart, until the user gives the password again.
    password_needed: bool
    server_version: str | None
    encrypted: bool | None


def postgres_response(source: StoredPostgresSource, kept: Kept | None) -> PostgresSourceResponse:
    return PostgresSourceResponse(
        id=source.id,
        kind="postgres",
        name=source.database,
        host=source.host,
        port=source.port,
        database=source.database,
        user=source.user,
        tls_mode=source.tls_mode,
        ca_path=source.ca_path,
        password_kept=UNTIL_STOP,
        password_needed=kept is None,
        server_version=kept.facts.version if kept else None,
        encrypted=kept.facts.encrypted if kept else None,
    )


def _details_of(source: StoredPostgresSource) -> ConnectionDetails:
    return ConnectionDetails(
        host=source.host,
        port=source.port,
        database=source.database,
        user=source.user,
        tls_mode=source.tls_mode,
        ca_path=source.ca_path,
    )


def _connected(details: ConnectionDetails, password: str, workspace: Workspace) -> ServerFacts:
    try:
        return check(details, password, workspace.temp_directory)
    except ConnectFailure as failure:
        raise HTTPException(status_code=422, detail=str(failure)) from None


CANNOT_CHANGE_YET = "Changing a saved connection isn't available yet."


def _same_tls(saved: StoredPostgresSource, details: ConnectionDetails) -> None:
    """Saving a saved connection again must not drop the TLS settings the user just chose."""
    if saved.tls_mode != details.tls_mode:
        raise HTTPException(
            status_code=409,
            detail=f"This connection is already saved with TLS mode {saved.tls_mode}. "
            + CANNOT_CHANGE_YET,
        )
    if saved.ca_path != details.ca_path:
        raise HTTPException(
            status_code=409,
            detail="This connection is already saved with another CA certificate file. "
            + CANNOT_CHANGE_YET,
        )


def postgres_router(workspace: Workspace, kept: KeptPasswords) -> APIRouter:
    router = APIRouter(prefix="/api/sources")

    @router.post("/postgres/test")
    def test_connection(body: ConnectionRequest) -> ServerResponse:
        facts = _connected(body.details(), body.password.get_secret_value(), workspace)
        return ServerResponse(server_version=facts.version, encrypted=facts.encrypted)

    @router.post("/postgres", status_code=201)
    def save_connection(body: ConnectionRequest, response: Response) -> PostgresSourceResponse:
        details = body.details()
        identity = (details.host, details.port, details.database, details.user)
        saved = find_source_by_connection(workspace.engine, *identity)
        if saved is not None:
            _same_tls(saved, details)
        password = body.password.get_secret_value()
        # Tested first, so a connection that doesn't work is never saved (AC-03).
        facts = _connected(details, password, workspace)
        source = StoredPostgresSource(
            id=uuid.uuid4().hex,
            host=details.host,
            port=details.port,
            database=details.database,
            user=details.user,
            tls_mode=details.tls_mode,
            ca_path=details.ca_path,
        )
        if not add_postgres_source(workspace.engine, source):
            # Saved by another request while this one was testing.
            response.status_code = 200
            existing = find_source_by_connection(workspace.engine, *identity)
            assert existing is not None
            _same_tls(existing, details)
            source = existing
        kept.keep(source.id, password, facts)
        return postgres_response(source, kept.get(source.id))

    @router.post("/{source_id}/password")
    def give_password(source_id: str, body: PasswordRequest) -> PostgresSourceResponse:
        """After a restart: kept only if the server takes it (AC-05)."""
        source = find_source(workspace.engine, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="There's no such source.")
        if not isinstance(source, StoredPostgresSource):
            raise HTTPException(status_code=409, detail="A file has no password.")
        password = body.password.get_secret_value()
        kept.keep(source.id, password, _connected(_details_of(source), password, workspace))
        return postgres_response(source, kept.get(source.id))

    return router
