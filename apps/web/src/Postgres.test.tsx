import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import { CREDENTIAL_STORE, UNTIL_STOP } from "./api";

const workspace = {
  location: "/home/me/DrivnX/default",
  name: "Sales",
  ai_mode: "off",
  setup_complete: true,
};

const PASSWORD = "s3cret"; // pragma: allowlist secret, ai-skills: allow

const fixture = {
  id: "p1",
  kind: "postgres",
  name: "drivnx_fixture",
  host: "db.example.com",
  port: 5432,
  database: "drivnx_fixture",
  user: "drivnx_reader",
  tls_mode: "prefer",
  ca_path: null,
  password_kept: UNTIL_STOP,
  password_needed: false,
  server_version: "18.1 (Debian 18.1-1.pgdg13+1)",
  encrypted: true,
};

const fetchMock = vi.fn();
let sources: object[] = [];
// What the server answers to a POST, by path.
let answers: Record<string, () => Response> = {};
// What it answers to a GET, by path, where a test needs more than the defaults below.
let gets: Record<string, () => Response> = {};

beforeEach(() => {
  sources = [];
  gets = {};
  answers = {
    "/api/sources/postgres/test": () =>
      Response.json({ server_version: "18.1 (Debian 18.1-1.pgdg13+1)", encrypted: true }),
    "/api/sources/postgres": () => {
      sources = [...sources, fixture];
      return Response.json(fixture, { status: 201 });
    },
  };
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return answers[url]();
    }
    if (gets[url] !== undefined) {
      return gets[url]();
    }
    if (url === "/api/workspace") {
      return Response.json(workspace);
    }
    if (url === "/api/profile-runs") {
      return Response.json([]);
    }
    if (/^\/api\/sources\/[^/]+\/objects$/.test(url)) {
      return Response.json({ discovered_at: null, schemas: [] });
    }
    return Response.json(sources);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <App signedIn />
    </QueryClientProvider>,
  );
}

async function openForm() {
  const user = userEvent.setup();
  renderApp();
  const add = await screen.findByRole("button", { name: "Add a PostgreSQL database" });
  expect(add).toHaveAttribute("aria-expanded", "false");
  await user.click(add);
  expect(add).toHaveAttribute("aria-expanded", "true");
  return { user, form: screen.getByRole("form", { name: "PostgreSQL database" }) };
}

function posted(path: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([url, init]) => url === path && init?.method === "POST")
    .map(([, init]) => JSON.parse(init.body));
}

async function fillIn(user: ReturnType<typeof userEvent.setup>, form: HTMLElement) {
  await user.type(within(form).getByRole("textbox", { name: "Host" }), "db.example.com");
  await user.type(within(form).getByRole("textbox", { name: "Database" }), "drivnx_fixture");
  await user.type(within(form).getByRole("textbox", { name: "User" }), "drivnx_reader");
  await user.type(within(form).getByLabelText("Password"), PASSWORD);
}

test("the form asks for each detail, with port 5432 and TLS prefer by default", async () => {
  // CFX-APP-postgres/AC-01
  const { form } = await openForm();

  expect(within(form).getByRole("textbox", { name: "Host" })).toHaveValue("");
  expect(within(form).getByRole("spinbutton", { name: "Port" })).toHaveValue(5432);
  expect(within(form).getByRole("textbox", { name: "Database" })).toHaveValue("");
  expect(within(form).getByRole("textbox", { name: "User" })).toHaveValue("");
  expect(within(form).getByLabelText("Password")).toHaveAttribute("type", "password");
  const tls = within(form).getByRole("combobox", { name: "TLS mode" });
  expect(tls).toHaveValue("prefer");
  expect(within(tls).getAllByRole("option").map((option) => option.getAttribute("value"))).toEqual([
    "prefer",
    "require",
    "verify-full",
  ]);
});

test("there's no field for a connection string or URL", async () => {
  // CFX-APP-postgres/AC-01
  const { form } = await openForm();

  const names = [...form.querySelectorAll("input, select, textarea")].map(
    (field) => (field as HTMLInputElement).labels?.[0]?.textContent,
  );
  expect(names).toEqual(["Host", "Port", "Database", "User", "Password", "TLS mode"]);
});

