# 0008 — A PostgreSQL password is kept in the OS credential store through keyring, and in memory only where there is none

- **Status:** Accepted 2026-11-15
- **Date:** 2026-11-15
- **Source:** ticket DRIVN-48, approved by the user in chat on 2026-11-15; spec `CFX-APP-postgres` revision 2
  (AC-04, AC-05, AC-06, AC-30, the "No OS credential store" assumption and the Security limit); amends ADR 0007
  Decision 1

## Context

ADR 0007 kept a database's password in two places only: `KeptPasswords`, a dictionary in the API process's memory,
and a temporary DuckDB secret for the one job that connects. After a restart every database source asked for its
password again (AC-05). The spec wants it kept in Keychain, Windows Credential Manager or Secret Service under an entry
naming DrivnX and the source, read from there after a restart (AC-04), asked for again only when the server refuses it
(AC-06), and nowhere else: "No secret is saved outside the OS credential store" (Security). Where there is no store, it
stays in memory until DrivnX stops and the screen says so (AC-05). The spec names `keyring` as the new dependency.

keyring picks a backend once per process: `PYTHON_KEYRING_BACKEND`, then `keyringrc.cfg`, then the most suitable
installed one, which may be a chain of several. Either setting can choose a backend that isn't an OS store, such as
keyrings.alt's plaintext file or the null keyring, which stores nothing and says nothing
(`.ai/knowledge/dependencies/keyring-credential-store.md`).

## Decision

**1. keyring reaches the store, behind `KeptPasswords`.** `local_api/credentials.py` chooses the store once, when
`drivnx` starts (`os_credential_store()` in `cli.py`), and names each entry. `KeptPasswords` (`postgres_api.py`) is
still the only thing the routes ask for a password; it now also writes it to the store and reads it back. A test or a
second app passes its own store, or `None`, to `create_app`.

**2. Only Keychain, Credential Manager or Secret Service count as a store.** The backend keyring chose must be
`macOS.Keyring`, `Windows.WinVaultKeyring` or `SecretService.Keyring`, and for a chain every link must be one, since a
chain reads and writes each link in turn. Anything else, including keyring's `fail.Keyring` where no store answers,
means no store: the password is kept in memory only and the screen says it lasts until DrivnX stops (AC-05).

**3. One entry per source.** The service is `DrivnX PostgreSQL <user>@<host>:<port>/<database>`, so a person finds it
in Keychain Access, Credential Manager or Seahorse. The account is the source's id, so two workspaces with the same
connection don't share one.

**4. On Windows the entry stays on this computer** (`persist = local_machine`). Credential Manager's default,
`CRED_PERSIST_ENTERPRISE`, lets a roaming profile copy it to the user's other computers.

**5. The store is written only with a password the server took.** Saving a connection and giving a password both
connect first, as ADR 0007 Decision 5 requires, and only then write the store. A wrong password given again leaves the
stored one as it was (AC-06).

**6. Memory first, the store once.** After a restart, the first time a source's password is needed it's read from the
store into `KeptPasswords`, outside its lock, since the store may wait for the user to unlock it. Later reads come from
memory, so the store isn't asked, and can't prompt, on every request. A read that finds no entry isn't tried again
for that source in this run: it asks for the password, as with no store, until one is given. A read that fails (the
user dismissed the unlock prompt) stops reading the store for every source in this run, since a locked store prompts on
each read; a password given is still written to it.

**7. The server is asked when a source is opened.** The UI calls `POST /api/sources/{id}/connect` when it opens a
source that has a password but no connection in this run, and shows the server's version and encryption. The password
the server took is left where it is, not written to the store again. A refresh of the object list that reaches the
server asks again too, so a server that didn't answer on opening no longer says so. When the server refuses the
password there, or on a refresh, `KeptPasswords` forgets it, unless another password the server took replaced it
meanwhile, and won't read the store again in this run. The UI then reloads the sources, the source asks for the
password, and the prompt gives the server's reason (AC-06). A server that doesn't answer leaves the password as it is,
since it may answer later with the same one.

**8. A store that fails is the same as no store.** Any error from the store counts, not only a `KeyringError`: keyring
raises none for Windows' `pywintypes.error` or a dismissed Secret Service prompt. A failed write (locked, the prompt
dismissed, too long) keeps the password in memory and the response says `until_stop`; a failed read asks for the
password (Decision 6). Only the error's type is logged; the store's own message is never logged or shown.

