---
id: planted-scan-matches-a-launch-code
kind: issue
status: CONFIRMED
found: "2026-11-24"
related: [DRIVN-58, DRIVN-45, DRIVN-9, ADR-0001]
evidence:
  - tests/e2e/test_profile_journey.py
  - tests/e2e/test_postgres_journey.py
  - tests/conftest.py
  - apps/local-api/local_api/session.py
  - apps/local-api/local_api/cli.py
---

# The planted-value scan can match the random launch code in DrivnX's output

**Symptom.** On PR #39's CI on Windows (run 45215125174, attempt 1),
`test_the_database_journey_keeps_no_value_and_sends_only_drivnxs_reads` failed AC-29's log scan with
`assert [b'zqh0EEjFjZv2Pbk'] == []`. The re-run on the same commit passed. The match has mixed case and no
`@planted.example`, unlike any planted value.

**Cause.** `assert_no_value_kept_or_logged` scans everything DrivnX printed, which includes the launch URL
`http://127.0.0.1:<port>/#launch=<code>` (`cli.py`; `RunningDrivnX.all_output` keeps the lines read while waiting for
it). The launch code is `secrets.token_urlsafe(32)` (`session.py`), and `PLANTED`'s `zq\w+?k` matched any `zq`, then
letters, digits or `_`, then a `k`: 0.22% of random launch URLs, so about 1 CI run in 50 over the 9 launches scanned
(3 per OS). It is the only random base64-like string in the output: at `--log-level trace` uvicorn prints
`'headers': '<...>'` and body sizes, so the session cookie never appears, and the app's IDs are uuid hex, which has
no `z`, `q` or `k`. That this run's code was the match is INFERRED, as CI doesn't print the output; nothing else
printed could have matched.

**Fix.** `PLANTED` matches an email's own part by the four shapes planted (`zq<digits>k`, `zq<digits>afterk`,
`zqbeforek`, `zqoopsk`). `test_the_scan_finds_each_kind_of_planted_value_but_not_a_launch_code` holds the CI match
and each shape. A random launch URL now matches about 35 times in a million (`zq<digits>k` can still occur), about 1
CI run in 3,000. A new planted value's pattern must not be one random URL-safe base64 can spell easily, and the
journeys' own check that every planted value matches still guards against a pattern that's too narrow.