test("a CA certificate file is asked for only with verify-full", async () => {
  // CFX-APP-postgres/AC-01
  const { user, form } = await openForm();
  const tls = within(form).getByRole("combobox", { name: "TLS mode" });

  expect(within(form).queryByRole("textbox", { name: "CA certificate file" })).not.toBeInTheDocument();
  await user.selectOptions(tls, "verify-full");
  expect(within(form).getByRole("textbox", { name: "CA certificate file" })).toBeRequired();
  await user.selectOptions(tls, "require");
  expect(within(form).queryByRole("textbox", { name: "CA certificate file" })).not.toBeInTheDocument();
});

test("testing a connection shows the server's version and that it's encrypted", async () => {
  // CFX-APP-postgres/AC-02
  const { user, form } = await openForm();
  await fillIn(user, form);

  await user.click(within(form).getByRole("button", { name: "Test connection" }));

  expect(await within(form).findByRole("status")).toHaveTextContent(
    "Connected to PostgreSQL 18.1 (Debian 18.1-1.pgdg13+1). The connection is encrypted.",
  );
  expect(posted("/api/sources/postgres/test")).toEqual([
    {
      host: "db.example.com",
      port: 5432,
      database: "drivnx_fixture",
      user: "drivnx_reader",
      password: PASSWORD,
      tls_mode: "prefer",
    },
  ]);
});

test("a connection that isn't encrypted says so", async () => {
  // CFX-APP-postgres/AC-02
  answers["/api/sources/postgres/test"] = () => Response.json({ server_version: "16.4", encrypted: false });
  const { user, form } = await openForm();
  await fillIn(user, form);

  await user.click(within(form).getByRole("button", { name: "Test connection" }));

  expect(await within(form).findByRole("status")).toHaveTextContent(
    "Connected to PostgreSQL 16.4. The connection isn't encrypted.",
  );
});

test("verify-full sends the CA certificate file, and another mode doesn't", async () => {
  // CFX-APP-postgres/AC-01
  const { user, form } = await openForm();
  await fillIn(user, form);
  const tls = within(form).getByRole("combobox", { name: "TLS mode" });
  await user.selectOptions(tls, "verify-full");
  await user.type(within(form).getByRole("textbox", { name: "CA certificate file" }), '"/certs/ca.crt" ');

  await user.click(within(form).getByRole("button", { name: "Test connection" }));
  await within(form).findByRole("status");
  await user.selectOptions(tls, "require");
  await user.click(within(form).getByRole("button", { name: "Test connection" }));

  await vi.waitFor(() => expect(posted("/api/sources/postgres/test")).toHaveLength(2));
  const [verified, required] = posted("/api/sources/postgres/test") as Record<string, unknown>[];
  expect(verified).toMatchObject({ tls_mode: "verify-full", ca_path: "/certs/ca.crt" });
  expect(required.tls_mode).toBe("require");
  expect(required).not.toHaveProperty("ca_path");
});

test.each([
  ["the host can't be reached", "Nothing answered at this host and port. Check them, and that the server is running."],
  ["the credentials are refused", "The server refused this user name and password."],
  ["the database doesn't exist", "The server has no database with this name."],
  ["TLS is required and fails", "The server doesn't offer encrypted connections, and the TLS mode requires one."],
  ["the platform has no PostgreSQL", "PostgreSQL isn't available on this platform (linux_arm64)."],
])("when %s, testing shows the server's message and keeps what was typed", async (_case, message) => {
  // CFX-APP-postgres/AC-03 and CFX-APP-postgres/AC-07
  answers["/api/sources/postgres/test"] = () => Response.json({ detail: message }, { status: 422 });
  const { user, form } = await openForm();
  await fillIn(user, form);

  await user.click(within(form).getByRole("button", { name: "Test connection" }));

  expect(await within(form).findByRole("alert")).toHaveTextContent(message);
  expect(within(form).queryByRole("status")).not.toBeInTheDocument();
  expect(within(form).getByRole("textbox", { name: "Host" })).toHaveValue("db.example.com");
  expect(within(form).getByLabelText("Password")).toHaveValue(PASSWORD);
});

