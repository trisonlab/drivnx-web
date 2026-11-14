# Threat register — Phase 1

The one-page register required by the direction doc (§7, §12). **Only the first row has code and tests in full; the
input limits row and R3 are built and tested for CSV only, the two PostgreSQL rows in part, and the other mitigations
are still planned.** Update a row when its test ships. The product claims in §7 stay off-limits until then.

## Threats

| Threat | Mitigation | Decided in | Status |
|---|---|---|---|
| Malicious web page calls the local API | Loopback binding; exact Host and Origin checks; per-launch token in an `HttpOnly` cookie; GET routes have no side effects; a Content-Security-Policy stops other pages framing the UI | §7, ADR 0001 | Implemented and tested (T02). First spike 6 pass 2026-11-01, Chromium only |
| Database or API key leaks | OS credential store; masking; never in SQLite, URLs, browser storage, logs or exports | §7, ADR 0007 | Partly implemented for PostgreSQL passwords (DRIVN-47): kept only in memory and a temporary DuckDB secret, never in the connection string or DuckDB's errors; tests scan `workspace.db`, its WAL and the logs at trace level for a planted right and wrong password. Planned: the OS credential store and the scan of browser storage and exports (DRIVN-48, AC-30) |
| DrivnX changes a connected database | Read-only sessions; no writes; bounded queries; cancellation | §7, ADR 0007 | Read-only sessions implemented and tested (DRIVN-47, `CFX-APP-postgres` AC-14): DuckDB attaches read-only and the server starts every transaction read-only, and a write through either alone is refused by the server. Bounded queries and cancellation planned with profiling (ADR 0005) |
| Sensitive data sent to AI unintentionally | AI off by default; context preview; explicit approval; audit record | §6, §7 | Planned (M8) |
| Workspace copied or stolen | Owner-only permissions; no credentials in the workspace; raw data not copied by default | §7, ADR 0002 | Planned; see R3 and R4 |
| Huge or malformed input exhausts resources | Sampling; size warnings; limits; cancellation | §5, §7 | Implemented and tested for CSV (`CFX-APP-csv-profile`): a fast profile reads only the first 100,000 rows, a full profile runs only on request and cancels within 5 s (AC-10), and a file over 100 MB in an encoding that must be decoded in memory is refused (AC-03). No size warnings yet, and the number of columns isn't capped. Planned for XLSX and Parquet (M2) and PostgreSQL (M4) |
| AI produces incorrect documentation | Inferred state; human review | §3, §12 | Planned (M1, M8) |

## Residual risks

| # | Risk | Position | Owner of next step |
|---|---|---|---|
| R1 | **The session cookie reaches other local servers.** Cookies aren't isolated by port, so any server on `127.0.0.1` receives it for paths under `/api` and could replay it. **Confirmed in spike 6** (Chromium, 2026-11-01, and again against the installed wheel on 2026-11-05): a server on another port received the token, and replaying it passed sign-in. The cookie did not reach paths outside `/api`. The same server can also overwrite the cookie, which signs the user out until they restart DrivnX, because the launch code is already used (reproduced in Chromium, T02 review). The launch URL is also passed to the browser as a command-line argument, so other local users may briefly be able to see it; it is single-use, and no attack on this was attempted. | Accepted for Phase 1: a program running as the same user can already read the workspace. Weakest on shared machines. | Revisit at hardening (M9) |
| R2 | **Windows permissions.** No ACL code in Phase 1. Owner-only access relies on the workspace being under the user's profile, which is the default location. | Accepted for Phase 1 (ADR 0002). | Revisit at hardening (M9) |
| R3 | **Values taken from source data are stored in the workspace:** min/max, frequent values, pattern examples, dictionary examples, contract accepted values, a row of a CSV that detection used as column names because it couldn't tell a header from data (AC-22), and their backups and SQLite free pages. This conflicts with "raw data not copied by default". | **Rule built and tested for CSV profiling (ADR 0004, `CFX-APP-csv-profile` AC-04 and AC-33).** A profile saves only counts, percentages, text lengths and masked patterns, and values are computed on request and never saved. **Still open:** a row used as column names that the user never corrects stays saved, and the screen says so; pre-migration backups can keep names replaced later; dictionary examples and contract accepted values aren't covered. | Dictionary and contract slices for what's left |
| R4 | **DuckDB spill files can hold source data** for the length of a job, or until the next start after a crash. They stay inside the workspace (ADR 0002 point 7). | Accepted for Phase 1. The setting is in place and tested (AC-49). | None |
| R5 | **Workspaces in sync folders or on network drives.** A sync service could copy the live database, and WAL isn't safe on network filesystems. The runtime warns about known sync folders. Network filesystems aren't detected. | Accepted for Phase 1 (ADR 0002). | Document for users |
