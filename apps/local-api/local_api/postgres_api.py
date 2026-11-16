"""`/api/sources` for PostgreSQL: testing a connection, saving it, and its password after a restart.

The password is kept in this process's memory, and in the OS credential store where there is one
(CFX-APP-postgres AC-04, AC-05, ADR 0008); never in the workspace.
"""

import logging
import threading
import unicodedata
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Literal

from artifacts.sources import (
    NotChoosable,
    ObjectFound,
    StoredColumn,
    StoredKey,
    StoredObject,
    StoredObjects,
    StoredPostgresSource,
    add_postgres_source,
    find_source,
    find_source_by_connection,
    read_object,
    read_objects,
    save_discovery,
    save_selection,
)
from artifacts.workspace import Workspace
from connectors.postgres import (
    ConnectFailure,
    ConnectionDetails,
    Discovery,
    ServerFacts,
    check,
    discover,
)
from fastapi import APIRouter, HTTPException, Response
from keyring.backend import KeyringBackend
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

from local_api.credentials import entry

_log = logging.getLogger(__name__)


def _store_failed(action: str, error: Exception) -> None:
    """Only the kind of failure: the store's own text could say anything (AC-30)."""
    _log.warning("The OS credential store couldn't %s a password: %s", action, type(error).__name__)


# How long a password is kept, which the screen tells the user (AC-04, AC-05).
UNTIL_STOP = "until_stop"
CREDENTIAL_STORE = "credential_store"
HowKept = Literal["until_stop", "credential_store"]


@dataclass(frozen=True)
class Kept:
    password: str
    # None for a password read from the store, until the server is asked in this run.
    facts: ServerFacts | None
    kept: HowKept


class KeptPasswords:
    """Each source's password and what the server said, in this process's memory, and the
    password also in the OS credential store when there is one (AC-04)."""

    def __init__(self, store: KeyringBackend | None) -> None:
        self._store = store
        self._kept: dict[str, Kept] = {}
        # Sources that ask the user for the password, not the store, until one is given: the
        # server refused the stored one (AC-06), or the store had none or couldn't be read. A
        # locked store shows its unlock prompt on every read, so it's read once per run.
        self._asking: set[str] = set()
        # The store failed when read: not read again in this run, for any source, as a locked
        # store prompts on each read (ADR 0008 Decision 6). A password given is still written.
        self._unreadable = False
        self._lock = threading.Lock()

    def keep(self, source: StoredPostgresSource, password: str, facts: ServerFacts) -> None:
        kept: HowKept = UNTIL_STOP
        if self._store is not None:
            try:
                self._store.set_password(*entry(source), password)
                kept = CREDENTIAL_STORE
            except Exception as error:
                # Locked, its prompt dismissed, or full: kept in memory only, as the screen
                # then says (AC-05). keyring raises no KeyringError for Windows' errors or a
                # dismissed Secret Service prompt, so any failure counts (ADR 0008 Decision 8).
                _store_failed("keep", error)
        with self._lock:
            self._kept[source.id] = Kept(password, facts, kept)
            self._asking.discard(source.id)

    @property
    def next_kept(self) -> HowKept:
        """Where a password given now goes, which the screen says before it's asked for."""
        return UNTIL_STOP if self._store is None else CREDENTIAL_STORE

    def connected(self, source_id: str, known: Kept, facts: ServerFacts) -> None:
        """The server took the kept or stored password: what it said, with the password left
        where it is."""
        with self._lock:
            # Unless another password replaced it meanwhile.
            if self._kept.get(source_id) is known:
                self._kept[source_id] = replace(known, facts=facts)

    def refused(self, source_id: str, known: Kept) -> None:
        """The server refused the password: ask for it, until one it takes replaces it."""
        with self._lock:
            # Unless another password replaced it meanwhile, which the server took.
            if self._kept.get(source_id) is known:
                del self._kept[source_id]
                self._asking.add(source_id)

    def get(self, source: StoredPostgresSource) -> Kept | None:
        """From memory, else read once from the store, as after a restart (AC-04)."""
        source_id = source.id
        with self._lock:
            known = self._kept.get(source_id)
            if (
                known is not None
                or self._store is None
                or self._unreadable
                or source_id in self._asking
            ):
                return known
        # Outside the lock: the store may take a moment, or wait for the user to unlock it.
        try:
            password = self._store.get_password(*entry(source))
        except Exception as error:
            # Locked, its prompt dismissed, or failing: asked for, as without a store.
            _store_failed("read", error)
            self._unreadable = True
            password = None
        with self._lock:
            known = self._kept.get(source_id)
            if known is not None or source_id in self._asking:
                return known
            if password is None:
                self._asking.add(source_id)
                return None
            return self._kept.setdefault(source_id, Kept(password, None, CREDENTIAL_STORE))


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
    # In the OS credential store, or until DrivnX stops where there is none.
    password_kept: HowKept
    # After a restart, until the user gives the password again.
    password_needed: bool
    server_version: str | None
    encrypted: bool | None


