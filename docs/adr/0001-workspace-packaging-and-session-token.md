# 0001 — Workspace packaging: CLI launcher, and how the browser gets the session token

- **Status:** Accepted 2026-10-31, amended 2026-11-01 (first spike 6 pass 2026-11-01, Chromium only; see the threat register)
- **Date:** 2026-10-31
- **Source:** Direction doc §4 (deliberately avoided), §7 (must-have controls, threat model), §11/§12 (open
  decisions), Review Notes #10

## Context

The first vertical slice is "start the local runtime → browser opens → create a workspace → connect a CSV". This ADR
covers how the runtime is installed and started, how it opens the browser, and how the browser gets the per-launch
session token.

Constraints we already have:

- Electron/Tauri are deliberately avoided until the browser UX is validated (§4). Signed installers count as later
  hardening (§7).
- The runtime binds only to `127.0.0.1`, serves UI and API from one origin, and validates Host and Origin. Every
  `/api/*` call except `/api/session` needs the session token (§7).
- Secrets never go in URLs, browser storage or logs (§7). We treat the session token as a secret.
- Threat to cover: a malicious web page calls the local API (§7).
- Assumption still to test: users are comfortable starting a local runtime and opening a browser (§11).

## Decision

**1. Packaging: one Python package, distributed as `drivnx-data`, with a `drivnx` console command.** `apps/local-api`
is built with the production `apps/web` bundle included as package data. Users install it with
`uv tool install drivnx-data`, which can also fetch Python 3.12. `pipx install drivnx-data` works too, but by default
needs Python 3.12 to be present already.

- **Never `uv tool install drivnx`.** That name on PyPI belongs to an unrelated DrivnX network SDK, so install docs
  must always use the distribution name.
- `drivnx-data` was free on PyPI on 2026-11-01 but isn't reserved. Publishing or reserving it is a separate step.

```text
drivnx [WORKSPACE_DIR] [--port N] [--no-browser] [--log-level LEVEL]
```

**One runtime serves exactly one workspace, chosen at launch.** With no argument, `WORKSPACE_DIR` is `~/DrivnX/default`.
The default sits directly under the home directory, not under Documents, which Windows often redirects to OneDrive (ADR
0002 point 5). Opening a different workspace means restarting with a different directory. On first launch the UI shows
where the workspace is stored, then asks for its name and AI mode. So the API is `/api/workspace` (the current
workspace), not `/api/workspaces`, and the runtime needs no server-side folder browser.

**2. Startup sequence**

1. Resolve the workspace directory and check that DrivnX may use it (ADR 0002 point 1). Only then create
   `.drivnx.lock` and take an **OS-held exclusive lock** on it. The OS releases that lock when the process dies, even
   on a crash, so the file's existence means nothing and it stays on disk. If a second runtime opens the same workspace,
   it exits with a clear message instead of sharing SQLite. Then open the workspace (create or migrate the database,
   mark interrupted runs, ADR 0002).
2. Bind Uvicorn to `127.0.0.1`. By default the OS picks a free port; `--port` sets one. Binding to anything other
   than a loopback address is refused.
3. Generate in memory, with `secrets.token_urlsafe`, a **session token** (it lasts as long as the process) and a
   **launch code** (single-use, expires after 2 minutes). Neither one is written to disk or logged.
4. Open `http://127.0.0.1:<port>/#launch=<code>` with `webbrowser`. With `--no-browser`, if opening fails, or on Linux
   with no display and no `BROWSER` set, print that URL to the terminal (the only place the code ever appears). The
   display rule exists because Python would otherwise hand the URL to a text browser, which can't run the UI, and the
   user would never see the link.
5. Ctrl+C stops the runtime. The token dies with the process.

**3. Token handshake (`/api/session`)**

1. The UI reads the launch code from the URL fragment. Browsers don't send the fragment to the server, so it stays
   out of request logs. The UI removes it right away with `history.replaceState`.
2. The UI sends `POST /api/session {code}`. If the code is valid and unused, the server burns it and responds with
   `Set-Cookie: drivnx_session_<port>=<token>; HttpOnly; SameSite=Strict; Path=/api`.
3. The browser sends that cookie on every `/api/*` call. JavaScript can't read the token, and it never appears in a
   URL or in browser storage. `GET /api/session` reports only whether the cookie is valid, so a page opened without a
   code can tell the user to start DrivnX again.
4. Middleware on every `/api/*` route, **including `/api/session`**, checks Host is exactly `127.0.0.1:<port>` (blocks
   DNS rebinding), then Origin is exactly `http://127.0.0.1:<port>` whenever the header is present and always on
   state-changing methods. Every route except `/api/session` then checks the token cookie (compared in constant time).
