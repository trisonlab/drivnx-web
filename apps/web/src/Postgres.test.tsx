import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
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
  // DRIVN-45/AC-01
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
  // DRIVN-45/AC-01
  const { form } = await openForm();

  const names = [...form.querySelectorAll("input, select, textarea")].map(
    (field) => (field as HTMLInputElement).labels?.[0]?.textContent,
  );
  expect(names).toEqual(["Host", "Port", "Database", "User", "Password", "TLS mode"]);
});

test("a CA certificate file is asked for only with verify-full", async () => {
  // DRIVN-45/AC-01
  const { user, form } = await openForm();
  const tls = within(form).getByRole("combobox", { name: "TLS mode" });

  expect(within(form).queryByRole("textbox", { name: "CA certificate file" })).not.toBeInTheDocument();
  await user.selectOptions(tls, "verify-full");
  expect(within(form).getByRole("textbox", { name: "CA certificate file" })).toBeRequired();
  await user.selectOptions(tls, "require");
  expect(within(form).queryByRole("textbox", { name: "CA certificate file" })).not.toBeInTheDocument();
});

test("testing a connection shows the server's version and that it's encrypted", async () => {
  // DRIVN-45/AC-02
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
  // DRIVN-45/AC-02
  answers["/api/sources/postgres/test"] = () => Response.json({ server_version: "16.4", encrypted: false });
  const { user, form } = await openForm();
  await fillIn(user, form);

  await user.click(within(form).getByRole("button", { name: "Test connection" }));

  expect(await within(form).findByRole("status")).toHaveTextContent(
    "Connected to PostgreSQL 16.4. The connection isn't encrypted.",
  );
});

test("verify-full sends the CA certificate file, and another mode doesn't", async () => {
  // DRIVN-45/AC-01
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
  // DRIVN-45/AC-03 and DRIVN-45/AC-07
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
  // DRIVN-45/AC-02
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
  // DRIVN-45/AC-05
  sources = [fixture];
  renderApp();

  const details = await screen.findByRole("region", { name: "drivnx_fixture" });
  expect(details).toHaveTextContent(
    "DrivnX keeps this password only until it stops, and will ask for it again after a restart.",
  );
});

test("a connection that fails isn't saved, and saving shows why", async () => {
  // DRIVN-45/AC-03
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
  // DRIVN-45/AC-05
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
  // DRIVN-45/AC-05
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
  // DRIVN-45/AC-04: "DrivnX reads it from there after a restart", without asking.
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
  // DRIVN-45/AC-06: "when the source is opened, then the user is asked for the password".
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
  // DRIVN-45/AC-06 when listing is the read that finds it: the server dropped the password.
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
  // DRIVN-45/AC-06 asks again only when the server refuses the password; AC-03's reason.
  sources = [stored];
  answers["/api/sources/p1/connect"] = () =>
    Response.json({ detail: "DrivnX couldn't reach this host. Check the host and port." }, { status: 422 });
  renderApp();

  const details = await screen.findByRole("region", { name: "drivnx_fixture" });

  expect(await within(details).findByRole("alert")).toHaveTextContent("DrivnX couldn't reach this host. Check the host and port.");
  expect(within(details).queryByRole("form", { name: "Password for drivnx_reader" })).not.toBeInTheDocument();
});