def postgres_response(
    source: StoredPostgresSource, passwords: KeptPasswords
) -> PostgresSourceResponse:
    kept = passwords.get(source)
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
        password_kept=kept.kept if kept else passwords.next_kept,
        password_needed=kept is None,
        server_version=kept.facts.version if kept and kept.facts else None,
        encrypted=kept.facts.encrypted if kept and kept.facts else None,
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


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The chosen objects' ids, as the list gave them (32 characters today). An empty list
    # chooses nothing.
    object_ids: Annotated[
        list[Annotated[str, StringConstraints(min_length=1, max_length=64)]],
        Field(max_length=100_000),
    ]


class ObjectSummary(BaseModel):
    id: str
    name: str
    kind: Literal["table", "partitioned_table", "view", "materialized_view"]
    # None when the server has no estimate: a view, or an object never analysed.
    estimated_rows: int | None
    readable: bool
    # False for a materialized view never refreshed: it can't be read or chosen.
    populated: bool
    chosen: bool


class SchemaResponse(BaseModel):
    name: str
    objects: list[ObjectSummary]


class ObjectsResponse(BaseModel):
    # None until the source's objects are first listed.
    discovered_at: str | None
    schemas: list[SchemaResponse]


class ObjectColumn(BaseModel):
    name: str
    physical_type: str
    normalised_type: str
    allows_nulls: bool


class KeyReference(BaseModel):
    # Sent as "schema": a field of that name would shadow BaseModel.schema().
    schema_: str = Field(serialization_alias="schema")
    object: str
    columns: list[str]


class KeyResponse(BaseModel):
    kind: Literal["primary_key", "unique", "foreign_key"]
    name: str
    # In the key's order.
    columns: list[str]
    # What a foreign key references, by name: it may not be listed.
    references: KeyReference | None


class ObjectResponse(ObjectSummary):
    schema_: str = Field(serialization_alias="schema")
    columns: list[ObjectColumn]
    keys: list[KeyResponse]


def _summary(found: StoredObject) -> ObjectSummary:
    return ObjectSummary(
        id=found.id,
        name=found.name,
        kind=found.kind,
        estimated_rows=found.estimated_rows,
        readable=found.readable,
        populated=found.populated,
        chosen=found.chosen,
    )


def objects_response(stored: StoredObjects) -> ObjectsResponse:
    return ObjectsResponse(
        discovered_at=stored.discovered_at,
        schemas=[
            SchemaResponse(
                name=schema,
                objects=[_summary(found) for found in stored.objects if found.schema == schema],
            )
            for schema in stored.schemas
        ],
    )


def object_response(
    found: StoredObject, columns: tuple[StoredColumn, ...], keys: tuple[StoredKey, ...]
) -> ObjectResponse:
    return ObjectResponse(
        **_summary(found).model_dump(),
        schema_=found.schema,
        columns=[
            ObjectColumn(
                name=column.name,
                physical_type=column.physical_type,
                normalised_type=column.normalised_type,
                allows_nulls=column.allows_nulls,
            )
            for column in columns
        ],
        keys=[
            KeyResponse(
                kind=key.kind,
                name=key.name,
                columns=list(key.columns),
                references=None
                if key.referenced_object is None
                else KeyReference(
                    schema_=key.referenced_schema,
                    object=key.referenced_object,
                    columns=list(key.referenced_columns or ()),
                ),
            )
            for key in keys
        ],
    )


def _save_discovery(workspace: Workspace, source_id: str, discovery: Discovery) -> None:
    """Save what the connector found. The workspace package doesn't know the connector's types."""
    save_discovery(
        workspace.engine,
        source_id,
        discovery.schemas,
        tuple(
            ObjectFound(
                schema=found.schema,
                name=found.name,
                kind=found.kind,
                estimated_rows=found.estimated_rows,
                readable=found.readable,
                columns=tuple(
                    StoredColumn(
                        column.name,
                        column.physical_type,
                        column.normalised_type,
                        column.allows_nulls,
                    )
                    for column in found.columns
                ),
                keys=tuple(
                    StoredKey(
                        key.kind,
                        key.name,
                        key.columns,
                        key.referenced_schema,
                        key.referenced_object,
                        key.referenced_columns,
                    )
                    for key in found.keys
                ),
                populated=found.populated,
            )
            for found in discovery.objects
        ),
    )


