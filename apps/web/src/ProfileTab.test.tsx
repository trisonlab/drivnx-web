import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";

const POLLED_WITHIN = { timeout: 3000 };

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
  skip_lines: null,
  size_bytes: 1536,
  modified_at: "2024-03-05T10:00:00+00:00",
  available: true,
  columns: [{ name: "city", physical_type: "VARCHAR", normalised_type: "string" }],
};

type Run = {
  id: string;
  source_id: string;
  mode: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  current_settings: boolean;
};

function run(overrides: Partial<Run> = {}): Run {
  return {
    id: "run-1",
    source_id: "s1",
    mode: "fast",
    status: "completed",
    started_at: "2024-03-05T10:00:00+00:00",
    ended_at: "2024-03-05T10:00:01+00:00",
    current_settings: true,
    ...overrides,
  };
}

function metrics(scope: string) {
  const metric = (column: string | null, name: string, value: unknown) => ({
    column,
    metric: name,
    value,
    scope,
    precision: "exact",
  });
  return [
    metric(null, "rows_read", 100000),
    ...extraMetrics.map(([name, value]) => metric(null, name, value)),
    metric("id", "missing_count", 0),
    metric("city", "missing_count", 12),
    metric("city", "missing_percent", 0.01),
    metric("city", "distinct_count", 2500),
    metric("city", "distinct_percent", 2.5),
    metric("city", "not_fitting_count", 7),
    metric("city", "shortest_length", 3),
    metric("city", "longest_length", 41),
    metric("city", "average_length", 6.75),
    metric("city", "masked_patterns", [
      { pattern: "AAAAA", count: 900, cut: false },
      { pattern: "A".repeat(40), count: 2, cut: true },
    ]),
  ];
}

const fetchMock = vi.fn();
let runs: Run[];
let scope: string;
let onStart: () => Response;
let extraMetrics: [string, unknown][];

beforeEach(() => {
  extraMetrics = [];
  runs = [];
  scope = "full";
  onStart = () => {
    runs = [run({ status: "running", ended_at: null })];
    return Response.json(runs[0], { status: 202 });
  };
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === "/api/workspace") {
      return Response.json(workspace);
    }
    if (url === "/api/sources") {
      return Response.json([customers]);
    }
    if (url.includes("/rows")) {
      return Response.json({ columns: [], rows: [], offset: 0, has_next: false, skipped_lines: 0 });
    }
    if (url === "/api/profile-runs" && init?.method === "POST") {
      return onStart();
    }
    if (url === "/api/profile-runs") {
      return Response.json(runs);
    }
    if (url.startsWith("/api/profile-runs/")) {
      const found = runs.find((candidate) => url.endsWith(candidate.id));
      return Response.json({ ...found, metrics: metrics(scope) });
    }
    if (url.endsWith("/settings")) {
      runs = runs.map((candidate) => ({ ...candidate, current_settings: false }));
      return Response.json(customers);
    }
    throw new Error(`Unexpected request: ${url}`);
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

async function profilePanel() {
  return screen.findByRole("tabpanel", { name: "Profile" });
}

async function startButton() {
  return within(await profilePanel()).findByRole("button", { name: "Fast profile" });
}

test("choosing Fast profile starts a run for this source", async () => {
  // CFX-APP-csv-profile/AC-07
  renderApp();

  await userEvent.click(await startButton());

  const started = fetchMock.mock.calls.find(
    ([url, init]) => url === "/api/profile-runs" && init?.method === "POST",
  );
  expect(JSON.parse(String(started?.[1]?.body))).toEqual({ source_id: "s1", mode: "fast" });
});

test("a run in progress shows in the header and the Profile tab", async () => {
  // CFX-APP-csv-profile/AC-09
  runs = [run({ status: "running", ended_at: null })];
  renderApp();

  const banner = await screen.findByRole("banner");
  await within(banner).findByText("Profile status: Running on customers.csv");
  expect(within(await profilePanel()).getByRole("status")).toHaveTextContent(/profile is running/i);
});

test("the results appear by themselves when the run completes", async () => {
  // CFX-APP-csv-profile/AC-07
  renderApp();
  await userEvent.click(await startButton());
  await within(await profilePanel()).findByRole("status");

  runs = [run()];

  await within(await profilePanel()).findByRole("table", undefined, POLLED_WITHIN);
  expect(within(await screen.findByRole("banner")).getByText("Profile status: Completed")).toBeInTheDocument();
  expect(within(await profilePanel()).queryByRole("status")).not.toBeInTheDocument();
});

test("a second run is refused with the server's message", async () => {
  // CFX-APP-csv-profile/AC-09
  onStart = () =>
    Response.json(
      { detail: "A profile is already running. Wait for it to finish, then try again." },
      { status: 409 },
    );
  renderApp();

  await userEvent.click(await startButton());

  expect(await within(await profilePanel()).findByRole("alert")).toHaveTextContent(
    "A profile is already running",
  );
});

test("a metric from the first rows says so and claims no total", async () => {
  // CFX-APP-csv-profile/AC-15 and CFX-APP-csv-profile/AC-16
  runs = [run()];
  scope = "sample";
  renderApp();

  const table = await within(await profilePanel()).findByRole("table");

  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);
  for (const row of rows) {
    expect(row).toHaveTextContent("first 100,000 rows, not random");
  }
  expect(table).toHaveTextContent("Rows read");
  expect(await profilePanel()).not.toHaveTextContent(/total|whole file/i);
});