test("details the server refuses as invalid are named by their fields", async () => {
  // A host with a space, which the browser can't check.
  answers["/api/sources/postgres/test"] = () =>
    Response.json(
      {
        detail: [
          { field: "body.host", reason: "String should match pattern" },
          { field: "body.password", reason: "must not contain control characters" },
        ],
      },
      { status: 422 },
    );
  const { user, form } = await openForm();
  await fillIn(user, form);

  await user.click(within(form).getByRole("button", { name: "Test connection" }));

  expect(await within(form).findByRole("alert")).toHaveTextContent("Check these fields: Host, Password.");
});

test.each([
  ["the session has ended", 401],
  ["the server failed", 500],
])("when %s, testing says to start DrivnX again", async (_case, status) => {
  answers["/api/sources/postgres/test"] = () => Response.json({ detail: "no" }, { status });
  const { user, form } = await openForm();
  await fillIn(user, form);

  await user.click(within(form).getByRole("button", { name: "Test connection" }));

  expect(await within(form).findByRole("alert")).toHaveTextContent(/start drivnx again/i);
});

test("a saved connection is listed under its database name and opened", async () => {
  // CFX-APP-postgres/AC-02
  const { user, form } = await openForm();
  await fillIn(user, form);

  await user.click(within(form).getByRole("button", { name: "Save" }));

  const explorer = screen.getByRole("navigation", { name: "Data Explorer" });
  expect(await within(explorer).findByRole("button", { name: "drivnx_fixture" })).toHaveAttribute(
    "aria-current",
    "true",
  );
  expect(screen.queryByRole("form", { name: "PostgreSQL database" })).not.toBeInTheDocument();
  const details = screen.getByRole("region", { name: "drivnx_fixture" });
  expect(screen.getByRole("main")).toContainElement(details);
  expect(within(details).getByText("db.example.com")).toBeInTheDocument();
  expect(within(details).getByText("5432")).toBeInTheDocument();
  expect(within(details).getByText("drivnx_reader")).toBeInTheDocument();
  expect(within(details).getByText("prefer")).toBeInTheDocument();
  expect(details).toHaveTextContent(
    "Connected to PostgreSQL 18.1 (Debian 18.1-1.pgdg13+1). The connection is encrypted.",
  );
  expect(posted("/api/sources/postgres")).toEqual([
    {
      host: "db.example.com",
      port: 5432,
      database: "drivnx_fixture",
      user: "drivnx_reader",
      password: PASSWORD,
      tls_mode: "prefer",
    },
  ]);
});

test("with no credential store, a saved connection says its password is kept only until DrivnX stops", async () => {
  // CFX-APP-postgres/AC-05
  sources = [fixture];
  renderApp();

  const details = await screen.findByRole("region", { name: "drivnx_fixture" });
  expect(details).toHaveTextContent(
    "DrivnX keeps this password only until it stops, and will ask for it again after a restart.",
  );
});

test("a connection that fails isn't saved, and saving shows why", async () => {
  // CFX-APP-postgres/AC-03
  answers["/api/sources/postgres"] = () =>
    Response.json({ detail: "The server refused this user name and password." }, { status: 422 });
  const { user, form } = await openForm();
  await fillIn(user, form);

  await user.click(within(form).getByRole("button", { name: "Save" }));

  expect(await within(form).findByRole("alert")).toHaveTextContent(
    "The server refused this user name and password.",
  );
  expect(screen.getByRole("form", { name: "PostgreSQL database" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "drivnx_fixture" })).not.toBeInTheDocument();
});

test("only the last attempt's answer is shown", async () => {
  answers["/api/sources/postgres/test"] = () =>
    Response.json({ detail: "The server has no database with this name." }, { status: 422 });
  answers["/api/sources/postgres"] = () =>
    Response.json({ detail: "The server refused this user name and password." }, { status: 422 });
  const { user, form } = await openForm();
  await fillIn(user, form);

  await user.click(within(form).getByRole("button", { name: "Test connection" }));
  await within(form).findByText("The server has no database with this name.");
  await user.click(within(form).getByRole("button", { name: "Save" }));

  expect(await within(form).findByRole("alert")).toHaveTextContent(
    /^The server refused this user name and password\.$/,
  );
});