test("a server that answers again when its tables are listed no longer says it can't be reached", async () => {
  // DRIVN-45/AC-06: the password was kept, so listing the tables connects again.
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

test("a database source offers a fast and a full profile, its rows are previewed by object, and nothing asks for them", async () => {
  // DRIVN-45/AC-18 and AC-19: a fast or a full profile of its chosen objects. AC-12: a chosen
  // object's preview is in its details (DatabaseObjects.test.tsx), so the source has none of its own.
  sources = [fixture];
  renderApp();

  await screen.findByRole("region", { name: "drivnx_fixture" });
  const tab = screen.getByRole("tabpanel", { name: "Profile" });
  expect(await within(tab).findByRole("button", { name: "Fast profile" })).toBeEnabled();
  expect(within(tab).getByRole("button", { name: "Full profile" })).toBeEnabled();
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
  // DRIVN-45/AC-12 with AC-06: the preview is the read that finds the password refused.
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

test("a database's full profile is started with its time limit per object, 10 minutes unless changed", async () => {
  // DRIVN-45/AC-19 and AC-20: "Given a full profile and a per-table time limit"; the API keeps 1 to
  // 120 minutes, 10 by default (DRIVN-53).
  sources = [fixture];
  answers["/api/profile-runs"] = () =>
    Response.json({ ...databaseRun, mode: "full", status: "running" }, { status: 202 });
  const user = userEvent.setup();
  renderApp();
  const tab = await screen.findByRole("tabpanel", { name: "Profile" });
  const full = await within(tab).findByRole("button", { name: "Full profile" });
  expect(full).toBeEnabled();
  const minutes = within(tab).getByRole("spinbutton", { name: "Time limit per object, in minutes" });
  expect(minutes).toHaveValue(10);

  await user.click(full);
  await user.clear(minutes);
  await user.type(minutes, "25");
  await user.click(full);

  expect(posted("/api/profile-runs")).toEqual([
    { source_id: "p1", mode: "full", time_limit_minutes: 10 },
    { source_id: "p1", mode: "full", time_limit_minutes: 25 },
  ]);
});

test.each(["0", "121", "2.5", ""])(
  "a time limit of %j minutes starts nothing and says what it takes",
  async (typed) => {
    // DRIVN-45/AC-20 with the API's 1 to 120 minutes; WCAG 3.3.1, the error is named in text and
    // tied to its field.
    sources = [fixture];
    const user = userEvent.setup();
    renderApp();
    const tab = await screen.findByRole("tabpanel", { name: "Profile" });
    const minutes = await within(tab).findByRole("spinbutton", { name: "Time limit per object, in minutes" });

    await user.clear(minutes);
    if (typed) {
      await user.type(minutes, typed);
    }
    await user.click(within(tab).getByRole("button", { name: "Full profile" }));

    expect(posted("/api/profile-runs")).toEqual([]);
    expect(minutes).toHaveAttribute("aria-invalid", "true");
    expect(minutes).toHaveAccessibleDescription("Enter a whole number of minutes from 1 to 120.");
  },
);

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
  // DRIVN-45/AC-18 and AC-24: each object's scope (about N rows sampled by page, the first rows
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

const fullRun = { ...databaseRun, id: "r2", mode: "full" };

test.each([
  [{ method: "page_sample", rows: 98765 }, "2 patterns, from about 98,765 rows, sampled by page"],
  [{ method: "view_first_rows", rows: 3 }, "2 patterns, from the first 3 rows the view returned, not random"],
  [{ method: "first_rows", rows: 100000 }, "2 patterns, from the first 100,000 rows returned, not random"],
])("a full run's results say each object was read row by row, with patterns from its fast reading: %o", async (
  patternsFrom,
  label,
) => {
  // DRIVN-45/AC-19: "every row of each object is read, with exact results and each object's exact
  // row count"; the Assumptions: "masked patterns always come from the fast profile's sample rows, even on
  // a full profile, and are labelled that way", as AC-24 labels a fast reading (found in review).
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([fullRun]);
  gets["/api/findings?profile_run_id=r2"] = () => Response.json([]);
  gets["/api/profile-runs/r2"] = () =>
    Response.json({
      ...fullRun,
      file_changed: null,
      objects: [
        { object: "sales.orders", method: "full", stopped_at_time_limit: false, patterns_from: patternsFrom },
      ],
      metrics: [
        metric("sales.orders", null, "rows_read", 1234567, "full"),
        metric("sales.orders", "region", "distinct_count", 4, "full"),
        metric("sales.orders", "region", "masked_patterns", [
          { pattern: "AAAA", count: 90000, cut: false },
          { pattern: "AAAAA", count: 10000, cut: false },
        ]),
      ],
    });
  renderApp();

  const tab = await screen.findByRole("tabpanel", { name: "Profile" });
  const orders = await within(tab).findByRole("table");

  expect(orders.querySelector("caption")).toHaveTextContent("sales.orders: every row, 1,234,567 rows");
  expect(within(orders).getByRole("row", { name: /region/ })).toHaveTextContent(label);
});

test("an object stopped at its time limit shows it has no results, and the run names each such object", async () => {
  // DRIVN-45/AC-20: "the object shows 'stopped at the time limit, no results', the run goes on to
  // the next object, and the finished run says which objects have no results".
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([fullRun]);
  gets["/api/findings?profile_run_id=r2"] = () => Response.json([]);
  gets["/api/profile-runs/r2"] = () =>
    Response.json({
      ...fullRun,
      file_changed: null,
      objects: [
        { object: "sales.big", method: "full", stopped_at_time_limit: true },
        { object: "sales.orders", method: "full", stopped_at_time_limit: false },
        { object: "sales.slow_view", method: "full", stopped_at_time_limit: true },
      ],
      metrics: [
        metric("sales.big", null, "time_limit_reached", 10, "full"),
        metric("sales.orders", null, "rows_read", 3, "full"),
        metric("sales.orders", "region", "distinct_count", 2, "full"),
        metric("sales.slow_view", null, "time_limit_reached", 10, "full"),
      ],
    });
  renderApp();

  const tab = await screen.findByRole("tabpanel", { name: "Profile" });

  expect(await within(tab).findByText("No results for these objects, stopped at the time limit: sales.big, sales.slow_view.")).toBeInTheDocument();
  expect(within(tab).getByText("sales.big: stopped at the time limit, no results")).toBeInTheDocument();
  expect(within(tab).getByText("sales.slow_view: stopped at the time limit, no results")).toBeInTheDocument();
  expect(within(tab).getAllByRole("table").map((table) => table.querySelector("caption")?.textContent)).toEqual([
    "sales.orders: every row, 3 rows",
  ]);
});

// A run one of whose objects a refresh has since replaced (DRIVN-57).
const changedRun = { ...databaseRun, id: "r3", current_settings: false, object_names: ["sales.orders", "sales.small"] };
const changedDetail = {
  ...changedRun,
  file_changed: null,
  objects: [
    { object: "sales.orders", method: "page_sample", stopped_at_time_limit: false, out_of_date: true },
    { object: "sales.small", method: "whole", stopped_at_time_limit: false, out_of_date: false },
  ],
  metrics: [
    metric("sales.orders", null, "rows_read", 98412),
    metric("sales.orders", "region", "distinct_count", 4),
    metric("sales.small", null, "rows_read", 3, "full"),
    metric("sales.small", "id", "distinct_count", 3, "full"),
  ],
};

test("a database's newest completed run is shown even once a refresh has changed one of its objects", async () => {
  // DRIVN-45/AC-31: "the newest completed run is shown by default", unlike a file's, which must
  // have the current settings (DRIVN-9 AC-35); AC-32 says its results are out of date.
  sources = [fixture];
  gets["/api/profile-runs"] = () =>
    Response.json([changedRun, { ...databaseRun, object_names: ["sales.orders", "sales.small"] }]);
  gets["/api/findings?profile_run_id=r3"] = () => Response.json([]);
  gets["/api/profile-runs/r3"] = () => Response.json(changedDetail);
  renderApp();

  const tab = await screen.findByRole("tabpanel", { name: "Profile" });

  expect(await within(tab).findByText(`Run r3, finished ${new Date(changedRun.ended_at).toLocaleString()}`)).toBeInTheDocument();
  expect(within(screen.getByRole("banner")).getByText("Profile status: Completed")).toBeInTheDocument();
  expect(tab).not.toHaveTextContent("No profile has been run with these settings yet.");
});

test("a database's runs list names each run's objects, and says which runs are out of date", async () => {
  // DRIVN-45/AC-31: "list them with mode, status, time and objects"; AC-32.
  sources = [fixture];
  gets["/api/profile-runs"] = () =>
    Response.json([changedRun, { ...databaseRun, current_settings: true, object_names: ["sales.small"] }]);
  gets["/api/findings?profile_run_id=r3"] = () => Response.json([]);
  gets["/api/profile-runs/r3"] = () => Response.json(changedDetail);
  renderApp();

  const tab = await screen.findByRole("tabpanel", { name: "Profile" });
  const runs = within(await within(tab).findByRole("list", { name: "Runs of this database" })).getAllByRole("listitem");

  const started = new Date(databaseRun.started_at).toLocaleString();
  expect(runs.map((run) => run.textContent)).toEqual([
    `Fast profile, Completed, started ${started}, of sales.orders, sales.small · Out of date · Shown`,
    `Fast profile, Completed, started ${started}, of sales.small Open`,
  ]);
});

test("a database run's results say they describe each object as it was when the run read it", async () => {
  // DRIVN-45/AC-32: "Given results, then they say they describe the object at the run's time."
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([{ ...changedRun, current_settings: true }]);
  gets["/api/findings?profile_run_id=r3"] = () => Response.json([]);
  gets["/api/profile-runs/r3"] = () =>
    Response.json({
      ...changedDetail,
      current_settings: true,
      objects: changedDetail.objects.map((found) => ({ ...found, out_of_date: false })),
    });
  renderApp();

  const tab = await screen.findByRole("tabpanel", { name: "Profile" });

  expect(
    await within(tab).findByText(
      `These results describe each object as it was when this run read it, on ${new Date(changedRun.started_at).toLocaleString()}.`,
    ),
  ).toBeInTheDocument();
  expect(tab).not.toHaveTextContent("out of date");
});

test.each([
  [
    ["sales.orders"],
    "sales.orders has changed or is gone since this run, so its results are out of date and this run's findings aren't current. Profile again to see it as it is now.",
  ],
  [
    ["sales.orders", "sales.small"],
    "sales.orders, sales.small have changed or are gone since this run, so their results are out of date and this run's findings aren't current. Profile again to see them as they are now.",
  ],
])("a database run whose objects have changed since says their results are out of date: %o", async (changed, note) => {
  // DRIVN-45/AC-32: "Given an object whose columns or types differ from the run's, then the Profile
  // tab says the results are out of date."
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([changedRun]);
  gets["/api/findings?profile_run_id=r3"] = () => Response.json([]);
  gets["/api/profile-runs/r3"] = () =>
    Response.json({
      ...changedDetail,
      objects: changedDetail.objects.map((found) => ({ ...found, out_of_date: changed.includes(found.object) })),
    });
  renderApp();

  const tab = await screen.findByRole("tabpanel", { name: "Profile" });

  expect(await within(tab).findByText(note)).toBeInTheDocument();
  expect(within(tab).getAllByRole("table").map((table) => table.querySelector("caption")?.textContent)).toEqual([
    "sales.orders: about 98,412 rows, sampled by page (out of date)",
    changed.includes("sales.small") ? "sales.small: the whole object, 3 rows (out of date)" : "sales.small: the whole object, 3 rows",
  ]);
});

test("refreshing a database's objects shows at once which of the shown run's results are out of date", async () => {
  // DRIVN-45/AC-32: the refresh is what finds an object's columns changed (decided for DRIVN-57).
  const user = userEvent.setup();
  let refreshed = false;
  sources = [fixture];
  gets["/api/sources/p1/objects"] = () => Response.json({ discovered_at: "2026-11-17T08:00:00+00:00", schemas: [] });
  answers["/api/sources/p1/discover"] = () => {
    refreshed = true;
    return Response.json({ discovered_at: "2026-11-17T10:00:00+00:00", schemas: [] });
  };
  gets["/api/profile-runs"] = () => Response.json([{ ...changedRun, current_settings: !refreshed }]);
  gets["/api/findings?profile_run_id=r3"] = () => Response.json([]);
  gets["/api/profile-runs/r3"] = () =>
    Response.json({
      ...changedDetail,
      current_settings: !refreshed,
      objects: changedDetail.objects.map((found) => ({ ...found, out_of_date: refreshed && found.object === "sales.orders" })),
    });
  renderApp();
  const tab = await screen.findByRole("tabpanel", { name: "Profile" });
  await within(tab).findByText(/^These results describe each object/);
  expect(tab).not.toHaveTextContent("out of date");

  await user.click(await screen.findByRole("button", { name: "Refresh" }));

  expect(await within(tab).findByText(/^sales\.orders has changed or is gone since this run/)).toBeInTheDocument();
  expect(within(tab).getByRole("list", { name: "Runs of this database" })).toHaveTextContent("Out of date");
});

test("a running database run can be cancelled from the Profile tab", async () => {
  // DRIVN-45/AC-21: "Given a run in progress, when the user cancels it".
  sources = [fixture];
  gets["/api/profile-runs"] = () =>
    Response.json([{ ...fullRun, status: "running", ended_at: null, current_object: "sales.orders" }]);
  answers["/api/profile-runs/r2/cancel"] = () =>
    Response.json({ ...fullRun, status: "running", ended_at: null }, { status: 202 });
  const user = userEvent.setup();
  renderApp();
  const tab = await screen.findByRole("tabpanel", { name: "Profile" });

  await user.click(await within(tab).findByRole("button", { name: "Cancel profile" }));

  // A cancel has no body, so it's counted rather than read.
  expect(
    fetchMock.mock.calls.filter(([url, init]) => url === "/api/profile-runs/r2/cancel" && init?.method === "POST"),
  ).toHaveLength(1);
});

test("a running database run says which object it is on, in the header and the Profile tab", async () => {
  // DRIVN-45/AC-17
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
  // DRIVN-45/AC-16: "the screen names the objects ... and why, and the run starts only after the
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
  // DRIVN-45/AC-16: the run starts once the user confirms, and only what they were shown.
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
  // WCAG 2.4.3: the dialog closes with focus in it, so focus goes back to where the user started.
  expect(within(tab).getByRole("button", { name: "Fast profile" })).toHaveFocus();
});

test("profiling anyway after a full profile's warning starts it with its time limit, back on Full profile", async () => {
  // DRIVN-45/AC-16 with AC-20: the run the user asked for, as they asked for it; WCAG 2.4.3,
  // focus goes back to where the user started.
  sources = [fixture];
  let starts = 0;
  answers["/api/profile-runs"] = () =>
    starts++ === 0
      ? refusedFor(writerAccess)()
      : Response.json({ ...databaseRun, mode: "full", status: "running" }, { status: 202 });
  const user = userEvent.setup();
  renderApp();
  const tab = await screen.findByRole("tabpanel", { name: "Profile" });
  const minutes = await within(tab).findByRole("spinbutton", { name: "Time limit per object, in minutes" });
  await user.clear(minutes);
  await user.type(minutes, "30");
  await user.click(within(tab).getByRole("button", { name: "Full profile" }));

  await user.click(within(await within(tab).findByRole("alertdialog")).getByRole("button", { name: "Profile anyway" }));

  expect(posted("/api/profile-runs")).toEqual([
    { source_id: "p1", mode: "full", time_limit_minutes: 30 },
    { source_id: "p1", mode: "full", time_limit_minutes: 30, write_access: writerAccess },
  ]);
  expect(within(tab).getByRole("button", { name: "Full profile" })).toHaveFocus();
});

test("cancelling the write warning starts nothing and returns to Fast profile", async () => {
  // DRIVN-45/AC-16: no run without the user's confirmation; WCAG 2.4.3, focus goes back to where
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
  // DRIVN-45/AC-16: "the screen names the objects or the superuser role and why".
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
  // DRIVN-45/AC-16: "Given a role that can only read the chosen objects, then no warning appears,
  // and the screen says the account can only read them." Said of the run, as the selection can change
  // since, and of its account, as the role it signed in as can differ from the user name (found in review).
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([{ ...databaseRun, read_only: readOnly }]);
  gets["/api/findings?profile_run_id=r1"] = () => Response.json([]);
  gets["/api/profile-runs/r1"] = () =>
    Response.json({ ...databaseRun, read_only: readOnly, file_changed: null, objects: [], metrics: [] });
  renderApp();

  const tab = await screen.findByRole("tabpanel", { name: "Profile" });
  await within(tab).findByText(`Run r1, finished ${new Date(databaseRun.ended_at).toLocaleString()}`);

  expect(within(tab).queryByText("The account this run used could only read the objects it profiled.") !== null).toBe(
    said,
  );
});

test("the write warning takes focus on its safe choice, Cancel", async () => {
  // DRIVN-45/AC-16 and WCAG 2.4.3: an alert dialog moves focus into itself, onto the choice that
  // changes nothing, as the possibly-sensitive values' confirmation does.
  sources = [fixture];
  answers["/api/profile-runs"] = refusedFor(writerAccess);

  const { tab } = await startFastProfile();

  const dialog = await within(tab).findByRole("alertdialog");
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
});

test("a write warning shows even when a run elsewhere ends as the start is refused", async () => {
  // DRIVN-45/AC-16: the refusal mustn't be forgotten with a refusal for a run in progress, which
  // the end of the run elsewhere clears (found in review).
  sources = [fixture];
  let postedYet = false;
  const elsewhere = { ...databaseRun, id: "r9", source_id: "other", status: "running", ended_at: null };
  gets["/api/profile-runs"] = () => Response.json(postedYet ? [] : [elsewhere]);
  answers["/api/profile-runs"] = () => {
    postedYet = true;
    return refusedFor(writerAccess)();
  };
  const user = userEvent.setup();
  renderApp();
  const tab = await screen.findByRole("tabpanel", { name: "Profile" });
  await within(tab).findByText("A profile is running on another file.");

  await user.click(within(tab).getByRole("button", { name: "Fast profile" }));

  expect(await within(tab).findByRole("alertdialog")).toBeInTheDocument();
});

// A database run's finding, with the saved texts the server sends.
function databaseFinding(kind: string, evidence: object[]) {
  return {
    id: `f-${kind}`,
    profile_run_id: "r1",
    kind,
    interpretation: `Saved interpretation of ${kind}`,
    why_it_matters: `Saved reason for ${kind}`,
    recommended_action: `Saved action for ${kind}`,
    state: "inferred",
    object: (evidence[0] as { object: string }).object,
    evidence,
  };
}

// The orders table read whole, so no finding says it's from some rows only.
const wholeOrders = {
  ...databaseRun,
  file_changed: null,
  objects: [{ object: "sales.orders", method: "whole" }],
  metrics: [
    metric("sales.orders", null, "rows_read", 2500, "full"),
    metric("sales.orders", "region", "missing_count", 0, "full"),
    metric("sales.orders", "region", "distinct_count", 2500, "full"),
    metric("sales.orders", "region", "distinct_percent", 100, "full"),
  ],
};

async function findingsPanel() {
  const panel = await screen.findByRole("complementary", { name: "Intelligence & Review" });
  return within(panel).findByRole("region", { name: "Findings" });
}

test("a database run's finding says which object it was observed in", async () => {
  // DRIVN-45/AC-26: "the CSV profile's findings appear", each of one object.
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([databaseRun]);
  gets["/api/profile-runs/r1"] = () => Response.json(wholeOrders);
  gets["/api/findings?profile_run_id=r1"] = () =>
    Response.json([
      databaseFinding("possible_identifier", [
        metric("sales.orders", "region", "distinct_count", 2500, "full"),
        metric("sales.orders", "region", "distinct_percent", 100, "full"),
      ]),
    ]);
  renderApp();

  const shown = await within(await findingsPanel()).findByRole("article");

  expect(shown).toHaveTextContent("Saved interpretation of possible_identifier");
  expect(within(shown).getByText(/^Observed/).textContent).toBe(
    "Observed in sales.orders: region has 2,500 distinct values (100%).",
  );
});

test("before any profile of a database, the findings say what to do", async () => {
  // DRIVN-45/AC-26, as DRIVN-9/AC-21 says it for a file.
  sources = [fixture];
  renderApp();

  expect(
    await within(await findingsPanel()).findByText("Profile this database to see what it finds."),
  ).toBeInTheDocument();
});

const ORDER_PATTERNS = [{ pattern: "AAAA@AAAA.AAA", count: 40, cut: false }];

test.each([
  [
    "a page sample",
    { mode: "fast", objects: [{ object: "sales.orders", method: "page_sample" }], rows: 98412 },
    [
      metric("sales.orders", "region", "distinct_count", 98412),
      metric("sales.orders", "region", "distinct_percent", 100),
    ],
    "possible_identifier",
    "region has 98,412 distinct values (100%) in about 98,412 rows, sampled by page",
  ],
  [
    "a table's first rows",
    { mode: "fast", objects: [{ object: "sales.orders", method: "first_rows" }], rows: 100000 },
    [metric("sales.orders", "region", "distinct_count", 1)],
    "single_value",
    "every value in region is the same in the first 100,000 rows returned, not random",
  ],
  [
    "a view's first rows",
    { mode: "fast", objects: [{ object: "sales.orders", method: "view_first_rows" }], rows: 3 },
    [metric("sales.orders", "region", "distinct_count", 1)],
    "single_value",
    "every value in region is the same in the first 3 rows the view returned, not random",
  ],
  [
    "a full profile's patterns, from its fast reading",
    {
      mode: "full",
      objects: [
        { object: "sales.orders", method: "full", patterns_from: { method: "page_sample", rows: 98765 } },
      ],
      rows: 1234567,
    },
    [metric("sales.orders", "region", "masked_patterns", ORDER_PATTERNS)],
    "possibly_sensitive",
    "region has a value of this shape among its most common masked patterns in about 98,765 rows, sampled by page",
  ],
])("a database finding from %s says how its object was read", async (_, reading, evidence, kind, said) => {
  // DRIVN-45/AC-24 and AC-26: a finding is "observed in" its object's reading, as
  // DRIVN-9/AC-24 says a file's first rows; a full profile's patterns are its fast reading's.
  const shownRun = { ...databaseRun, mode: reading.mode };
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([shownRun]);
  gets["/api/profile-runs/r1"] = () =>
    Response.json({
      ...shownRun,
      file_changed: null,
      objects: reading.objects,
      metrics: [metric("sales.orders", null, "rows_read", reading.rows, reading.mode === "full" ? "full" : "sample")],
    });
  gets["/api/findings?profile_run_id=r1"] = () => Response.json([databaseFinding(kind, evidence)]);
  renderApp();

  const shown = await within(await findingsPanel()).findByRole("article");

  await waitFor(() =>
    expect(within(shown).getByText(/^Observed/).textContent).toBe(`Observed in sales.orders: ${said}.`),
  );
});

// Two objects with a column of the same name, so a link can only find its own object's cell. The finding is of
// the second, so an ID without the object would land in the first.
function twoObjects(object: string) {
  return [
    metric(object, null, "rows_read", 2500, "full"),
    metric(object, null, "duplicate_rows", object === "sales.returns" ? 3 : 0, "full"),
    metric(object, "region", "missing_count", object === "sales.returns" ? 12 : 0, "full"),
    metric(object, "region", "missing_percent", object === "sales.returns" ? 0.48 : 0, "full"),
    metric(object, "region", "distinct_count", object === "sales.returns" ? 2500 : 4, "full"),
    metric(object, "region", "distinct_percent", object === "sales.returns" ? 100 : 0.16, "full"),
    metric(object, "region", "masked_patterns", ORDER_PATTERNS),
  ];
}

test.each([
  ["possible_identifier", [metric("sales.returns", "region", "distinct_count", 2500, "full")], "2,500 (100%)"],
  ["missing_values", [metric("sales.returns", "region", "missing_count", 12, "full")], "12 (0.48%)"],
  ["possibly_sensitive", [metric("sales.returns", "region", "masked_patterns", ORDER_PATTERNS)], "1 pattern"],
  ["duplicate_rows", [metric("sales.returns", null, "duplicate_rows", 3, "full")], "3 rows are exact copies of another row."],
])("a database %s finding's evidence link goes to its own object's metric", async (kind, evidence, shownAs) => {
  // DRIVN-45/AC-26: "with their evidence links", to the cell of the object it's of.
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([fullRun]);
  gets["/api/profile-runs/r2"] = () =>
    Response.json({
      ...fullRun,
      file_changed: null,
      objects: ["sales.orders", "sales.returns"].map((object) => ({
        object,
        method: "full",
        stopped_at_time_limit: false,
        patterns_from: { method: "whole", rows: 2500 },
      })),
      metrics: [...twoObjects("sales.orders"), ...twoObjects("sales.returns")],
    });
  gets["/api/findings?profile_run_id=r2"] = () =>
    Response.json([{ ...databaseFinding(kind, evidence), profile_run_id: "r2" }]);
  renderApp();
  const shown = await within(await findingsPanel()).findByRole("article");
  const tab = await screen.findByRole("tabpanel", { name: "Profile" });
  const [, returns] = await within(tab).findAllByRole("table");

  await userEvent.click(within(shown).getByRole("button", { name: /evidence/i }));

  const focused = document.activeElement as HTMLElement;
  expect(focused.textContent).toContain(shownAs);
  // The object's own section: its table, or what's said under it.
  expect(focused.closest(".table-scroll")).toBe(returns.closest(".table-scroll"));
});

test("a database run's findings say having no flag doesn't mean no personal data", async () => {
  // DRIVN-45/AC-27: "the screen says that no flag doesn't mean a column has no personal data",
  // as DRIVN-9/AC-28 does for a file.
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([databaseRun]);
  gets["/api/profile-runs/r1"] = () => Response.json(wholeOrders);
  gets["/api/findings?profile_run_id=r1"] = () => Response.json([]);
  renderApp();

  const panel = await findingsPanel();

  expect(await within(panel).findByText("This profile found nothing to point out.")).toBeInTheDocument();
  expect(panel).toHaveTextContent("Having no possibly-sensitive flag doesn't mean a column has no personal data.");
});

test("when a database run's results don't load, its findings say so", async () => {
  // DRIVN-45/AC-26: a database run's findings say how each object was read, from its results, so without
  // them the findings can't show, and the panel says why rather than seeming to have found nothing.
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([databaseRun]);
  gets["/api/profile-runs/r1"] = () => Response.json({ detail: "no" }, { status: 500 });
  gets["/api/findings?profile_run_id=r1"] = () =>
    Response.json([databaseFinding("single_value", [metric("sales.orders", "region", "distinct_count", 1)])]);
  renderApp();

  const panel = await findingsPanel();

  expect(await within(panel).findByRole("alert")).toHaveTextContent("DrivnX couldn't load the findings.");
  expect(within(panel).queryByRole("article")).not.toBeInTheDocument();
});

test("the findings of a database run whose objects have changed since say why they aren't current", async () => {
  // DRIVN-45/AC-32: its results are out of date, and so are its findings; a database has no
  // settings, which a file's run says it used (DRIVN-9 AC-36).
  sources = [fixture];
  gets["/api/profile-runs"] = () => Response.json([changedRun]);
  gets["/api/findings?profile_run_id=r3"] = () => Response.json([]);
  gets["/api/profile-runs/r3"] = () => Response.json(changedDetail);
  renderApp();

  const findings = await findingsPanel();

  expect(
    await within(findings).findByText(
      "These findings are from a profile some of whose objects have changed or gone since, so they aren't current.",
    ),
  ).toBeInTheDocument();
  expect(findings).not.toHaveTextContent("used earlier settings");
});

test.each([
  [["s.a", "s.b", "s.c"], "of s.a, s.b, s.c"],
  [["s.a", "s.b", "s.c", "s.d"], "of s.a, s.b, s.c and 1 more"],
  [["s.a", "s.b", "s.c", "s.d", "s.e"], "of s.a, s.b, s.c and 2 more"],
])("a database run in the runs list names its first 3 objects, then how many more: %o", async (names, shownAs) => {
  // DRIVN-45/AC-31: "list them with ... objects"; the first 3, then "and N more" (decided for
  // DRIVN-57). The run's results name every one.
  sources = [fixture];
  gets["/api/profile-runs"] = () =>
    Response.json([{ ...databaseRun, current_settings: true, object_names: names }]);
  gets["/api/findings?profile_run_id=r1"] = () => Response.json([]);
  gets["/api/profile-runs/r1"] = () => Response.json({ ...databaseRun, file_changed: null, objects: [], metrics: [] });
  renderApp();

  const tab = await screen.findByRole("tabpanel", { name: "Profile" });
  const list = await within(tab).findByRole("list", { name: "Runs of this database" });

  const started = new Date(databaseRun.started_at).toLocaleString();
  expect(within(list).getByRole("listitem")).toHaveTextContent(
    `Fast profile, Completed, started ${started}, ${shownAs} · Shown`,
    { normalizeWhitespace: true },
  );
});
