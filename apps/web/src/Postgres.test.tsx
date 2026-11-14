import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import { UNTIL_STOP } from "./api";

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

beforeEach(() => {
  sources = [];
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
    if (url === "/api/workspace") {
      return Response.json(workspace);
    }
    if (url === "/api/profile-runs") {
      return Response.json([]);
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
  expect(within(details).queryByRole("form")).not.toBeInTheDocument();
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

test("a database source's profile and preview aren't available yet, and nothing asks for its rows", async () => {
  sources = [fixture];
  renderApp();

  await screen.findByRole("region", { name: "drivnx_fixture" });
  expect(screen.getByRole("tabpanel", { name: "Profile" })).toHaveTextContent(
    "Profiling a database Not available yet",
  );
  expect(screen.getByRole("region", { name: "Preview" })).toHaveTextContent("Not available yet");
  expect(screen.queryByRole("button", { name: /fast profile/i })).not.toBeInTheDocument();
  const review = screen.getByRole("complementary", { name: "Intelligence & Review" });
  expect(review).not.toHaveTextContent(/profile this file/i);
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("p1"))).toEqual([]);
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