const afterRestart = { ...fixture, password_needed: true, server_version: null, encrypted: null };

test("after a restart, opening the source asks for its password before anything is read", async () => {
  // CFX-APP-postgres/AC-05
  const user = userEvent.setup();
  sources = [afterRestart];
  answers["/api/sources/p1/password"] = () => {
    sources = [fixture];
    return Response.json(fixture);
  };
  renderApp();

  const details = await screen.findByRole("region", { name: "drivnx_fixture" });
  const prompt = within(details).getByRole("form", { name: "Password for drivnx_reader" });
  expect(details).not.toHaveTextContent(/Connected to PostgreSQL/);
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("p1"))).toEqual([]);

  await user.type(within(prompt).getByLabelText("Password"), PASSWORD);
  await user.click(within(prompt).getByRole("button", { name: "Connect" }));

  expect(await within(details).findByText(/Connected to PostgreSQL 18\.1/)).toBeInTheDocument();
  expect(within(details).queryByRole("form", { name: "Password for drivnx_reader" })).not.toBeInTheDocument();
  expect(posted("/api/sources/p1/password")).toEqual([{ password: PASSWORD }]);
});

test("after a restart, a password the server refuses isn't kept, and the prompt says why", async () => {
  // CFX-APP-postgres/AC-05
  const user = userEvent.setup();
  sources = [afterRestart];
  answers["/api/sources/p1/password"] = () =>
    Response.json({ detail: "The server refused this user name and password." }, { status: 422 });
  renderApp();

  const prompt = await screen.findByRole("form", { name: "Password for drivnx_reader" });
  await user.type(within(prompt).getByLabelText("Password"), "wrong");
  await user.click(within(prompt).getByRole("button", { name: "Connect" }));

  expect(await within(prompt).findByRole("alert")).toHaveTextContent(
    "The server refused this user name and password.",
  );
  expect(screen.getByRole("form", { name: "Password for drivnx_reader" })).toBeInTheDocument();
});

// After a restart, with the password in the OS credential store: not connected yet in this run.
const stored = { ...fixture, password_kept: CREDENTIAL_STORE, server_version: null, encrypted: null };

function connects(): number {
  return fetchMock.mock.calls.filter(
    ([url, init]) => url === "/api/sources/p1/connect" && init?.method === "POST",
  ).length;
}

test("after a restart, opening the source connects with the password from the credential store", async () => {
  // CFX-APP-postgres/AC-04: "DrivnX reads it from there after a restart", without asking.
  const connected = { ...stored, server_version: fixture.server_version, encrypted: true };
  sources = [stored];
  answers["/api/sources/p1/connect"] = () => {
    sources = [connected];
    return Response.json(connected);
  };
  renderApp();

  const details = await screen.findByRole("region", { name: "drivnx_fixture" });

  expect(await within(details).findByText(/Connected to PostgreSQL 18\.1/)).toBeInTheDocument();
  expect(connects()).toBe(1);
  expect(within(details).queryByRole("form", { name: "Password for drivnx_reader" })).not.toBeInTheDocument();
  expect(details).not.toHaveTextContent(/only until it stops/);
});

test("a stored password the server now refuses asks for the password, says why, and a new one connects", async () => {
  // CFX-APP-postgres/AC-06: "when the source is opened, then the user is asked for the password".
  const user = userEvent.setup();
  const connected = { ...stored, server_version: fixture.server_version, encrypted: true };
  sources = [stored];
  answers["/api/sources/p1/connect"] = () => {
    sources = [{ ...stored, password_needed: true }];
    return Response.json({ detail: "The server refused this user name and password." }, { status: 422 });
  };
  answers["/api/sources/p1/password"] = () => {
    sources = [connected];
    return Response.json(connected);
  };
  renderApp();

  const prompt = await screen.findByRole("form", { name: "Password for drivnx_reader" });

  expect(
    await within(prompt).findByText(
      "The server refused this user name and password. Enter the password for drivnx_reader to connect again.",
    ),
  ).toBeInTheDocument();
  expect(prompt).not.toHaveTextContent(/DrivnX has restarted/);
  await user.type(within(prompt).getByLabelText("Password"), PASSWORD);
  await user.click(within(prompt).getByRole("button", { name: "Connect" }));
  const details = screen.getByRole("region", { name: "drivnx_fixture" });
  expect(await within(details).findByText(/Connected to PostgreSQL 18\.1/)).toBeInTheDocument();
  expect(posted("/api/sources/p1/password")).toEqual([{ password: PASSWORD }]);
  expect(connects()).toBe(1);
  // The refusal was for the old password, so it no longer shows.
  expect(within(details).queryByRole("alert")).not.toBeInTheDocument();
});

