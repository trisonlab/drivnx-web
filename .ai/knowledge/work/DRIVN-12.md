---
id: DRIVN-12
legacy_id: CFX-APP-first-slice-T02
title: "Session protection"
feature: CFX-APP-first-slice
feature_revision: 10
stories: [US-01, US-02, US-09]
criteria: [AC-06, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13, AC-52]
blocked_by: [DRIVN-11]
state: done
external_key: DRIVN-12
status: CONFIRMED
---

# Session protection

Plane: [DRIVN-12](https://app.plane.so/drivnx/browse/DRIVN-12/) · was `CFX-APP-first-slice-T02`

## Outcome
The browser signs in with the launch code and holds the session in an `HttpOnly` cookie, and every bad request is refused, including on `/api/session`. Part of spike 6 (local browser security).

## Acceptance checklist
- [x] AC-06 (US-01): Given the runtime is running, when the user presses Ctrl+C, then it stops, and the previous session no longer works after a restart.
- [x] AC-08 (US-02): Given a call to any `/api/*` route except `/api/session` without a valid session cookie, then the response is 401.
- [x] AC-09 (US-02): Given a call to any `/api/*` route, including `/api/session`, with a wrong Host, or a wrong Origin, or no Origin on a state-changing method, then the response is 403.
- [x] AC-10 (US-02): Given a launch code, when it is used a second time or more than 2 minutes after launch, then it is rejected.
- [x] AC-11 (US-02): Given a successful handshake, then the session cookie is `HttpOnly` and `SameSite=Strict` with `Path=/api`, and the token is in no URL or browser storage.
- [x] AC-12 (US-02): Given a full run of this slice's journey with logging at its most detailed, then the logs contain no session token, launch code or preview row values.
- [x] AC-13 (US-02): Given every GET and HEAD route under `/api`, when each is called, then no workspace data changes.
- [x] AC-52 (US-09): Given the Vite dev server, then the browser uses the same cookie flow through its proxy, and the dev origin is allowed only with the dev flag.

## Expected evidence
Tests naming each criterion, including a check over every GET and HEAD route and a log capture. Record what spike 6 confirmed about accepted risk R1 in the threat register.

## From the T01 review
- On Linux with no display, a text browser such as `www-browser` receives the launch URL and the terminal prints nothing, so the user can't sign in. Reproduced in the T01 review. On Linux with no display, print the URL and don't try a browser, narrow ADR 0001 to say so, and add a test.
