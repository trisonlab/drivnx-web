"""The OS credential store that keeps a database's password (DRIVN-45 AC-04, ADR 0008)."""

import keyring
from artifacts.sources import StoredPostgresSource
from keyring.backend import KeyringBackend
from keyring.backends import SecretService, Windows, macOS
from keyring.backends.chainer import ChainerBackend

# Keychain, Windows Credential Manager and Secret Service. Not a keyring PYTHON_KEYRING_BACKEND or
# keyringrc.cfg chooses instead, such as keyrings.alt's plaintext file or the null keyring.
_OS_STORES = (macOS.Keyring, Windows.WinVaultKeyring, SecretService.Keyring)


def os_credential_store() -> KeyringBackend | None:
    """This computer's credential store, or None to keep passwords in memory only (AC-05)."""
    store = keyring.get_keyring()
    # A chain reads and writes each of its keyrings in turn, so every one must be an OS store.
    links = store.backends if isinstance(store, ChainerBackend) else [store]
    if not links or not all(isinstance(link, _OS_STORES) for link in links):
        return None
    for link in links:
        if isinstance(link, Windows.WinVaultKeyring):
            # On this computer only: the default also roams to the user's other computers.
            link.persist = "local_machine"
    return store


def entry(source: StoredPostgresSource) -> tuple[str, str]:
    """The store's service and user name for a source's password: DrivnX and the source (AC-04).

    The source's id as the user name gives each source its own entry, also when two workspaces
    hold the same connection.
    """
    where = f"{source.user}@{source.host}:{source.port}/{source.database}"
    return (f"DrivnX PostgreSQL {where}", source.id)