test("a password the server refuses when the tables are listed asks for it, saying why", async () => {
  // CFX-APP-postgres/AC-06 when listing is the read that finds it: the server dropped the password.
  const user = userEvent.setup();
  sources = [{ ...fixture, password_kept: CREDENTIAL_STORE }];
  answers["/api/sources/p1/discover"] = () => {
    sources = [{ ...stored, password_needed: true }];
    return Response.json({ detail: "The server refused this user name and password." }, { status: 422 });
  };
  renderApp();
  const details = await screen.findByRole("region", { name: "drivnx_fixture" });

  await user.click(await within(details).findByRole("button", { name: "List schemas and tables" }));

  const prompt = await within(details).findByRole("form", { name: "Password for drivnx_reader" });
  expect(prompt).toHaveTextContent(
    "The server refused this user name and password. Enter the password for drivnx_reader to connect again.",
  );
  expect(connects()).toBe(0);
});

test("a server that doesn't answer on opening says so, and the password isn't asked for", async () => {
  // CFX-APP-postgres/AC-06 asks again only when the server refuses the password; AC-03's reason.
  sources = [stored];
  answers["/api/sources/p1/connect"] = () =>
    Response.json({ detail: "DrivnX couldn't reach this host. Check the host and port." }, { status: 422 });
  renderApp();

  const details = await screen.findByRole("region", { name: "drivnx_fixture" });

  expect(await within(details).findByRole("alert")).toHaveTextContent("DrivnX couldn't reach this host. Check the host and port.");
  expect(within(details).queryByRole("form", { name: "Password for drivnx_reader" })).not.toBeInTheDocument();
});

test("a server that answers again when its tables are listed no longer says it can't be reached", async () => {
  // CFX-APP-postgres/AC-06: the password was kept, so listing the tables connects again.
  const connected = { ...stored, server_version: fixture.server_version, encrypted: true };
  sources = [stored];
  let reachable = false;
  answers["/api/sources/p1/connect"] = () => {
    if (!reachable) {
      return Response.json({ detail: "DrivnX couldn't reach this host. Check the host and port." }, { status: 422 });
    }
    sources = [connected];
    return Response.json(connected);
  };
  answers["/api/sources/p1/discover"] = () => Response.json({ discovered_at: "2026-11-15T10:00:00Z", schemas: [] });
  const user = userEvent.setup();
  renderApp();
  const details = await screen.findByRole("region", { name: "drivnx_fixture" });
  await within(details).findByRole("alert");

  reachable = true;
  await user.click(within(details).getByRole("button", { name: "List schemas and tables" }));

  expect(await within(details).findByText(/Connected to PostgreSQL 18\.1/)).toBeInTheDocument();
  expect(within(details).queryByRole("alert")).not.toBeInTheDocument();
});

test("a database source offers a fast profile, its rows are previewed by object, and nothing asks for them", async () => {
  // CFX-APP-postgres/AC-18: a fast profile of its chosen objects; a full one comes with DRIVN-54.
  // AC-12: a chosen object's preview is in its details (DatabaseObjects.test.tsx), so the source has
  // none of its own.
  sources = [fixture];
  renderApp();

  await screen.findByRole("region", { name: "drivnx_fixture" });
  const tab = screen.getByRole("tabpanel", { name: "Profile" });
  expect(await within(tab).findByRole("button", { name: "Fast profile" })).toBeEnabled();
  expect(within(tab).getByRole("button", { name: /Full profile/ })).toBeDisabled();
  expect(tab).toHaveTextContent("Full profile Not available yet");
  expect(screen.queryByRole("region", { name: "Preview" })).not.toBeInTheDocument();
  const review = screen.getByRole("complementary", { name: "Intelligence & Review" });
  expect(review).not.toHaveTextContent(/profile this file/i);
  // Its list of tables is read from the workspace (DRIVN-49); nothing reads its rows or settings.
  expect(
    fetchMock.mock.calls.filter(([url]) => /\/p1\/(rows|settings)|profile-runs\/./.test(String(url))),
  ).toEqual([]);
});

