import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";

const workspace = {
  location: "/home/me/Conflux/default",
  name: "Sales",
  ai_mode: "off",
  setup_complete: true,
};

const customers = {
  id: "s1",
  name: "customers.csv",
  path: "/data/customers.csv",
  delimiter: ",",
  encoding: "UTF-8",
  has_header: true,
  size_bytes: 1536,
  modified_at: "2024-03-05T10:00:00+00:00",
  available: true,
  columns: [
    { name: "id", physical_type: "BIGINT", normalised_type: "integer" },
    { name: "city", physical_type: "VARCHAR", normalised_type: "string" },
  ],
};

const fetchMock = vi.fn();
let sources: (typeof customers)[] = [];
let onConnect: (path: string) => Response;

beforeEach(() => {
  sources = [];
  onConnect = () => Response.json(customers, { status: 201 });
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === "/api/workspace") {
      return Response.json(workspace);
    }
    if (init?.method === "POST") {
      const response = onConnect(JSON.parse(String(init.body)).path);
      if (response.ok) {
        sources = [...sources, customers];
      }
      return response;
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

async function connect(path: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByRole("textbox", { name: "CSV file path" }), path);
  await user.click(screen.getByRole("button", { name: "Connect" }));
}

function postedPaths(): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([, init]) => JSON.parse(init.body).path);
}

test("with nothing connected, the explorer says so and offers to connect a file", async () => {
  renderApp();

  const explorer = await screen.findByRole("navigation", { name: "Data Explorer" });
  expect(within(explorer).getByRole("textbox", { name: "CSV file path" })).toBeInTheDocument();
  expect(await within(explorer).findByText(/no sources yet/i)).toBeInTheDocument();
});

test("connecting a file shows it in the explorer with its columns", async () => {
  // CFX-APP-first-slice/AC-20 and CFX-APP-first-slice/AC-24
  renderApp();

  await connect("/data/customers.csv");

  const explorer = screen.getByRole("navigation", { name: "Data Explorer" });
  expect(await within(explorer).findByRole("button", { name: /customers\.csv/ })).toBeInTheDocument();
  expect(within(explorer).getByText("id")).toBeInTheDocument();
  expect(within(explorer).getByText("city")).toBeInTheDocument();
  expect(postedPaths()).toEqual(["/data/customers.csv"]);
});

test("a connected file shows what the scan found, labelled as metadata", async () => {
  // CFX-APP-first-slice/AC-22, CFX-APP-first-slice/AC-24 and CFX-APP-first-slice/AC-25
  sources = [customers];
  renderApp();

  const details = await screen.findByRole("region", { name: "customers.csv" });
  expect(screen.getByRole("main")).toContainElement(details);
  expect(within(details).getByText("Metadata")).toBeInTheDocument();
  expect(within(details).getByText("/data/customers.csv")).toBeInTheDocument();
  expect(within(details).getByText("1.5 KB")).toBeInTheDocument();
  expect(within(details).getByText(/2024/)).toBeInTheDocument();
  expect(within(details).getByText("Comma")).toBeInTheDocument();
  expect(within(details).getByText("UTF-8")).toBeInTheDocument();
  expect(within(details).getByText("A row from the file was used as column names")).toBeInTheDocument();
  const rows = within(within(details).getByRole("table")).getAllByRole("row");
  expect(rows[1]).toHaveTextContent("id");
  expect(rows[1]).toHaveTextContent("integer");
  expect(rows[1]).toHaveTextContent("BIGINT");
  expect(details).not.toHaveTextContent(/\brows?\b(?! from the file)/i);
});

test.each([
  [",", "Comma"],
  [";", "Semicolon"],
  ["\t", "Tab"],
  ["|", "Pipe"],
])("the delimiter %j is named %s", async (delimiter, label) => {
  // CFX-APP-first-slice/AC-22
  sources = [{ ...customers, delimiter }];
  renderApp();

  expect(await screen.findByText(label)).toBeInTheDocument();
});

test("generated column names are shown as generated", async () => {
  // CFX-APP-first-slice/AC-22
  sources = [{ ...customers, has_header: false }];
  renderApp();

  expect(await screen.findByText(/no header row found/i)).toBeInTheDocument();
  expect(screen.getByText(/column names are generated/i)).toBeInTheDocument();
});

test("a pasted path loses surrounding spaces and quotes", async () => {
  // Windows' "Copy as path" adds quotes.
  renderApp();

  await connect('  "/data/my customers.csv"  ');

  await screen.findByRole("region", { name: "customers.csv" });
  expect(postedPaths()).toEqual(["/data/my customers.csv"]);
});

test("the path box empties after connecting, ready for another", async () => {
  renderApp();

  await connect("/data/customers.csv");

  await screen.findByRole("region", { name: "customers.csv" });
  expect(screen.getByRole("textbox", { name: "CSV file path" })).toHaveValue("");
});

test("a refused path shows the server's reason and keeps what was typed", async () => {
  // CFX-APP-first-slice/AC-21
  onConnect = () => Response.json({ detail: "There's no file at this path." }, { status: 422 });
  renderApp();

  await connect("/data/nothing.csv");

  expect(await screen.findByRole("alert")).toHaveTextContent("There's no file at this path.");
  expect(screen.getByRole("textbox", { name: "CSV file path" })).toHaveValue("/data/nothing.csv");
  expect(screen.queryByRole("region", { name: "customers.csv" })).not.toBeInTheDocument();
});

test("a validation error without a message gets a general one", async () => {
  onConnect = () => Response.json({ detail: [{ field: "body.path", reason: "x" }] }, { status: 422 });
  renderApp();

  await connect("/data/x.csv");

  expect(await screen.findByRole("alert")).toHaveTextContent(/check the path/i);
});

test.each([
  ["the session has ended", 401],
  ["the server failed", 500],
])("when %s, it says to start Conflux again", async (_case, status) => {
  onConnect = () => Response.json({ detail: "no" }, { status });
  renderApp();

  await connect("/data/x.csv");

  expect(await screen.findByRole("alert")).toHaveTextContent(/start conflux again/i);
});

test("a file that has moved is marked unavailable and the app keeps working", async () => {
  // CFX-APP-first-slice/AC-23
  sources = [{ ...customers, available: false }];
  renderApp();

  const explorer = await screen.findByRole("navigation", { name: "Data Explorer" });
  expect(await within(explorer).findByText(/unavailable/i)).toBeInTheDocument();
  const details = screen.getByRole("region", { name: "customers.csv" });
  expect(within(details).getByText(/can't find this file/i)).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "CSV file path" })).toBeEnabled();
});

test("choosing another source shows its details", async () => {
  const user = userEvent.setup();
  sources = [customers, { ...customers, id: "s2", name: "orders.csv", path: "/data/orders.csv" }];
  renderApp();

  await screen.findByRole("region", { name: "customers.csv" });
  await user.click(screen.getByRole("button", { name: /orders\.csv/ }));

  expect(await screen.findByRole("region", { name: "orders.csv" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "customers.csv" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /orders\.csv/ })).toHaveAttribute("aria-current", "true");
});

test("markup in a file or column name is shown as plain text", async () => {
  sources = [
    {
      ...customers,
      name: "<img src=x onerror=alert(1)>.csv",
      columns: [{ name: "<b>bold</b>", physical_type: "VARCHAR", normalised_type: "string" }],
    },
  ];
  renderApp();

  expect(await screen.findByRole("region", { name: "<img src=x onerror=alert(1)>.csv" })).toBeInTheDocument();
  expect(document.querySelector("img, b")).toBeNull();
});
