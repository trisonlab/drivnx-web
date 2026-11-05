# 0002 — Workspace storage: SQLite only; files exist only as explicit exports

- **Status:** Accepted 2026-11-01, amended 2026-11-01, 2026-11-03 and 2026-11-04
- **Date:** 2026-10-31
- **Source:** Direction doc §3 (artifacts, trust model), §4 (Figure 1, component table, stack), §5 (files referenced
  in place), §7 (workspace threat), §11/§12 (open decisions), §12 (domain model, trust-state rules); Review Notes #4a/#4b;
  ADR 0001 (one runtime per workspace)

## Context

The workspace has to hold project state and the three artifacts: the Data Profile (an immutable snapshot per run), and
the Data Dictionary and Data Contract (both editable and versioned). It also holds their revisions, trust states and
provenance. Definition of done (§2): "Create and reopen a local workspace with provenance intact."

The question still open is whether SQLite holds everything, or whether artifacts also live as separate files
(JSON/YAML) next to it.

What matters for this decision:

- **Integrity of state and provenance.** Every trust-state change, edit, promotion and demotion must be saved
  together with its ProvenanceEvent (§12). Demoting a Defined item must remove its ContractRule from the contract in
  the same step.
  A half-applied transition breaks the product's main promise.
- **Reopen intact.** A crash or power loss must not leave the workspace inconsistent.
- **No copies of source data, no secrets** (§5, §7). The "workspace copied or stolen" threat is handled by owner-only
  permissions.
- **Readable, shareable files** are the Export service's job: JSON, YAML, Markdown, CSV, and ODCS for contracts.
- **Simplicity:** modular monolith, stack already chosen (SQLite + SQLAlchemy Core / Alembic), and one runtime per
  workspace (ADR 0001 lock file).

## Decision

**1. One SQLite database per workspace holds all workspace state.** That covers workspace settings, sources (a
connection reference only), datasets and fields, profile runs and their observations, findings, dictionary entries
and their revisions, contracts and rules, and provenance events. Conflux writes no artifact files of its own.

```text
<workspace>/                    directory mode 0700
  .conflux.lock                 ADR 0001: one runtime per workspace
  workspace.db                  file mode 0600 (SQLite gives -wal/-shm the same mode)
  workspace.db.bak-<schema-rev> pre-migration backups, mode 0600 (point 6)
  duckdb-tmp/                   DuckDB spill files; throwaway, emptied at startup (point 7)
```

- **Permissions are set by Conflux, not left to the umask.** SQLite creates a new database file using the process
  umask (usually 0644), so the runtime creates `workspace.db` itself with mode 0600 before SQLite opens it. Backup
  files are created with mode 0600 before they are written. Every open also sets the directory to 0700 and Conflux's own
  files to 0600, because a copied workspace can arrive with looser permissions. Other files in the directory are left alone.
- **Conflux only uses a directory that is new, empty or already a workspace.** `conflux ~/Documents` must not chmod
  the user's Documents folder to 0700 or scatter files into it. A non-empty directory without `workspace.db` is
  refused with a message suggesting a subdirectory. This check runs before the lock file is created (ADR 0001). A
  directory holding only `.conflux.lock` counts as empty, so a crash on first launch doesn't block the next one.
- **Windows:** no ACL code in Phase 1. Owner-only access relies on the workspace being under the user's profile,
  which is the default location. The threat register says so.

**2. Files appear only when the user exports.** The Export service writes to a location the user picks. Exported
files are copies made at that moment, not something Conflux keeps in sync. Conflux never reads them back as state.
Importing them is out of scope for Phase 1.

**3. Transactions follow domain operations.** Each artifact-service operation (accept, edit, promote, demote, reject,
defer) runs in a single SQLite transaction. Its ProvenanceEvent and any rows it creates go in that same transaction.
The domain layer decides whether a transition is allowed, and the store makes sure the whole operation is written or
none of it is.

- **Write transactions start with `BEGIN IMMEDIATE`.** Every transition reads the current state, checks it, then
  writes. A deferred transaction takes the write lock only at its first write. If another connection committed in
  between, SQLite returns `SQLITE_BUSY` at once in WAL mode, and `busy_timeout` doesn't help. Taking the write
  lock at the start also closes the check-then-act race: two clicks on Accept can't both pass the check. Python's
  `sqlite3` driver and SQLAlchemy don't emit `BEGIN IMMEDIATE` by default, so the store sets it up explicitly
  (confirmed on the pinned versions by `test_a_write_transaction_holds_the_write_lock_before_its_first_write`).