test("a verify-full connection shows its CA certificate file, and another doesn't", async () => {
  sources = [fixture, { ...fixture, id: "p2", name: "verified", tls_mode: "verify-full", ca_path: "/certs/ca.crt" }];
  const user = userEvent.setup();
  renderApp();

  const plain = await screen.findByRole("region", { name: "drivnx_fixture" });
  expect(plain).not.toHaveTextContent("CA certificate file");
  await user.click(screen.getByRole("button", { name: "verified" }));
  const verified = await screen.findByRole("region", { name: "verified" });
  expect(within(verified).getByText("/certs/ca.crt")).toBeInTheDocument();
});

test("saving a saved connection with other TLS settings shows why it's refused", async () => {
  // Found in review: the new settings were dropped, and the save looked as if it had worked.
  const message =
    "This connection is already saved with TLS mode prefer. Changing a saved connection isn't available yet.";
  answers["/api/sources/postgres"] = () => Response.json({ detail: message }, { status: 409 });
  const { user, form } = await openForm();
  await fillIn(user, form);

  await user.click(within(form).getByRole("button", { name: "Save" }));

  expect(await within(form).findByRole("alert")).toHaveTextContent(message);
});

test("changing a detail after a test clears its result, which was for the old details", async () => {
  // Found in review.
  const { user, form } = await openForm();
  await fillIn(user, form);
  await user.click(within(form).getByRole("button", { name: "Test connection" }));
  await within(form).findByRole("status");

  await user.type(within(form).getByRole("textbox", { name: "Host" }), "x");

  expect(within(form).queryByRole("status")).not.toBeInTheDocument();
});

test("a password the server refuses when an object is previewed asks for it, saying why", async () => {
  // CFX-APP-postgres/AC-12 with AC-06: the preview is the read that finds the password refused.
  const user = userEvent.setup();
  sources = [fixture];
  const orders = { id: "o1", name: "orders", kind: "table", estimated_rows: 3, readable: true, populated: true, chosen: true };
  gets["/api/sources/p1/objects"] = () =>
    Response.json({ discovered_at: "2026-11-16T10:00:00+00:00", schemas: [{ name: "sales", objects: [orders] }] });
  gets["/api/sources/p1/objects/o1"] = () => Response.json({ ...orders, schema: "sales", columns: [], keys: [] });
  gets["/api/sources/p1/objects/o1/rows?offset=0"] = () => {
    sources = [{ ...fixture, password_needed: true }];
    return Response.json({ detail: "The server refused this user name and password." }, { status: 422 });
  };
  renderApp();
  const details = await screen.findByRole("region", { name: "drivnx_fixture" });

  await user.click(await within(details).findByRole("button", { name: "orders" }));

  const prompt = await within(details).findByRole("form", { name: "Password for drivnx_reader" });
  expect(prompt).toHaveTextContent(
    "The server refused this user name and password. Enter the password for drivnx_reader to connect again.",
  );
});

function metric(object: string, column: string | null, name: string, value: unknown, scope = "sample") {
  return { object, column, metric: name, value, scope, precision: "exact" };
}

const databaseRun = {
  id: "r1",
  source_id: "p1",
  mode: "fast",
  status: "completed",
  started_at: "2026-11-17T09:00:00+00:00",
  ended_at: "2026-11-17T09:00:02+00:00",
  current_settings: true,
  failure: null,
  current_object: null,
};

