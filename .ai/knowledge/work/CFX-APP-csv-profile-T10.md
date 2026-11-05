---
id: CFX-APP-csv-profile-T10
title: "Values on request"
feature: CFX-APP-csv-profile
feature_revision: 1
stories: [US-05, US-06]
criteria: [AC-29, AC-30, AC-31, AC-32, AC-34]
blocked_by: [CFX-APP-csv-profile-T06, CFX-APP-csv-profile-T09]
state: blocked
external_key: null
status: CONFIRMED
---

# Values on request

## Outcome
A user can ask to see a column's real values and statistics, computed fresh and never saved, with an extra confirmation for columns flagged as possibly sensitive.

## Acceptance checklist
- [ ] AC-29 (US-05): Given a column flagged as possibly sensitive, when on-request values are requested without the extra confirmation, then the API refuses and shows no values. With the confirmation, they are shown.
- [ ] AC-30 (US-06): Given a column in a completed run, when the user asks to see its values, then min, max and the 10 most frequent values are shown, and for numeric and date columns also a histogram, and mean, median and standard deviation where they apply. They are labelled with the part of the file they came from and the time they were computed.
- [ ] AC-31 (US-06): Given values shown on request, then each is cut at 1,000 characters and marked when cut, as in the preview, and the response is `Cache-Control: no-store`.
- [ ] AC-32 (US-06): Given a file that is no longer readable, when values are requested, then the user gets a clear message, and the saved metrics still show.
- [ ] AC-34 (US-06): Given every new GET route, when it is called, then no workspace data changes, and it follows AC-08, AC-09 and AC-13 of the first slice.

## Expected evidence
Tests naming each criterion, including `Cache-Control: no-store`, the refusal without confirmation, and the first slice's checks on the new GET route.