5. GET and HEAD routes never change state. The Origin check can't cover them, as explained below.

**Every response carries `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'` and
`X-Content-Type-Options: nosniff`.** `SameSite` treats other local ports as the same site, so without this a page on
another port could frame the signed-in UI and trick the user into clicking it.

**Why the Origin check is required, not optional:** cookies are not isolated by port, and `SameSite` treats every
`127.0.0.1` port as the same site. A page served by another local process on `127.0.0.1:<other>` therefore gets
past `SameSite`. The exact Origin check is what stops it on state-changing requests and on any request that carries
Origin. A simple cross-origin GET may not carry it, but the browser won't let that page read the response, which is why
GET routes must have no side effects. The port in the cookie name keeps two DrivnX runtimes from overwriting each
other's cookie.

**4. Development:** the Vite dev server proxies `/api` to the runtime, so the browser still sees one origin and the
same cookie flow. The proxy sets `changeOrigin: true`, so the runtime still sees its own Host. The runtime's
`--dev-origin` flag adds the Vite origin as the second allowed Origin. It accepts only `http://127.0.0.1:<port>`, makes
the launch link point at Vite, and means the runtime doesn't serve the UI bundle. Nothing else changes.

## Alternatives considered

| Option | Why not now |
|---|---|
| Native installer (MSI/pkg/deb) | Needs signing, packaging for each OS and an update channel. Signing is already listed as later hardening. Testing the "local runtime + browser" assumption doesn't need it. It can wrap this same launcher later. |
| Desktop wrapper (Tauri/Electron) | Deliberately avoided until the browser UX is validated (§4). Most of it carries over later anyway: the wrapper can start the runtime and hand over the launch code the same way. |
| Docker image | Users would need Docker. Reading local files and using the OS credential store would require mounts and a keyring bridge. |
| Token in the URL, reusable (Jupyter style) | Breaks "never in URLs". The token ends up in browser history and can leak through screenshots and copy-paste. |
| Token kept in JS memory or `sessionStorage`, sent as a header | Memory: the session is lost on every reload. `sessionStorage`: breaks "never in browser storage", and any XSS can read it. An `HttpOnly` cookie protected by the exact-Origin check avoids both problems. |
| Fixed default port | Clashes with other local tools, and gives no benefit because the token changes on every launch anyway. |
| Start with no workspace and pick one in the UI | Closer to the direction doc's original journey, but needs a server-side folder browser and moves locking, migrations and interrupted-run cleanup out of startup. Switching workspaces by restarting is acceptable for Phase 1. The direction doc's step 1 was amended to match. |

## Consequences

- **Easier:** a single artifact to build, test and run in CI and Playwright (start `drivnx --no-browser
  --port …`, then take the URL from stdout). The first slice needs no packaging infrastructure.
- **Harder or accepted:**
  - Users need `uv` or `pipx` and a terminal. That's acceptable for Phase 1's technical users, and moderated
    sessions will check whether it's acceptable at all (§11 assumption).
  - The launch code is single-use. Opening DrivnX in a second browser means restarting the runtime. New tabs in the
    same browser are fine because the cookie is shared.
  - The runtime stops when its terminal closes. An idle shutdown or a "Stop" button waits until someone needs it.
  - Switching workspaces means restarting. Running two workspaces at once means two runtimes on two ports.
  - **The session cookie reaches other local servers.** Cookies aren't isolated by port, so the browser also sends it to
    any other server on `127.0.0.1` for a path under `/api`. That server could log or replay the token, forging Host
    and Origin as it isn't a browser. Accepted for Phase 1: a program running as the same user can already read the
    workspace. Shared machines are the weaker case. Spike 6 confirmed it in Chromium. Recorded as R1 in the threat register.
- **Tests that must ship with the first commit:**
  - Non-loopback bind is refused.
  - `/api/*` without the cookie returns 401.
  - A wrong Host or Origin returns 403, on `/api/session` too.
  - A second runtime on the same workspace is refused, and a new runtime can start after the first is killed with
    `SIGKILL`.
  - No argument resolves to the default workspace, and it is created if missing.
  - A launch code is rejected on second use and after it expires.
  - Neither the token nor the code appears in log output.
- **Revisit when:** the user sessions reject the terminal and install step, or enterprise distribution needs
  signed installers. Either case gets a new ADR that supersedes the packaging part of this one. The handshake can
  stay as it is.