test("a database run's results show each object with how its rows were read, the run's ID and time", async () => {
  // CFX-APP-postgres/AC-18 and AC-24: each object's scope (about N rows sampled by page, the first rows
  // returned, or the whole object), each metric's precision, and the run's time and ID; no "values that
  // don't fit" in a typed column, and no values on request yet (DRIVN-56).
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([databaseRun]);
  gets["/api/findings?profile_run_id=r1"] = () => Response.json([]);
  gets["/api/profile-runs/r1"] = () =>
    Response.json({
      ...databaseRun,
      file_changed: null,
      objects: [
        { object: "sales.big_guess", method: "first_rows" },
        { object: "sales.order_view", method: "view_first_rows" },
        { object: "sales.orders", method: "page_sample" },
        { object: "sales.small", method: "whole" },
      ],
      metrics: [
        metric("sales.big_guess", null, "rows_read", 100000),
        metric("sales.order_view", null, "rows_read", 100000),
        metric("sales.orders", null, "rows_read", 98412),
        metric("sales.orders", "region", "missing_count", 12),
        metric("sales.orders", "region", "missing_percent", 0.01),
        metric("sales.orders", "region", "distinct_count", 4),
        metric("sales.orders", "region", "distinct_percent", 0.0),
        metric("sales.orders", "region", "shortest_length", 4),
        metric("sales.orders", "region", "longest_length", 5),
        metric("sales.orders", "region", "average_length", 4.5),
        metric("sales.small", null, "rows_read", 3, "full"),
      ],
    });
  renderApp();

  const tab = await screen.findByRole("tabpanel", { name: "Profile" });

  const tables = await within(tab).findAllByRole("table");
  expect(tables.map((table) => table.querySelector("caption")?.textContent)).toEqual([
    "sales.big_guess: the first 100,000 rows returned, not random",
    "sales.order_view: the first 100,000 rows the view returned, not random",
    "sales.orders: about 98,412 rows, sampled by page",
    "sales.small: the whole object, 3 rows",
  ]);
  expect(tab).toHaveTextContent(`Run r1, finished ${new Date(databaseRun.ended_at).toLocaleString()}`);
  const orders = tables[2];
  expect(
    within(orders)
      .getAllByRole("columnheader")
      .map((header) => header.textContent),
  ).toEqual(["Column", "Precision", "Missing", "Distinct", "Shortest", "Longest", "Average", "Patterns"]);
  const region = within(orders).getByRole("row", { name: /region/ });
  expect([...region.querySelectorAll("th, td")].map((cell) => cell.textContent)).toEqual([
    "region",
    "Exact",
    "12 (0.01%)",
    "4 (0%)",
    "4",
    "5",
    "4.5",
    "\u2014",
  ]);
});

test("a running database run says which object it is on, in the header and the Profile tab", async () => {
  // CFX-APP-postgres/AC-17
  sources = [fixture];
  gets["/api/profile-runs"] = () =>
    Response.json([{ ...databaseRun, status: "running", ended_at: null, current_object: "sales.orders" }]);
  renderApp();

  const tab = await screen.findByRole("tabpanel", { name: "Profile" });

  expect(await screen.findByText("Profile status: Running on drivnx_fixture: sales.orders")).toBeInTheDocument();
  expect(within(tab).getByRole("status")).toHaveTextContent(
    "A profile is running on sales.orders. Results appear here when it finishes.",
  );
  expect(within(tab).getByRole("heading", { name: "Runs of this database" })).toBeInTheDocument();
});

// What the server refuses a start with when the role could change chosen objects (DRIVN-52).
const writerAccess = {
  role: "drivnx_writer",
  superuser: false,
  objects: [
    { object: "sales.events", privileges: ["DELETE", "TRUNCATE"] },
    { object: "sales.orders", privileges: ["INSERT", "UPDATE"] },
  ],
  chosen: ["sales.events", "sales.orders", "sales.people"],
};

function refusedFor(access: object): () => Response {
  return () => Response.json({ detail: { message: "Confirm to profile.", write_access: access } }, { status: 409 });
}

async function startFastProfile() {
  const user = userEvent.setup();
  renderApp();
  const tab = await screen.findByRole("tabpanel", { name: "Profile" });
  await user.click(await within(tab).findByRole("button", { name: "Fast profile" }));
  return { user, tab };
}

