import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";

const POLLED_WITHIN = { timeout: 3000 };

const workspace = {
  location: "/home/me/DrivnX/default",
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
  failure?: string | null;
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
    scope: name === "masked_patterns" ? (patternsScope ?? scope) : scope,
    precision: "exact",
  });
  return [
    metric(null, "rows_read", rowsRead),
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
let patternsScope: string | undefined;
let rowsRead: number;
let findings: unknown[];
let findingsOf: string;
let onStart: () => Response;
let onCancel: (url: string) => Response;
let extraMetrics: [string, unknown][];
let onValues: (url: string) => Response;
let fileChanged: boolean | null;

beforeEach(() => {
  fileChanged = false;
  extraMetrics = [];
  onValues = () => Response.json(cityValues);
  runs = [];
  scope = "full";
  patternsScope = undefined;
  rowsRead = 100000;
  findings = [];
  findingsOf = "run-1";
  onStart = () => {
    runs = [run({ status: "running", ended_at: null })];
    return Response.json(runs[0], { status: 202 });
  };
  onCancel = () => {
    runs = runs.map((candidate) => ({ ...candidate, status: "cancelled" }));
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
    if (url.startsWith("/api/findings?")) {
      return Response.json(url.endsWith(`profile_run_id=${findingsOf}`) ? findings : []);
    }
    if (url.endsWith("/cancel") && init?.method === "POST") {
      return onCancel(url);
    }
    if (url.includes("/values?")) {
      return onValues(url);
    }
    if (url.startsWith("/api/profile-runs/")) {
      const found = runs.find((candidate) => url.endsWith(candidate.id));
      return Response.json({ ...found, metrics: metrics(scope), file_changed: fileChanged });
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

test("choosing Full profile starts a full run for this source", async () => {
  // CFX-APP-csv-profile/AC-08
  renderApp();

  await userEvent.click(await within(await profilePanel()).findByRole("button", { name: "Full profile" }));

  const started = fetchMock.mock.calls.find(
    ([url, init]) => url === "/api/profile-runs" && init?.method === "POST",
  );
  expect(JSON.parse(String(started?.[1]?.body))).toEqual({ source_id: "s1", mode: "full" });
});

test("a run in progress shows in the header and the Profile tab", async () => {
  // CFX-APP-csv-profile/AC-09
  runs = [run({ status: "running", ended_at: null })];
  renderApp();

  const banner = await screen.findByRole("banner");
  await within(banner).findByText("Profile status: Running on customers.csv");
  expect(within(await profilePanel()).getByRole("status")).toHaveTextContent(/profile is running/i);
});

test("a run in progress on this file can be cancelled", async () => {
  // CFX-APP-csv-profile/AC-10
  runs = [run({ status: "running", ended_at: null })];
  renderApp();

  await userEvent.click(await within(await profilePanel()).findByRole("button", { name: "Cancel profile" }));

  expect(
    fetchMock.mock.calls.some(([url, init]) => url === "/api/profile-runs/run-1/cancel" && init?.method === "POST"),
  ).toBe(true);
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

test.each([
  [1, "This file has 1 row."],
  [100000, "This file has 100,000 rows."],
])("a run that read the whole file of %i rows says how many it has", async (rows, said) => {
  // CFX-APP-csv-profile/AC-08
  runs = [run({ mode: "full" })];
  rowsRead = rows;
  renderApp();

  expect(await within(await profilePanel()).findByText(said)).toBeInTheDocument();
});

test("a run of the first rows doesn't say how many rows the file has", async () => {
  // CFX-APP-csv-profile/AC-16
  runs = [run()];
  scope = "sample";
  renderApp();

  await within(await profilePanel()).findByRole("table");
  expect(await profilePanel()).not.toHaveTextContent("This file has");
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
  // CFX-APP-csv-profile/AC-36
  runs = [run({ current_settings: false })];
  renderApp();

  const panel = await profilePanel();
  await within(panel).findByText("No profile has been run with these settings yet.");
  expect(within(panel).queryByRole("table")).not.toBeInTheDocument();
  expect(within(await screen.findByRole("banner")).getByText("Profile status: Not run yet")).toBeInTheDocument();
});

test("saving a correction stops showing the earlier results", async () => {
  // CFX-APP-csv-profile/AC-36: the list has to be read again for that to show.
  runs = [run()];
  renderApp();
  const panel = await profilePanel();
  await within(panel).findByRole("table");

  await userEvent.click(await screen.findByText("How this file is read"));
  await userEvent.click(await screen.findByRole("button", { name: "Save settings" }));

  await waitFor(() => expect(within(panel).queryByRole("table")).not.toBeInTheDocument());
});

async function runsList() {
  return within(await profilePanel()).findByRole("list", { name: "Runs of this file" });
}

test("every run of this file is listed with its mode, status and time", async () => {
  // CFX-APP-csv-profile/AC-35
  runs = [
    run({ id: "run-3", status: "failed", started_at: "2024-03-07T09:00:00+00:00" }),
    run({ id: "run-2", mode: "full", started_at: "2024-03-06T09:00:00+00:00" }),
    run({ id: "run-1", started_at: "2024-03-05T09:00:00+00:00" }),
    run({ id: "run-9", source_id: "another" }),
  ];
  renderApp();

  const items = within(await runsList()).getAllByRole("listitem");

  expect(items).toHaveLength(3);
  expect(items[0]).toHaveTextContent(/Fast profile.*Failed/);
  expect(items[0]).toHaveTextContent(new Date("2024-03-07T09:00:00+00:00").toLocaleString());
  expect(items[1]).toHaveTextContent(/Full profile.*Completed/);
  expect(items[1]).toHaveTextContent(new Date("2024-03-06T09:00:00+00:00").toLocaleString());
  expect(items[2]).toHaveTextContent(/Fast profile.*Completed/);
});

test("the newest completed run with the current settings is shown, and any completed one opens", async () => {
  // CFX-APP-csv-profile/AC-35
  runs = [run({ id: "run-3", status: "failed" }), run({ id: "run-2", mode: "full" }), run({ id: "run-1" })];
  findingsOf = "run-1";
  findings = [finding("single_value", [{ column: "city", metric: "distinct_count", value: 1 }])];
  renderApp();
  const panel = await profilePanel();
  await within(panel).findByText(/Run run-2, finished/);
  const items = within(await runsList()).getAllByRole("listitem");
  expect(items[1]).toHaveTextContent("Shown");
  expect(within(items[0]).queryByRole("button")).not.toBeInTheDocument();
  expect(within(await findingsPanel()).queryByRole("article")).not.toBeInTheDocument();

  await userEvent.click(within(items[2]).getByRole("button", { name: /Open/ }));

  expect(await within(panel).findByText(/Run run-1, finished/)).toBeInTheDocument();
  expect(panel).not.toHaveTextContent("Run run-2");
  // The findings panel follows, so a finding's evidence link goes to the run it came from.
  expect(await within(await findingsPanel()).findByRole("article")).toHaveTextContent("single_value");
});

test("starting a profile goes back to showing the newest results", async () => {
  // CFX-APP-csv-profile/AC-35
  runs = [run({ id: "run-2" }), run({ id: "run-1" })];
  onStart = () => {
    runs = [run({ id: "run-3" }), ...runs];
    return Response.json(runs[0], { status: 202 });
  };
  renderApp();
  const panel = await profilePanel();
  const items = within(await runsList()).getAllByRole("listitem");
  await userEvent.click(within(items[1]).getByRole("button", { name: /Open/ }));
  await within(panel).findByText(/Run run-1, finished/);

  await userEvent.click(await startButton());

  expect(await within(panel).findByText(/Run run-3, finished/)).toBeInTheDocument();
});

test("a run on earlier settings is labelled, and once opened says its findings aren't current", async () => {
  // CFX-APP-csv-profile/AC-36
  runs = [run({ current_settings: false })];
  findings = [finding("single_value", [{ column: "city", metric: "distinct_count", value: 1 }])];
  renderApp();
  const panel = await profilePanel();
  const [item] = within(await runsList()).getAllByRole("listitem");
  expect(item).toHaveTextContent("Used earlier settings");
  expect(within(await findingsPanel()).queryByRole("article")).not.toBeInTheDocument();

  await userEvent.click(within(item).getByRole("button", { name: /Open/ }));

  expect(await within(panel).findByText(/Run run-1, finished/)).toBeInTheDocument();
  expect(panel).toHaveTextContent("This profile used earlier settings, so its findings aren't current.");
  const findingsShown = await findingsPanel();
  await within(findingsShown).findByRole("article");
  expect(findingsShown).toHaveTextContent("These findings are from a profile that used earlier settings, so they aren't current.");
});

test("results from a file that changed since its run may be out of date", async () => {
  // CFX-APP-csv-profile/AC-37
  runs = [run()];
  fileChanged = true;
  renderApp();

  expect(
    await within(await profilePanel()).findByText(
      "This file has changed since this profile ran, so these results may be out of date.",
    ),
  ).toBeInTheDocument();
});

test.each([false, null])("results from a file that hasn't changed, or can't be looked at, say nothing (%s)", async (changed) => {
  // CFX-APP-csv-profile/AC-37
  runs = [run()];
  fileChanged = changed;
  renderApp();
  const panel = await profilePanel();

  await within(panel).findByRole("table");

  expect(panel).not.toHaveTextContent("may be out of date");
});

test("a run that was in progress when DrivnX stopped says so, and earlier results stay", async () => {
  // CFX-APP-csv-profile/AC-38
  runs = [run({ id: "run-2", status: "interrupted" }), run()];
  renderApp();
  const panel = await profilePanel();

  expect(await within(panel).findByText("This profile was interrupted when DrivnX stopped.")).toBeInTheDocument();
  expect(await within(panel).findByRole("table")).toBeInTheDocument();
  expect(panel).toHaveTextContent("The results below are from the run before.");
});

test("a source whose only run was interrupted says so in the tab", async () => {
  // CFX-APP-csv-profile/AC-38: from T04, the tab was empty while the header said "Interrupted".
  runs = [run({ status: "interrupted" })];
  renderApp();
  const panel = await profilePanel();

  expect(await within(panel).findByText("This profile was interrupted when DrivnX stopped.")).toBeInTheDocument();
  expect(panel).not.toHaveTextContent("from the run before");
});

test("a run that failed says so, and earlier results stay", async () => {
  // CFX-APP-csv-profile/AC-09: T07 gives each failure its own message.
  runs = [run({ id: "run-2", status: "failed" }), run()];
  renderApp();

  const panel = await profilePanel();

  expect(await within(panel).findByRole("alert")).toHaveTextContent("couldn't finish");
  expect(await within(panel).findByRole("table")).toBeInTheDocument();
  // CFX-APP-csv-profile/AC-11: they aren't the failed run's.
  expect(panel).toHaveTextContent("The results below are from the run before.");
  expect(panel).toHaveTextContent("run-1");
  expect(within(await screen.findByRole("banner")).getByText("Profile status: Failed")).toBeInTheDocument();
});

test("a cancelled run says so, and its results don't appear as current", async () => {
  // CFX-APP-csv-profile/AC-10
  runs = [run({ id: "run-2", status: "cancelled" }), run()];
  renderApp();

  const panel = await profilePanel();

  expect(await within(panel).findByText("This profile was cancelled.")).toBeInTheDocument();
  expect(await within(panel).findByRole("table")).toBeInTheDocument();
  expect(panel).toHaveTextContent("The results below are from the run before.");
  expect(panel).toHaveTextContent("run-1");
  expect(within(await screen.findByRole("banner")).getByText("Profile status: Cancelled")).toBeInTheDocument();
});

test("a failed run says why, in the server's words", async () => {
  // CFX-APP-csv-profile/AC-11
  runs = [run({ status: "failed", failure: "DrivnX can't find this file. It may have moved or been deleted." })];
  renderApp();

  const panel = await profilePanel();

  expect(await within(panel).findByRole("alert")).toHaveTextContent(
    "This profile couldn't finish. DrivnX can't find this file. It may have moved or been deleted.",
  );
  expect(panel).not.toHaveTextContent("from the run before");
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
  // CFX-APP-csv-profile/AC-10: it's cancelled from its own file's tab.
  expect(within(panel).queryByRole("button", { name: "Cancel profile" })).not.toBeInTheDocument();
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

test("a full profile's patterns say they come from the first rows", async () => {
  // CFX-APP-csv-profile/AC-18
  runs = [run({ mode: "full" })];
  patternsScope = "sample";
  renderApp();
  const row = await rowFor("city");

  expect(within(row).getAllByRole("cell")[0]).toHaveTextContent("whole file");
  expect(within(row).getByText(/2 patterns/)).toHaveTextContent("2 patterns, from the first 100,000 rows, not random");
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

test.each([
  [1, "1 line above the table was left out, as in the preview."],
  [40, "40 lines above the table were left out, as in the preview."],
])("%i lines left out above the table are mentioned", async (count, message) => {
  // As the preview says (first slice, AC-27), or a short count would look like the whole file.
  extraMetrics = [["skipped_lines", count]];
  runs = [run()];
  renderApp();

  expect(await within(await profilePanel()).findByText(message)).toBeInTheDocument();
});

test("a file with no lines left out above the table doesn't mention them", async () => {
  runs = [run()];
  renderApp();
  await rowFor("city");

  expect(await profilePanel()).not.toHaveTextContent("above the table");
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

function finding(kind: string, evidence: { column: string | null; metric: string; value: unknown }[], scope = "full") {
  return {
    id: `finding-${kind}`,
    profile_run_id: "run-1",
    kind,
    interpretation: `Saved interpretation of ${kind}`,
    why_it_matters: `Saved reason for ${kind}`,
    recommended_action: `Saved action for ${kind}`,
    state: "inferred",
    evidence: evidence.map((cited) => ({ ...cited, scope, precision: "exact" })),
  };
}

async function findingsPanel() {
  const panel = await screen.findByRole("complementary", { name: "Intelligence & Review" });
  return within(panel).findByRole("region", { name: "Findings" });
}

test("a finding says what was observed and inferred, why it matters and what to do, as Inferred", async () => {
  // CFX-APP-csv-profile/AC-23
  runs = [run()];
  findings = [
    finding("possible_identifier", [
      { column: "city", metric: "distinct_count", value: 2500 },
      { column: "city", metric: "distinct_percent", value: 100 },
    ]),
  ];
  renderApp();

  const shown = await within(await findingsPanel()).findByRole("article");

  expect(shown).toHaveTextContent("Saved interpretation of possible_identifier");
  expect(shown).toHaveTextContent("Inferred");
  expect(shown).toHaveTextContent("Observed: city has 2,500 distinct values (100%).");
  expect(shown).toHaveTextContent("Why it matters: Saved reason for possible_identifier");
  expect(shown).toHaveTextContent("Recommended action: Saved action for possible_identifier");
});

test.each([
  ["not_fitting", [{ column: "amount", metric: "not_fitting_count", value: 7 }], "amount has 7 values that don't fit its type"],
  ["not_fitting", [{ column: "amount", metric: "not_fitting_count", value: 1 }], "amount has 1 value that doesn't fit its type"],
  ["duplicate_rows", [{ column: null, metric: "duplicate_rows", value: 3 }], "3 rows are exact copies of another row"],
  ["duplicate_rows", [{ column: null, metric: "duplicate_rows", value: 1 }], "1 row is an exact copy of another row"],
  [
    "missing_values",
    [
      { column: "note", metric: "missing_count", value: 7500 },
      { column: "note", metric: "missing_percent", value: 25 },
    ],
    "note is missing 7,500 values (25%)",
  ],
  ["single_value", [{ column: "country", metric: "distinct_count", value: 1 }], "every value in country is the same"],
  [
    "missing_values",
    [
      { column: "note", metric: "missing_count", value: 1 },
      { column: "note", metric: "missing_percent", value: 25 },
    ],
    "note is missing 1 value (25%)",
  ],
  [
    "possible_identifier",
    [
      { column: "id", metric: "distinct_count", value: 1 },
      { column: "id", metric: "distinct_percent", value: 100 },
    ],
    "id has 1 distinct value (100%)",
  ],
])("a %s finding says what was observed: %#", async (kind, evidence, said) => {
  // CFX-APP-csv-profile/AC-23
  runs = [run()];
  findings = [finding(kind, evidence)];
  renderApp();

  const shown = await within(await findingsPanel()).findByRole("article");
  // Exact, as toHaveTextContent would hide a doubled space.
  expect(within(shown).getByText(/^Observed:/).textContent).toBe(`Observed: ${said}.`);
});

const PATTERNS = [{ pattern: "AAAA@AAAA.AAA", count: 40, cut: false }];

test.each([
  [
    "its name matched",
    [{ column: "city", metric: "missing_count", value: 12 }],
    "the column is named city",
  ],
  [
    "its patterns matched",
    [{ column: "city", metric: "masked_patterns", value: PATTERNS }],
    "city has a value of this shape among its most common masked patterns in the first 100,000 rows",
  ],
  [
    "both matched",
    [
      { column: "city", metric: "masked_patterns", value: PATTERNS },
      { column: "city", metric: "missing_count", value: 12 },
    ],
    "the column is named city, and it has a value of this shape among its most common masked patterns in the first 100,000 rows",
  ],
])("a possibly-sensitive finding says what was observed when %s", async (_, evidence, said) => {
  // CFX-APP-csv-profile/AC-27: it cites the missing count when the name matched, and the
  // patterns, which come from the first rows, when they did. A name isn't from the first rows.
  runs = [run()];
  scope = "sample";
  findings = [finding("possibly_sensitive", evidence, "sample")];
  renderApp();

  const shown = await within(await findingsPanel()).findByRole("article");
  expect(within(shown).getByText(/^Observed:/).textContent).toBe(`Observed: ${said}.`);
});

test("a finding from the first rows only says so", async () => {
  // CFX-APP-csv-profile/AC-24, as revised in revision 3
  runs = [run()];
  scope = "sample";
  findings = [
    finding(
      "possible_identifier",
      [
        { column: "city", metric: "distinct_count", value: 99500 },
        { column: "city", metric: "distinct_percent", value: 99.5 },
      ],
      "sample",
    ),
  ];
  renderApp();

  const shown = await within(await findingsPanel()).findByRole("article");

  expect(within(shown).getByText(/^Observed:/).textContent).toBe(
    "Observed: city has 99,500 distinct values (99.5%) in the first 100,000 rows.",
  );
});

test.each([
  ["fast", true],
  ["full", false],
] as const)("after a %s profile, whether duplicate rows need a full profile is said: %s", async (mode, said) => {
  // CFX-APP-csv-profile/AC-22
  runs = [run({ mode })];
  renderApp();

  const panel = await findingsPanel();
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/findings?"))).toBe(true));

  expect(within(panel).queryByText("Duplicate rows are checked only by a full profile.") !== null).toBe(said);
});

test.each([
  [3, "3 rows are exact copies of another row."],
  [1, "1 row is an exact copy of another row."],
  [0, "No row is an exact copy of another row."],
])("a full profile says how many rows copy another: %i", async (copies, said) => {
  // CFX-APP-csv-profile/AC-22: the count a duplicate-rows finding cites.
  runs = [run({ mode: "full" })];
  extraMetrics = [["duplicate_rows", copies]];
  renderApp();

  expect(await within(await profilePanel()).findByText(said)).toBeInTheDocument();
});

test("a finding's evidence link goes to the metric it cites, with its run", async () => {
  // CFX-APP-csv-profile/AC-23
  runs = [run()];
  findings = [
    finding("possible_identifier", [
      { column: "city", metric: "distinct_count", value: 2500 },
      { column: "city", metric: "distinct_percent", value: 2.5 },
    ]),
  ];
  renderApp();
  const shown = await within(await findingsPanel()).findByRole("article");
  const table = await within(await profilePanel()).findByRole("table");

  await userEvent.click(within(shown).getByRole("button", { name: /evidence/i }));

  expect(document.activeElement).toBe(within(await rowFor("city")).getByText("2,500 (2.5%)"));
  expect(table).toHaveTextContent("Run run-1");
});

test.each([
  ["duplicate_rows", { column: null, metric: "duplicate_rows", value: 3 }, "3 rows are exact copies of another row."],
  ["missing_values", { column: "city", metric: "missing_count", value: 12 }, "12 (0.01%)"],
  ["not_fitting", { column: "city", metric: "not_fitting_count", value: 7 }, "7"],
])("a %s finding's evidence link goes to its metric", async (kind, cited, shownAs) => {
  // CFX-APP-csv-profile/AC-23
  runs = [run({ mode: "full" })];
  extraMetrics = [["duplicate_rows", 3]];
  findings = [finding(kind, [cited])];
  renderApp();
  const shown = await within(await findingsPanel()).findByRole("article");
  await within(await profilePanel()).findByRole("table");

  await userEvent.click(within(shown).getByRole("button", { name: /evidence/i }));

  expect(document.activeElement?.textContent).toBe(shownAs);
});

test("a possibly-sensitive finding's evidence link goes to the patterns it cites", async () => {
  // CFX-APP-csv-profile/AC-27, as AC-23 asks of every finding
  runs = [run()];
  findings = [finding("possibly_sensitive", [{ column: "city", metric: "masked_patterns", value: PATTERNS }])];
  renderApp();
  const shown = await within(await findingsPanel()).findByRole("article");
  const row = await rowFor("city");

  await userEvent.click(within(shown).getByRole("button", { name: /evidence/i }));

  expect(document.activeElement).toBe(within(row).getByText("2 patterns").closest("td"));
});

const NO_FLAG_NOTE =
  "Having no possibly-sensitive flag doesn't mean a column has no personal data. Only column names, and values shaped like email addresses, phone numbers or payment card numbers, are checked.";

test.each([
  ["with a possibly-sensitive finding", [finding("possibly_sensitive", [{ column: "city", metric: "missing_count", value: 12 }])]],
  ["with findings of other kinds", [finding("single_value", [{ column: "city", metric: "distinct_count", value: 1 }])]],
  ["with no findings", []],
])("the findings say having no flag doesn't mean no personal data, %s", async (_, found) => {
  // CFX-APP-csv-profile/AC-28
  runs = [run()];
  findings = found;
  renderApp();

  expect(await within(await findingsPanel()).findByText(NO_FLAG_NOTE)).toBeInTheDocument();
});

test("before any profile, the findings say what to do", async () => {
  // CFX-APP-csv-profile/AC-21
  renderApp();

  expect(await within(await findingsPanel()).findByText("Profile this file to see what it finds.")).toBeInTheDocument();
});

test("a profile that found nothing says so", async () => {
  // CFX-APP-csv-profile/AC-21: no condition met, so no finding of any kind.
  runs = [run({ mode: "full" })];
  renderApp();

  expect(await within(await findingsPanel()).findByText("This profile found nothing to point out.")).toBeInTheDocument();
});

test("findings that can't be read say so", async () => {
  // CFX-APP-csv-profile/AC-23
  runs = [run()];
  const answer = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
    url.startsWith("/api/findings?") ? new Response(null, { status: 500 }) : answer?.(url, init),
  );
  renderApp();

  expect(await within(await findingsPanel()).findByRole("alert")).toHaveTextContent("couldn't load the findings");
});

test("after a cancelled run, the earlier run's findings stay", async () => {
  // CFX-APP-csv-profile/AC-26
  runs = [run({ id: "run-2", status: "cancelled" }), run()];
  findings = [finding("single_value", [{ column: "city", metric: "distinct_count", value: 1 }])];
  renderApp();

  expect(await within(await findingsPanel()).findByRole("article")).toHaveTextContent("every value in city");
});

test("with no file connected, the findings don't ask for a profile", async () => {
  // Found reviewing T08: the centre already says to connect a file.
  const answer = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
    url === "/api/sources" ? Response.json([]) : answer?.(url, init),
  );
  renderApp();

  const panel = await findingsPanel();
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === "/api/profile-runs")).toBe(true));

  expect(panel).not.toHaveTextContent("Profile this file");
});

test("if the runs can't be read, the findings say so instead of asking for a profile", async () => {
  // Found reviewing T08: there may well be runs.
  const answer = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
    url === "/api/profile-runs" ? new Response(null, { status: 500 }) : answer?.(url, init),
  );
  renderApp();

  const panel = await findingsPanel();

  expect(await within(panel).findByRole("alert")).toHaveTextContent("couldn't load the findings");
  expect(panel).not.toHaveTextContent("Profile this file");
});

const cell = (value: string, cut = false) => ({ value, cut });

const cityValues = {
  profile_run_id: "run-1",
  column: "city",
  scope: "full",
  rows_read: 3000,
  computed_at: "2024-03-05T11:00:00+00:00",
  minimum: cell("Aarhus"),
  maximum: cell("Zürich"),
  frequent: [
    { value: cell("Paris"), count: 900 },
    { value: cell("L".repeat(1000), true), count: 2 },
  ],
  histogram: null,
  mean: null,
  median: null,
  standard_deviation: null,
};

function valuesRequests(): string[] {
  return fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.includes("/values?"));
}

async function showValuesOf(column: string) {
  const row = await rowFor(column);
  await userEvent.click(within(row).getByRole("button", { name: `Show values of ${column}` }));
}

async function valuesSection() {
  return within(await profilePanel()).findByRole("region", { name: /^Values in / });
}

test("a column's values show on request, labelled with the part of the file and when", async () => {
  // CFX-APP-csv-profile/AC-30
  runs = [run()];
  renderApp();

  await showValuesOf("city");

  const shown = await valuesSection();
  expect(valuesRequests()).toEqual(["/api/profile-runs/run-1/values?column=city"]);
  expect(within(shown).getByRole("heading", { name: "Values in city" })).toBeInTheDocument();
  expect(shown).toHaveTextContent(
    `Computed ${new Date("2024-03-05T11:00:00+00:00").toLocaleString()} from the whole file, and not saved.`,
  );
  expect(within(shown).getByText("Minimum").nextElementSibling).toHaveTextContent("Aarhus");
  expect(within(shown).getByText("Maximum").nextElementSibling).toHaveTextContent("Zürich");
  const frequent = within(shown).getByRole("table", { name: "Most frequent values" });
  expect(within(frequent).getAllByRole("row").map((row) => row.textContent)).toEqual([
    "ValueCount",
    "Paris900",
    `${"L".repeat(1000)} … cut at 1,000 characters2`,
  ]);
  expect(within(shown).queryByRole("table", { name: "Histogram" })).not.toBeInTheDocument();
});

test("a number column's values show its histogram and statistics, from the first rows", async () => {
  // CFX-APP-csv-profile/AC-30
  runs = [run()];
  onValues = () =>
    Response.json({
      ...cityValues,
      scope: "sample",
      minimum: cell("1"),
      maximum: cell("9"),
      histogram: [
        { low: cell("1"), high: cell("5"), count: 3 },
        { low: cell("5"), high: cell("9"), count: 1 },
      ],
      mean: cell("3.5"),
      median: cell("3.0"),
      standard_deviation: cell("2.38"),
    });
  renderApp();

  await showValuesOf("city");

  const shown = await valuesSection();
  expect(shown).toHaveTextContent("from the first 100,000 rows, not random, and not saved.");
  const histogram = within(shown).getByRole("table", { name: "Histogram" });
  expect(within(histogram).getAllByRole("row").map((row) => row.textContent)).toEqual([
    "FromToCount",
    "153",
    "591",
  ]);
  expect(within(shown).getByText("Mean").nextElementSibling).toHaveTextContent("3.5");
  expect(within(shown).getByText("Median").nextElementSibling).toHaveTextContent("3.0");
  expect(within(shown).getByText("Standard deviation").nextElementSibling).toHaveTextContent("2.38");
});

test("a value cut at 1,000 characters is marked as cut", async () => {
  // CFX-APP-csv-profile/AC-31, as in the preview
  runs = [run()];
  onValues = () => Response.json({ ...cityValues, maximum: cell("Z".repeat(1000), true) });
  renderApp();

  await showValuesOf("city");

  const shown = await valuesSection();
  expect(within(shown).getByText("Maximum").nextElementSibling?.textContent).toBe(
    `${"Z".repeat(1000)} … cut at 1,000 characters`,
  );
});

const flaggedCity = () => [
  finding("possibly_sensitive", [{ column: "city", metric: "missing_count", value: 12 }]),
];

test("a possibly-sensitive column's values need the extra confirmation", async () => {
  // CFX-APP-csv-profile/AC-29
  runs = [run()];
  findings = flaggedCity();
  renderApp();
  await within(await findingsPanel()).findByRole("article");

  await showValuesOf("city");

  const asking = await within(await profilePanel()).findByRole("alertdialog", { name: /personal data/ });
  expect(asking).toHaveTextContent("This column may hold personal data. Show its values?");
  expect(valuesRequests()).toEqual([]);
  await userEvent.click(within(asking).getByRole("button", { name: "Show values" }));
  await valuesSection();
  expect(valuesRequests()).toEqual(["/api/profile-runs/run-1/values?column=city&confirmed=true"]);
});

test("declining the confirmation shows no values and asks for none", async () => {
  // CFX-APP-csv-profile/AC-29
  runs = [run()];
  findings = flaggedCity();
  renderApp();
  await within(await findingsPanel()).findByRole("article");

  await showValuesOf("city");
  await userEvent.click(await within(await profilePanel()).findByRole("button", { name: "Don't show" }));

  expect(within(await profilePanel()).queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(within(await profilePanel()).queryByRole("region", { name: /^Values in / })).not.toBeInTheDocument();
  expect(valuesRequests()).toEqual([]);
});

test("values that can't be worked out say why, and the saved metrics still show", async () => {
  // CFX-APP-csv-profile/AC-32
  runs = [run()];
  onValues = () =>
    Response.json(
      { detail: "DrivnX can't find this file. It may have moved or been deleted." },
      { status: 409 },
    );
  renderApp();

  await showValuesOf("city");

  const shown = await valuesSection();
  expect(await within(shown).findByRole("alert")).toHaveTextContent(
    "DrivnX can't find this file. It may have moved or been deleted.",
  );
  expect(within(await rowFor("city")).getByText("12 (0.01%)")).toBeInTheDocument();
});

test("closing the values forgets them, so showing them again works them out again", async () => {
  // CFX-APP-csv-profile/AC-30: computed on request each time (ADR 0004).
  runs = [run()];
  renderApp();
  await showValuesOf("city");
  const shown = await valuesSection();
  await within(shown).findByText("Aarhus");

  await userEvent.click(within(shown).getByRole("button", { name: "Close values" }));
  expect(within(await profilePanel()).queryByText("Aarhus")).not.toBeInTheDocument();
  await showValuesOf("city");
  await within(await valuesSection()).findByText("Aarhus");

  expect(valuesRequests()).toHaveLength(2);
});

test("the values section takes the focus when it opens", async () => {
  // CFX-APP-csv-profile/AC-30, by keyboard alone
  runs = [run()];
  renderApp();

  await showValuesOf("city");

  const shown = await valuesSection();
  await waitFor(() => expect(document.activeElement).toBe(within(shown).getByRole("heading", { name: "Values in city" })));
});

test("closing the values gives the focus back to the column's button", async () => {
  // CFX-APP-csv-profile/AC-30, by keyboard alone
  runs = [run()];
  renderApp();
  await showValuesOf("city");
  const shown = await valuesSection();

  await userEvent.click(within(shown).getByRole("button", { name: "Close values" }));

  expect(document.activeElement).toBe(within(await rowFor("city")).getByRole("button", { name: "Show values of city" }));
});

test("declining the confirmation gives the focus back to the column's button", async () => {
  // CFX-APP-csv-profile/AC-29, by keyboard alone
  runs = [run()];
  findings = flaggedCity();
  renderApp();
  await within(await findingsPanel()).findByRole("article");
  await showValuesOf("city");

  await userEvent.click(await within(await profilePanel()).findByRole("button", { name: "Don't show" }));

  expect(document.activeElement).toBe(within(await rowFor("city")).getByRole("button", { name: "Show values of city" }));
});
