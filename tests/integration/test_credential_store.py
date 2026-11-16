"""The OS credential store a database's password is kept in, CFX-APP-postgres AC-04 and AC-06
(DRIVN-48)."""

import secrets
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import keyring
import pytest
from artifacts.sources import StoredPostgresSource
from keyring.backend import KeyringBackend
from keyring.backends import SecretService, Windows, macOS, null
from keyring.backends.chainer import ChainerBackend
from keyring.errors import PasswordDeleteError
from local_api.credentials import entry, os_credential_store

# For `start_drivnx(env=NO_STORE)`: DrivnX without a credential store, keeping a password until it
# stops (AC-05). Every test that starts DrivnX with a database passes it, unless it tests the real
# store with `real_store`: it would otherwise read and write the user's own store.
NO_STORE = {"PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}

# Windows' store can only be set up on Windows: its `persist` goes through Windows' own API.
ON_WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows' API is needed")
OS_STORES = [
    macOS.Keyring,
    SecretService.Keyring,
    pytest.param(Windows.WinVaultKeyring, marks=ON_WINDOWS_ONLY),
]


class MemoryStore(KeyringBackend):
    """A keyring in a dict. Chosen as this process's keyring, it stands for keyrings.alt's
    plaintext file, which isn't an OS credential store; given to the app directly, it stands for
    one (tests/integration/test_postgres_password_store.py)."""

    priority = 1  # type: ignore[assignment]
    # keyring registers every subclass; without this it would chain this one with the real store.
    viable = False

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.passwords[(service, username)]


@pytest.fixture
def chosen_keyring() -> Iterator[None]:
    """Puts back the keyring this process had, after a test chose another."""
    before = keyring.get_keyring()
    yield
    keyring.set_keyring(before)


@dataclass
class RealStore:
    """This computer's credential store, and the entries a test wrote in it."""

    store: KeyringBackend
    written: list[tuple[str, str]] = field(default_factory=list)
    # For `start_drivnx(env=...)`, which otherwise gives DrivnX an empty home directory: macOS finds
    # the login keychain through HOME, and with none waits on a dialog nobody can answer on CI.
    env: dict[str, str] = field(default_factory=lambda: {"HOME": str(Path.home())})

    def set(self, service: str, username: str, password: str) -> None:
        self.written.append((service, username))
        self.store.set_password(service, username, password)


@pytest.fixture
def this_computers_store() -> Iterator[RealStore | None]:
    """Keychain, Credential Manager or Secret Service if this computer has one, else None. Every
    entry the test wrote is deleted afterwards, as it's the user's own store."""
    store = os_credential_store()
    if store is None:
        yield None
        return
    real = RealStore(store)
    yield real
    for service, username in real.written:
        try:
            store.delete_password(service, username)
        except PasswordDeleteError:
            pass


@pytest.fixture
def real_store(this_computers_store: RealStore | None) -> RealStore:
    """This computer's store, which a test needs. CI's macOS and Windows runners have one, so there
    a missing store fails the test; Linux without a Secret Service skips it."""
    if this_computers_store is None:
        if sys.platform in ("darwin", "win32"):
            pytest.fail(f"No credential store on {sys.platform}, where CI must prove it")
        pytest.skip("No Secret Service on this machine")
    return this_computers_store


@pytest.mark.parametrize("chosen", [null.Keyring, MemoryStore])
def test_a_plaintext_or_null_keyring_is_not_a_credential_store(
    chosen: type[KeyringBackend], chosen_keyring: None
) -> None:
    # CFX-APP-postgres/AC-04: only Keychain, Credential Manager or Secret Service, even when
    # PYTHON_KEYRING_BACKEND or keyringrc.cfg chooses another keyring.
    keyring.set_keyring(chosen())

    assert os_credential_store() is None


@pytest.mark.parametrize("chosen", OS_STORES)
def test_keychain_credential_manager_and_secret_service_are_credential_stores(
    chosen: type[KeyringBackend], chosen_keyring: None
) -> None:
    # CFX-APP-postgres/AC-04
    store = chosen()
    keyring.set_keyring(store)

    assert os_credential_store() is store


def chain_of(*links: KeyringBackend) -> ChainerBackend:
    """The keyring that keyring chooses when several are usable, with these links."""
    return type("Chain", (ChainerBackend,), {"backends": list(links), "viable": False})()


@pytest.mark.parametrize(
    ("links", "is_a_store"),
    [
        ((SecretService.Keyring(),), True),
        ((SecretService.Keyring(), macOS.Keyring()), True),
        ((SecretService.Keyring(), MemoryStore()), False),
        ((), False),
    ],
    ids=["an OS store", "OS stores only", "with a plaintext file", "empty"],
)
def test_a_chain_is_a_store_only_when_every_link_is_an_os_store(
    links: tuple[KeyringBackend, ...], is_a_store: bool, chosen_keyring: None
) -> None:
    # CFX-APP-postgres/AC-04: a chain reads every link in turn, so a plaintext file in it would
    # hold or answer for the password.
    chain = chain_of(*links)
    keyring.set_keyring(chain)

    assert (os_credential_store() is chain) is is_a_store


def test_the_entry_names_drivnx_and_the_source() -> None:
    # CFX-APP-postgres/AC-04: "under an entry naming DrivnX and the source", found again in
    # Keychain Access, Credential Manager or Seahorse; one entry per source, also for two
    # workspaces with the same connection.
    source = StoredPostgresSource(
        id="5d0f7c1e-8a2b-4c3d-9e4f-0a1b2c3d4e5f",
        host="db.example.com",
        port=5433,
        database="sales",
        user="analyst",
        tls_mode="require",
        ca_path=None,
    )

    assert entry(source) == (
        "DrivnX PostgreSQL analyst@db.example.com:5433/sales",
        "5d0f7c1e-8a2b-4c3d-9e4f-0a1b2c3d4e5f",
    )


def test_the_os_store_keeps_and_replaces_a_password(real_store: RealStore) -> None:
    # CFX-APP-postgres/AC-04 (kept and read again) and AC-06 (a new one replaces it).
    service = f"DrivnX PostgreSQL test {uuid.uuid4()}"
    first, second = secrets.token_urlsafe(18), secrets.token_urlsafe(18)

    real_store.set(service, "source-id", first)
    assert real_store.store.get_password(service, "source-id") == first
    real_store.set(service, "source-id", second)

    assert real_store.store.get_password(service, "source-id") == second


@ON_WINDOWS_ONLY
def test_on_windows_the_password_stays_on_this_computer(chosen_keyring: None) -> None:
    # CFX-APP-postgres/AC-30: Credential Manager's default lets a roaming profile copy it to the
    # user's other computers.
    from win32ctypes.pywin32 import win32cred

    keyring.set_keyring(Windows.WinVaultKeyring())

    store = os_credential_store()

    assert isinstance(store, Windows.WinVaultKeyring)
    assert store.persist == win32cred.CRED_PERSIST_LOCAL_MACHINE