test("a metric from a whole file says so", async () => {
  // CFX-APP-csv-profile/AC-15 and CFX-APP-csv-profile/AC-17
  runs = [run()];
  renderApp();

  const table = await within(await profilePanel()).findByRole("table");

  for (const row of within(table).getAllByRole("row").slice(1)) {
    expect(row).toHaveTextContent("whole file");
  }
  expect(table).not.toHaveTextContent(/first 100,000/);
});

test("every metric shows its precision, and the results show their run", async () => {
  // CFX-APP-csv-profile/AC-15
  runs = [run()];
  renderApp();

  const panel = await profilePanel();
  const table = await within(panel).findByRole("table");

  for (const row of within(table).getAllByRole("row").slice(1)) {
    expect(row).toHaveTextContent("Exact");
  }
  expect(panel).toHaveTextContent("run-1");
  expect(panel).toHaveTextContent(new Date("2024-03-05T10:00:01+00:00").toLocaleString());
});

test("a run on earlier settings isn't shown as the current results", async () => {
  // Until T11 labels them. Nothing says these came from a file read another way.
  runs = [run({ current_settings: false })];
  renderApp();

  const panel = await profilePanel();
  await within(panel).findByText("No profile has been run with these settings yet.");
  expect(within(panel).queryByRole("table")).not.toBeInTheDocument();
  expect(within(await screen.findByRole("banner")).getByText("Profile status: Not run yet")).toBeInTheDocument();
});

test("saving a correction stops showing the earlier results", async () => {
  // Until T11 labels them. The list has to be read again for that to show.
  runs = [run()];
  renderApp();
  const panel = await profilePanel();
  await within(panel).findByRole("table");

  await userEvent.click(await screen.findByText("How this file is read"));
  await userEvent.click(await screen.findByRole("button", { name: "Save settings" }));

  await waitFor(() => expect(within(panel).queryByRole("table")).not.toBeInTheDocument());
});

test("a run that failed says so, and earlier results stay", async () => {
  // CFX-APP-csv-profile/AC-09: T07 gives each failure its own message.
  runs = [run({ id: "run-2", status: "failed" }), run()];
  renderApp();

  const panel = await profilePanel();

  expect(await within(panel).findByRole("alert")).toHaveTextContent("couldn't finish");
  expect(await within(panel).findByRole("table")).toBeInTheDocument();
  expect(panel).toHaveTextContent("run-1");
  expect(within(await screen.findByRole("banner")).getByText("Profile status: Failed")).toBeInTheDocument();
});

test("with no source, the Profile tab says what to do first", async () => {
  // CFX-APP-csv-profile/AC-07
  fetchMock.mockImplementation(async (url: string) =>
    url === "/api/workspace" ? Response.json(workspace) : Response.json([]),
  );
  renderApp();

  const panel = await profilePanel();

  expect(panel).toHaveTextContent("Connect a CSV file");
  expect(within(panel).queryByRole("button", { name: "Fast profile" })).not.toBeInTheDocument();
  expect(within(await screen.findByRole("banner")).getByText("Profile status: Not run yet")).toBeInTheDocument();
});

test("if the runs can't be read, the header doesn't claim there are none", async () => {
  // CFX-APP-csv-profile/AC-09
  const answer = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
    url === "/api/profile-runs" ? new Response(null, { status: 500 }) : answer?.(url, init),
  );
  renderApp();

  const banner = await screen.findByRole("banner");

  await within(banner).findByText("Profile status: Not known");
  expect(await profilePanel()).not.toHaveTextContent("No profile has been run");
});

test("results that can't be read say so", async () => {
  // CFX-APP-csv-profile/AC-07
  runs = [run()];
  const answer = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
    url.startsWith("/api/profile-runs/") ? new Response(null, { status: 500 }) : answer?.(url, init),
  );
  renderApp();

  expect(await within(await profilePanel()).findByRole("alert")).toHaveTextContent("couldn't load these results");
});

test("a refusal because a run is in progress clears once that run ends", async () => {
  // CFX-APP-csv-profile/AC-09: left on screen, it would say a finished run is still going.
  runs = [run({ source_id: "s2", status: "running", ended_at: null })];
  onStart = () =>
    Response.json(
      { detail: "A profile is already running. Wait for it to finish, then try again." },
      { status: 409 },
    );
  renderApp();
  await screen.findByText("Profile status: Running");
  await userEvent.click(await startButton());
  const panel = await profilePanel();
  await within(panel).findByRole("alert");

  runs = [run({ source_id: "s2" })];

  await waitFor(() => expect(within(panel).queryByRole("alert")).not.toBeInTheDocument(), POLLED_WITHIN);
});

