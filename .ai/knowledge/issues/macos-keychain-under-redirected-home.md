---
id: macos-keychain-under-redirected-home
kind: issue
status: CONFIRMED
found: "2026-11-16"
related: [DRIVN-48, CFX-APP-postgres, ADR-0008]
evidence:
  - tests/conftest.py
  - tests/integration/test_credential_store.py
  - docs/adr/0008-postgres-password-in-the-os-credential-store.md
---

# On macOS, Keychain hangs for a process whose HOME has no login keychain

**Symptom.** On macOS CI (PR #22, run 43782467060), `POST /api/sources/postgres` timed out in the two tests that
start the installed `drivnx` with this computer's store (`test_a_restart_reads_the_password_from_the_os_store`,
`test_no_part_of_a_stored_password_reaches_the_workspace_or_the_most_detailed_logs`), and `drivnx` then didn't stop
within 10 s of SIGTERM. The in-process Keychain tests in `test_credential_store.py` passed.

**Cause.** `start_drivnx` (`tests/conftest.py`) gives every DrivnX an empty `HOME`, so its default workspace is never
the user's. macOS finds the login keychain through `HOME`; with none, `SecKeychainCopyDefault` fails and the first
write waits on a SecurityAgent dialog, which nobody answers on a CI runner. A probe on a macOS runner (run
43793755378) showed it: the same write took 0.30 s in a new process with the real `HOME`, and hung with
`SecurityAgent` running under an empty one. Linux (Secret Service over D-Bus) and Windows (Credential Manager) don't
use `HOME` for this.

**Why SIGTERM didn't stop it.** uvicorn's graceful shutdown waits for the request in flight, whose worker thread is
still inside the store call (stack dump with a blocking store on Linux). A consequence of the hang, not a second bug.

**Fix.** `RealStore.env` holds the user's own `HOME`; every test that starts DrivnX with this computer's store passes
it and an explicit workspace. Any new test that does so must pass `env=store.env`, or it will hang on macOS.