- **A profile run is a snapshot, not a single transaction.** A run over many tables can take minutes and is
  cancellable (§5). Holding the write lock that long would block every review action behind `busy_timeout`. So:
  1. Starting a run inserts the ProfileRun row with status `running`, in its own transaction.
  2. Observations are committed per relation in short transactions of at most 1,000 observations, which held the
     write lock for 0.2 to 0.5 s when measured. A single transaction of 40,000 ran a waiting review out of time. A relation with more
     than 1,000 observations takes several transactions.
  3. The run ends as `completed`, `cancelled` or `failed`, also in its own transaction.
  4. At startup, any run still `running` is marked `interrupted`. No process can still be writing it, because of
     the ADR 0001 lock.

  Findings, the overview and "needs re-review" checks read only `completed` runs. Partial runs stay visible in
  history with their status, so they're never shown as complete.

**4. History is append-only.**
- Observations are written once and never updated, because the Profile is an immutable snapshot per run. A ProfileRun's
  scope, mode and other settings are also written once. Only its status and end time change, once, from `running` to a
  final status (point 3). This also gives the "History = previous profile runs" view (§9).
- Dictionary entries and contract rules keep their revisions as new rows. The current version is the latest revision.
- ProvenanceEvent rows are only ever inserted.
- **Rules never lose rows.** Demoting a Defined rule writes a new revision in state Confirmed (ADR 0003). Deleting a rule
  outright writes a tombstone revision. A row delete would erase the rule's history. Earlier ProvenanceEvents point at
  those revisions too, so with `foreign_keys = ON` a delete would either fail or cascade into provenance. The contract's
  current rules are the rules whose latest revision is Defined and not a tombstone. A Defined rule leaving the contract
  creates a new contract version.

The application enforces these rules. Tests check that the artifact service never updates or deletes these rows.

**5. Connection settings.** Every connection runs `PRAGMA foreign_keys = ON` and `journal_mode = WAL`, so UI reads
don't block on a profiling job's writes. It also runs `synchronous = FULL`: losing a confirmed review on power loss
is worse than the small write cost. A `busy_timeout` handles the API thread pool and the job runner writing at the
same time inside the one process. That only works because every write transaction stays short (point 3).

**Writers also queue in the process, because `busy_timeout` isn't fair.** SQLite's waiters poll, and building T07 showed a
review running out the whole timeout behind a profile run that kept writing short transactions (`database is locked`, in 4
of 12 runs when last measured, and the rate varies). ADR 0001 means every writer is in one process, so writers take turns
there in the order they arrived. A review then waits only for the writers ahead of it.

A write can wait in three places, each limited to the `busy_timeout` value of 5 seconds, and each one that runs out gives
the same `WorkspaceBusy` error instead of hanging: its turn in this queue, a connection from the pool, and SQLite's write
lock. The limits count separately, so a write could wait about 15 seconds before it gives up, if all three were slow. SQLite's
own wait stays as the backstop for anything outside the queue.

**WAL needs a local disk.** WAL relies on shared memory, so it isn't safe on network filesystems. A live database in
a sync folder (OneDrive, Dropbox, iCloud Drive) can also be copied mid-write. On Windows, Documents is often
redirected to OneDrive. The runtime warns when the workspace path is under a known sync folder, and the docs tell
users to keep workspaces on a local disk. Detecting network filesystems reliably is not attempted in Phase 1.

**6. Migrations.** The schema is managed with Alembic.
- Before migrating, the runtime makes a copy of the database with SQLite's online backup API
  (`workspace.db.bak-<schema-rev>`).
- The runtime refuses to open a workspace whose schema is newer than its own code, so it never downgrades data
  silently.
- Alembic runs in batch mode (`render_as_batch=True`), because SQLite can't alter most column and constraint
  definitions in place.
- **The migration connection is the one exception to `foreign_keys = ON` (point 5).** Batch mode rebuilds a table and
  drops the old one, and with foreign keys on, that `DROP TABLE` deletes or blocks on the rows that reference it. A test
  on SQLite 3.53.4 lost a child row under `ON DELETE CASCADE` and raised `IntegrityError` without it. So the runtime turns
  foreign keys off before the transaction starts (the pragma does nothing inside one), runs the migration in an explicit
  transaction, and runs `PRAGMA foreign_key_check` before committing. Any violation rolls it back. Alembic and the Python
  driver need explicit setup for transactional DDL on SQLite (INFERRED; confirm against the pinned versions in M1).
- Backups are full copies, including any source-derived values (see Consequences). Only the last three are kept.
- **Checking the schema revision must not use a read-only connection.** After a crash SQLite has to write to recover, so a
  read-only open fails with a misleading error every time. The check uses a read-write connection and only runs `SELECT`s.

**7. DuckDB is not a store.** Profiling uses an in-memory DuckDB per job. It reads sources where they are and writes
its results into SQLite through the artifact service, so the workspace has no second database file.