test("a start refused by a run this page didn't know about shows that run", async () => {
  // CFX-APP-csv-profile/AC-09: another tab started it after this page last read the runs.
  renderApp();
  const button = await startButton();
  await screen.findByText("Profile status: Not run yet");
  onStart = () => {
    runs = [run({ status: "running", ended_at: null })];
    return Response.json(
      { detail: "A profile is already running. Wait for it to finish, then try again." },
      { status: 409 },
    );
  };

  await userEvent.click(button);

  await screen.findByText("Profile status: Running on customers.csv");
  expect(within(await profilePanel()).getByRole("status")).toHaveTextContent(/profile is running/i);
});

test("a run this page learned of from a refusal shows its results when it ends", async () => {
  // CFX-APP-csv-profile/AC-09: the page keeps asking for as long as the run goes on.
  renderApp();
  const button = await startButton();
  onStart = () => {
    runs = [run({ status: "running", ended_at: null })];
    return Response.json({ detail: "A profile is already running." }, { status: 409 });
  };
  await userEvent.click(button);
  await screen.findByText("Profile status: Running on customers.csv");

  runs = [run()];

  const panel = await profilePanel();
  await within(panel).findByRole("table", undefined, POLLED_WITHIN);
  expect(screen.getByText("Profile status: Completed")).toBeInTheDocument();
  expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();
});

test("a run on another file shows in the Profile tab too", async () => {
  // CFX-APP-csv-profile/AC-09: the header and the tab both show a run in progress.
  runs = [run({ source_id: "s2", status: "running", ended_at: null })];
  renderApp();

  const panel = await profilePanel();

  expect(await within(panel).findByRole("status")).toHaveTextContent("A profile is running on another file.");
});

async function rowFor(column: string) {
  const table = await within(await profilePanel()).findByRole("table");
  return within(table).getByRole("row", { name: new RegExp(`^${column}\\b`) });
}

test("each column shows every metric, on one row", async () => {
  // CFX-APP-csv-profile/AC-14
  runs = [run()];
  renderApp();

  const row = await rowFor("city");

  const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent);
  expect(cells.slice(2, 9)).toEqual(["100,000", "12 (0.01%)", "2,500 (2.5%)", "7", "3", "41", "6.75"]);
});

test("a metric that wasn't saved shows as a dash", async () => {
  // CFX-APP-csv-profile/AC-14: nothing to divide by, or a run from before these metrics existed.
  runs = [run()];
  renderApp();

  const row = await rowFor("id");

  const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent);
  expect(cells.slice(2, 10)).toEqual(["100,000", "0", "\u2014", "\u2014", "\u2014", "\u2014", "\u2014", "\u2014"]);
});

test("the most common patterns open from their cell, with their counts", async () => {
  // CFX-APP-csv-profile/AC-14
  runs = [run()];
  renderApp();
  const row = await rowFor("city");

  await userEvent.click(within(row).getByText("2 patterns"));

  expect(within(row).getByText("AAAAA").closest("li")).toHaveTextContent("AAAAA \u00d7 900");
});

test("a cut pattern says so", async () => {
  // CFX-APP-csv-profile/AC-20
  runs = [run()];
  renderApp();
  const row = await rowFor("city");

  await userEvent.click(within(row).getByText("2 patterns"));

  expect(within(row).getByText("A".repeat(40)).closest("li")).toHaveTextContent("cut at 40 characters");
  expect(within(row).getByText("AAAAA").closest("li")).not.toHaveTextContent("cut at");
});

test.each([
  [1, "Some rows have 1 more field than the columns shown, and it isn't measured."],
  [2, "Some rows have 2 more fields than the columns shown, and they aren't measured."],
])("%i surplus columns are mentioned", async (count, message) => {
  // CFX-APP-csv-profile/AC-14, by the user's decision: they have no saved field to give metrics to.
  extraMetrics = [["extra_columns", count]];
  runs = [run()];
  renderApp();

  expect(await within(await profilePanel()).findByText(message)).toBeInTheDocument();
});

test("a file with no surplus columns doesn't mention them", async () => {
  // CFX-APP-csv-profile/AC-14
  runs = [run()];
  renderApp();
  await rowFor("city");

  expect(await profilePanel()).not.toHaveTextContent("more field");
});

test("what a metric was measured on comes straight after its column", async () => {
  // CFX-APP-csv-profile/AC-15: the table scrolls on a smaller screen, and these must stay in view.
  runs = [run()];
  scope = "sample";
  renderApp();

  const row = await rowFor("city");

  const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent);
  expect(cells.slice(0, 2)).toEqual(["first 100,000 rows, not random", "Exact"]);
});
