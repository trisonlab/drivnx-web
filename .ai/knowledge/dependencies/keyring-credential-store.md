---
id: keyring-credential-store
kind: dependency-research
library: keyring
version: "25.7.0"
retrieved: "2026-11-14"
status: INFERRED
related: [DRIVN-45, DRIVN-48]
---

# keyring 25.7.0: reaching Keychain, Windows Credential Manager and Secret Service

Researched for DRIVN-48 (DRIVN-45 AC-04, AC-06, AC-30) on 2026-11-14. External documentation and source,
not project evidence: INFERRED until the build proves each point on CI. keyring is **not** a dependency yet
(`pyproject.toml`, `uv.lock` checked 2026-11-14); adding it is the user's decision.

## Package
- Latest release 25.7.0, 2025-11-16; MIT; `requires_python >=3.9`. — https://pypi.org/project/keyring/#history,
  https://pypi.org/pypi/keyring/json
- Runtime dependencies: `jaraco.classes`, `jaraco.functools`, `jaraco.context` (all MIT; the first two need
  `more-itertools`); Linux only `SecretStorage>=3.2` (3.5.0, BSD-3-Clause, needs `cryptography>=2.0` and
  `jeepney>=0.6`) and `jeepney>=0.4.2`; Windows only `pywin32-ctypes>=0.2.0`. macOS needs nothing more (ctypes).
  — https://pypi.org/pypi/keyring/json, https://pypi.org/pypi/SecretStorage/json,
  https://pypi.org/pypi/jaraco.functools/json, https://pypi.org/pypi/jaraco.classes/json,
  https://pypi.org/pypi/jaraco.context/json
- None of these are in `uv.lock` today, `cryptography` included.

## Choosing a backend (keyring/core.py, keyring/backend.py at v25.7.0)
- `get_keyring()` initialises once per process and caches the backend globally. Order: the
  `PYTHON_KEYRING_BACKEND` environment variable, then `keyringrc.cfg` in the config root, then the viable backend
  with the highest `priority`, else `fail.Keyring()`.
  — https://github.com/jaraco/keyring/blob/v25.7.0/keyring/core.py
- The `limit` callable of `init_backend(limit)` filters only the automatic choice; the environment variable and the
  config file bypass it. So an application that must refuse some backends has to check the backend it gets.
- A backend is viable when its `priority` class property doesn't raise. — keyring/backend.py
- When more than one backend is viable, `ChainerBackend` (priority 10) wins and tries each in priority order; its
  `backends` property lists them. — keyring/backends/chainer.py
- `fail.Keyring` (priority 0) raises `NoKeyringError` on get, set and delete; `null.Keyring` (priority -1) silently
  returns `None` and stores nothing. — keyring/backends/fail.py, keyring/backends/null.py
- README: "keyrings.alt" holds "alternate, possibly-insecure backends", opt-in only (plaintext files among them).
  — https://github.com/jaraco/keyring/blob/v25.7.0/README.rst

## The three OS stores
- **macOS** (`keyring.backends.macOS.Keyring`, priority 5, raises "macOS required" elsewhere): generic password
  keyed by service and account through the Security framework via ctypes. `set_password` deletes an existing item
  then adds (`SecItemAdd`); no access-control attribute is set. Errors: denied → `KeyringLocked`, other set errors →
  `PasswordSetError`, get errors → `KeyringError`. README: any script run by the same Python executable can read
  items keyring created with it. — keyring/backends/macOS/__init__.py, keyring/backends/macOS/api.py
- **Windows** (`keyring.backends.Windows.WinVaultKeyring`, priority 5, needs `pywin32-ctypes`): a
  `CRED_TYPE_GENERIC` credential whose `TargetName` is the service; a second user name under the same service moves
  to `"{username}@{service}"`. Persist defaults to `CRED_PERSIST_ENTERPRISE` (roams with a roaming profile), and the
  instance's `persist` can be changed. The blob is written UTF-16. Read and delete treat error 1168 as not found;
  other `pywintypes.error`s are re-raised. — keyring/backends/Windows.py
- Windows limits: `CredentialBlobSize` at most `CRED_MAX_CREDENTIAL_BLOB_SIZE` = 5*512 = 2,560 bytes, so at most
  1,280 UTF-16 code units; `CRED_PERSIST_LOCAL_MACHINE` keeps a credential on this computer only, `ENTERPRISE` also
  roams to the user's other computers. — https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw
- **Secret Service** (`keyring.backends.SecretService.Keyring`, priority 5): not viable without SecretStorage or when
  no Secret Service answers on the D-Bus session bus. Items carry `service`, `username` and
  `application="Python keyring library"`, label `Password for '<username>' on '<service>'`, written with
  `replace=True`. A locked collection is unlocked with a prompt; failure raises `KeyringLocked`.
  — keyring/backends/SecretService.py
- README: Secret Service works headless with only D-Bus (`dbus-run-session` and `gnome-keyring-daemon --unlock`).

## CI
- keyring's own workflow tests on ubuntu-latest, macos-latest and windows-latest with no keychain, D-Bus or
  gnome-keyring setup; its macOS and Windows test classes run `BackendBasicTests` (set, get, delete) and skip only
  when the backend isn't viable. INFERRED: GitHub's macOS and Windows runners have a working login keychain and
  Credential Manager; an Ubuntu runner has no Secret Service, so keyring falls back to `fail.Keyring` there.
  — https://github.com/jaraco/keyring/blob/v25.7.0/.github/workflows/main.yml,
  tests/backends/test_macOS.py, tests/backends/test_Windows.py

## Open
- Whether macOS's Security framework still finds the login keychain when `HOME` points elsewhere, as
  `tests/conftest.py`'s `start_drivnx` sets it. Unverified; check on the macOS runner.
- Which exception a too-large Windows blob raises through `set_password` (likely `pywintypes.error`, not a
  `KeyringError`). Unverified; check on the Windows runner.
- Whether `WinVaultKeyring().persist = "local_machine"` is the accepted spelling. Unverified.