**DuckDB's spill files stay inside the workspace.** A large sort or join can spill to disk, and those files would hold
source data. By default an in-memory DuckDB uses `.tmp` under the current directory (seen on DuckDB 2.0.0, where the
directory got mode 0755). So every job sets `temp_directory` to `<workspace>/duckdb-tmp/`, under the 0700 workspace
directory. The runtime empties it at startup, which clears anything left by a crash, and the ADR 0001 lock means no job
can still be using it.

**Observations are evidence, so they are always stored. This constrains the profile-caching ADR.** Findings and
provenance point at observations ("Profile run #42 / `customer.customer_id`"). A re-run, sampled or on changed data,
can give different numbers, so recomputing would break those references. The profile-caching ADR therefore only
decides about bulky derived data (histograms, distributions) and reusing results between runs. It can't make
observations recompute-only.

## Alternatives considered

| Option | Why not |
|---|---|
| SQLite + artifact files (JSON/YAML per artifact) | Writes would go to two places. A crash between the SQLite commit and the file write leaves state and file disagreeing, and you can't put a file write and a database row in one transaction. It also creates a second format alongside the exports, without a clear answer to which one wins. The benefits people expect from it (readable, diffable, shareable) are already covered by exports. |
| Files only (JSON/YAML, no database) | Same atomicity problem, worse. We'd also have to hand-build locking, queries (for the review inbox, findings and history) and migrations. |
| A persistent DuckDB file as the single store | Built for analytics, not for many small transactional writes. Alembic and SQLAlchemy support are weaker. It would also tie workspace state to the profiling engine's file format and its upgrades. |
| Bulky profile results (histograms, distributions) as Parquet files next to the database | Not needed yet, and whether to persist computed profile results at all belongs to the profile-caching ADR. If spike 1 shows SQLite rows are too slow or too big, that ADR can add a cache directory. The cache would be throwaway data that can always be recomputed, never state. |

## Consequences

- **Easier:**
  - All-or-nothing trust transitions come straight from SQLite transactions.
  - Backing up, moving or deleting a workspace means handling one directory. Sources stay where they are (§5), so
    a workspace moved to another machine keeps its artifacts, but its file sources need re-pointing before re-profiling.
  - Tests use a temporary database per test.
  - The "never in SQLite" check from §10 has a single place to scan.
- **Harder or accepted:**
  - Users can't read or diff the workspace without exporting it.
  - Multi-user or shared workspaces would need a different store. That is out of scope for Phase 1.
- **Finding to resolve, not decided here:** some observations and dictionary fields hold **values taken from the
  source data**: min/max, most frequent values, pattern examples, and `DictionaryEntry.examples`. That is a partial
  copy of source data, which the §7 threat model ("raw data not copied by default") doesn't account for. These
  values most likely include personal data. This needs a rule before M3:
  - store them only for fields not flagged as sensitive, or mask them;
  - keep a limit on how many values are stored;
  - update the claim in §7 to match.
  The rule also has to cover the pre-migration backups, and old values left in SQLite free pages and the WAL
  (`PRAGMA secure_delete`, or a `VACUUM` after masking). Raise it with the domain model and security review.
  **Resolved for profiling by [ADR 0004](0004-profile-values-and-caching.md), 2026-11-05.** `DictionaryEntry.examples` is
  due with the dictionary slice.
- **Tests that ship with M1:**
  - A failure halfway through a transition leaves neither the state change nor its ProvenanceEvent behind.
  - Demoting a Defined item writes a Confirmed revision of its ContractRule in the same transaction. The rule drops out
    of the current contract, and its history and provenance stay intact.
  - Deleting a rule writes a tombstone revision and removes no rows.
  - The artifact service never updates or deletes observations, revisions or provenance rows.
  - Two concurrent Accepts on the same item: one succeeds and one is rejected by the transition check. Neither
    gets `SQLITE_BUSY`.
  - A review action finishes within `busy_timeout` while a profile run is writing observations.
  - A run left `running` is marked `interrupted` on the next start, and findings ignore it.
  - A batch migration of a table with referencing rows keeps those rows, and a foreign-key violation rolls it back.
  - DuckDB jobs create nothing in the current directory, and leftovers in `duckdb-tmp/` are removed at startup.
  - The workspace is created with 0700/0600 permissions, backups included (POSIX).
  - A non-empty directory that isn't a workspace is refused, gets no lock file, and its permissions are left alone.
  - A workspace with a recovery journal left by a crash opens, with its committed data intact.
  - A workspace copied in with loose permissions is tightened when it opens.
  - A directory holding only `.conflux.lock` is accepted as new.
  - Opening a database whose schema is newer than the code is refused.
  - No credential-shaped values end up in `workspace.db`.
- **Revisit when:** users need to share or version-control a workspace itself rather than its exports, or spike 1
  shows profile results don't fit well in SQLite.