NOT_CHOOSABLE = (
    "Some of these objects can't be chosen: they have no read access, have never been populated,"
    " or are no longer in the database. Refresh and choose again."
)
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

    def database(source_id: str) -> StoredPostgresSource:
        source = find_source(workspace.engine, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="There's no such source.")
        if not isinstance(source, StoredPostgresSource):
            raise HTTPException(status_code=409, detail="A file has no schemas or tables.")
        return source

    def with_password(source_id: str) -> StoredPostgresSource:
        source = find_source(workspace.engine, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="There's no such source.")
        if not isinstance(source, StoredPostgresSource):
            raise HTTPException(status_code=409, detail="A file has no password.")
        return source

    def failed(source_id: str, known: Kept, failure: ConnectFailure) -> HTTPException:
        """A read with the kept or stored password failed. If the server refused the password,
        the source asks for it again (AC-06); an unreachable server leaves it as it is."""
        if failure.reason == "credentials":
            kept.refused(source_id, known)
        return HTTPException(status_code=422, detail=str(failure))

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
        else:
            # A new source lists its objects straight away (AC-08). If that fails, the source is
            # still saved, as the connection works, and the screen offers to list them.
            try:
                discovery = discover(details, password, workspace.temp_directory)
            except ConnectFailure:
                pass
            else:
                _save_discovery(workspace, source.id, discovery)
        kept.keep(source, password, facts)
        return postgres_response(source, kept)

    @router.post("/{source_id}/password")
    def give_password(source_id: str, body: PasswordRequest) -> PostgresSourceResponse:
        """After a restart or a refusal: kept only if the server takes it (AC-05, AC-06)."""
        source = with_password(source_id)
        password = body.password.get_secret_value()
        kept.keep(source, password, _connected(_details_of(source), password, workspace))
        return postgres_response(source, kept)

    @router.post("/{source_id}/connect")
    def connect(source_id: str) -> PostgresSourceResponse:
        """On opening a source: the server's version and encryption, with the password kept or
        stored, so a password the server now refuses is found then (AC-04, AC-06)."""
        source = with_password(source_id)
        known = kept.get(source)
        if known is None:
            raise HTTPException(
                status_code=409, detail="Enter the password for this database first."
            )
        try:
            facts = check(_details_of(source), known.password, workspace.temp_directory)
        except ConnectFailure as failure:
            raise failed(source_id, known, failure) from None
        kept.connected(source_id, known, facts)
        return postgres_response(source, kept)

    @router.post("/{source_id}/discover")
    def refresh_objects(source_id: str) -> ObjectsResponse:
        """List the database's objects as they are now (AC-08, AC-13)."""
        source = database(source_id)
        known = kept.get(source)
        if known is None:
            # After a restart, until the user gives it again (AC-05).
            raise HTTPException(
                status_code=409, detail="Enter the password for this database first."
            )
        try:
            discovery = discover(_details_of(source), known.password, workspace.temp_directory)
        except ConnectFailure as failure:
            raise failed(source_id, known, failure) from None
        _save_discovery(workspace, source_id, discovery)
        return objects_response(read_objects(workspace.engine, source_id))

    @router.get("/{source_id}/objects")
    def get_objects(source_id: str) -> ObjectsResponse:
        """From the workspace only, so it works after a restart, before the password."""
        database(source_id)
        return objects_response(read_objects(workspace.engine, source_id))

    @router.put("/{source_id}/selection")
    def save_chosen_objects(source_id: str, body: SelectionRequest) -> ObjectsResponse:
        """Choose exactly these objects to profile, the others not (AC-11)."""
        database(source_id)
        try:
            save_selection(workspace.engine, source_id, frozenset(body.object_ids))
        except NotChoosable:
            raise HTTPException(status_code=422, detail=NOT_CHOOSABLE) from None
        return objects_response(read_objects(workspace.engine, source_id))

    @router.get("/{source_id}/objects/{object_id}")
    def get_object(source_id: str, object_id: str) -> ObjectResponse:
        """An object's columns and declared keys, as the last discovery found them (AC-09)."""
        database(source_id)
        found = read_object(workspace.engine, source_id, object_id)
        if found is None:
            # Gone in a refresh, or another source's.
            raise HTTPException(
                status_code=404, detail="There's no such object. Refresh the list."
            )
        return object_response(*found)

    return router