test("a role that could change chosen objects is warned, naming each and how, before any run starts", async () => {
  // CFX-APP-postgres/AC-16: "the screen names the objects ... and why, and the run starts only after the
  // user confirms".
  sources = [fixture];
  answers["/api/profile-runs"] = refusedFor(writerAccess);

  const { tab } = await startFastProfile();

  const dialog = await within(tab).findByRole("alertdialog", {
    name: "drivnx_writer can change some of the chosen objects. Profile them anyway?",
  });
  expect(within(dialog).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
    "sales.events: delete, truncate",
    "sales.orders: insert, update",
  ]);
  expect(dialog).toHaveTextContent("DrivnX opens every session read-only and only reads them.");
  expect(within(tab).queryByRole("alert")).not.toBeInTheDocument();
});

test("profiling anyway starts the run with the write access the user was shown", async () => {
  // CFX-APP-postgres/AC-16: the run starts once the user confirms, and only what they were shown.
  sources = [fixture];
  let starts = 0;
  answers["/api/profile-runs"] = () =>
    starts++ === 0 ? refusedFor(writerAccess)() : Response.json({ ...databaseRun, status: "running" }, { status: 202 });
  const { user, tab } = await startFastProfile();
  const dialog = await within(tab).findByRole("alertdialog");

  await user.click(within(dialog).getByRole("button", { name: "Profile anyway" }));

  expect(posted("/api/profile-runs")).toEqual([
    { source_id: "p1", mode: "fast" },
    { source_id: "p1", mode: "fast", write_access: writerAccess },
  ]);
  expect(within(tab).queryByRole("alertdialog")).not.toBeInTheDocument();
});

test("cancelling the write warning starts nothing and returns to Fast profile", async () => {
  // CFX-APP-postgres/AC-16: no run without the user's confirmation; WCAG 2.4.3, focus goes back to where
  // the user was.
  sources = [fixture];
  answers["/api/profile-runs"] = refusedFor(writerAccess);
  const { user, tab } = await startFastProfile();
  const dialog = await within(tab).findByRole("alertdialog");

  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

  expect(posted("/api/profile-runs")).toEqual([{ source_id: "p1", mode: "fast" }]);
  expect(within(tab).queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(within(tab).getByRole("button", { name: "Fast profile" })).toHaveFocus();
});

test("a superuser is warned that it can change anything, naming the role", async () => {
  // CFX-APP-postgres/AC-16: "the screen names the objects or the superuser role and why".
  sources = [fixture];
  answers["/api/profile-runs"] = refusedFor({
    role: "drivnx_admin",
    superuser: true,
    objects: [{ object: "sales.orders", privileges: ["INSERT", "UPDATE", "DELETE", "TRUNCATE"] }],
    chosen: ["sales.orders"],
  });

  const { tab } = await startFastProfile();

  const dialog = await within(tab).findByRole("alertdialog", {
    name: "drivnx_admin is a superuser, so it can change anything in this database. Profile it anyway?",
  });
  expect(within(dialog).queryByRole("list")).not.toBeInTheDocument();
  expect(dialog).toHaveTextContent("DrivnX opens every session read-only and only reads it.");
});

test.each([
  [true, true],
  [false, false],
])("a run whose account could only read says so (read only: %s)", async (readOnly, said) => {
  // CFX-APP-postgres/AC-16: "Given a role that can only read the chosen objects, then no warning appears,
  // and the screen says the account can only read them."
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([{ ...databaseRun, read_only: readOnly }]);
  gets["/api/findings?profile_run_id=r1"] = () => Response.json([]);
  gets["/api/profile-runs/r1"] = () =>
    Response.json({ ...databaseRun, read_only: readOnly, file_changed: null, objects: [], metrics: [] });
  renderApp();

  const tab = await screen.findByRole("tabpanel", { name: "Profile" });
  await within(tab).findByText(`Run r1, finished ${new Date(databaseRun.ended_at).toLocaleString()}`);

  expect(within(tab).queryByText("drivnx_reader can only read the chosen objects.") !== null).toBe(said);
});

test("the write warning takes focus on its safe choice, Cancel", async () => {
  // CFX-APP-postgres/AC-16 and WCAG 2.4.3: an alert dialog moves focus into itself, onto the choice that
  // changes nothing, as the possibly-sensitive values' confirmation does.
  sources = [fixture];
  answers["/api/profile-runs"] = refusedFor(writerAccess);

  const { tab } = await startFastProfile();

  const dialog = await within(tab).findByRole("alertdialog");
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
});