**9. Tests never touch the user's store unless they mean to.** Every test that starts `drivnx` with a database passes
`PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring` (`NO_STORE`), except those that test this computer's own store.
Those clean up every entry they wrote, also when they stop midway. On macOS and Windows a missing store fails them, because CI must prove it there;
on Linux with no Secret Service they're skipped.

## Alternatives considered

- **Calling each OS's API ourselves** (Security framework, `CredWrite`, Secret Service over D-Bus): three
  implementations to write and keep working, which keyring already maintains. Rejected; the spec names keyring.
- **Trusting whatever backend keyring chooses:** a plaintext file or the null keyring would keep the password outside
  an OS store, or silently drop it while the screen says it's kept. Rejected by Decision 2.
- **Encrypting the password in SQLite:** the key would have to live somewhere, and the spec says never in SQLite.
  Already rejected in ADR 0007.
- **Reading the store on every request:** each read may prompt or wait for an unlock. Rejected by Decision 6.
- **Checking every stored password when the sources are listed:** one connection per database on each listing, and
  the spec asks "when the source is opened". Rejected by Decision 7.
- **Deleting the stored entry when the server refuses it:** the user would lose it if the server was wrong only for a
  moment, for example while a password rotation is in progress. The entry is replaced only by a password the server
  takes (Decision 5).
- **Failing a save when the store can't keep the password:** the user would be blocked by a locked keyring. Keeping it
  in memory and saying so is what the spec asks where there's no store (AC-05).

## Measured

2026-11-15, Linux with a Secret Service on the session bus:

- `tests/integration/test_credential_store.py` and `tests/integration/test_postgres_password_store.py` pass, including
  the installed `drivnx` reading the password from Secret Service after a restart
  (`test_a_restart_reads_the_password_from_the_os_store`).
- AC-30's scan in the browser (`tests/e2e/test_connect_postgres.py`) finds no part of a planted, a refused and the
  right password in the workspace's files, the logs at trace level, cookies, local and session storage, IndexedDB or
  any URL the browser requested. A password planted in session storage, and one put in a URL, each make it fail.
- Not measured yet: Keychain on macOS and Credential Manager on Windows. Both run only on CI's runners.

2026-11-16, CI on PR #22 (run 43782467060) and a probe on a macOS runner (run 43793755378):

- Windows: every store test passed, including `persist = "local_machine"`.
- macOS: Keychain works with the user's own `HOME`, in the test process and in a new one (written in 0.30 s). Under a
  `HOME` with no login keychain, `security default-keychain` says "A default keychain could not be found" and the
  first write waits on a SecurityAgent dialog that nobody can answer on CI. The tests give every DrivnX started for
  this computer's store the user's own `HOME` (`RealStore.env`); the others still get an empty one.
- A store call that never returns keeps `drivnx` running after SIGTERM: uvicorn's shutdown waits for that request,
  whose worker thread is still in the store (measured on Linux with a store that blocks; an idle `drivnx` stopped in
  0.2 s).

## Consequences

- A password is in a third place, the OS store, until the user deletes the entry. No source can be removed yet, so an
  entry stays after its source is gone (the spec records this limit).
- keyring adds `jaraco.*` and `more-itertools`; on Linux its Secret Service client with `jeepney` and
  `cryptography`; on Windows `pywin32-ctypes` (the full list is in `uv.lock`).
- Any program run by the same Python executable can read an entry keyring made on macOS (keyring's README). Another
  user's programs can't.
- A store that waits for an unlock holds that one request until the user answers.
- After the server refuses a stored password, the wrong entry stays in the store until a password the server takes
  replaces it, so after a restart DrivnX tries it once more and asks again.
- Ubuntu CI has no Secret Service, so there the real-store tests are skipped and DrivnX uses memory; the store's own
  behaviour is proven on the macOS and Windows runners.
- While the store waits for an unlock nobody answers, `drivnx` doesn't stop on SIGTERM either (Measured). Not tested
  with Ctrl+C.
- The real-store tests on macOS need the user's own `HOME`, so they run with it, and pass an explicit workspace so
  nothing is written in the user's home folder.
