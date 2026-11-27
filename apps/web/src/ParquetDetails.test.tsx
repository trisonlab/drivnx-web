import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";

const workspace = {
  location: "/home/me/DrivnX/default",
  name: "Sales",
  ai_mode: "off",
  setup_complete: true,
};

// DRIVN-60's `events.parquet`, as the API sends it (DRIVN-61).
const events = {
  id: "p1",
  kind: "parquet",
  name: "events.parquet",
  path: "/data/events.parquet",
  size_bytes: 1_997_159_793,
  modified_at: "2026-11-20T10:00:00+00:00",
  available: true,
  row_count: 40_000_000,
  row_group_count: 326,
  created_by: "parquet-cpp-arrow version 17.0.0",
  columns: [
    { name: "id", physical_type: "UBIGINT", normalised_type: "integer" },
    { name: "tags", physical_type: "INTEGER[]", normalised_type: "string" },
    { name: "device", physical_type: "STRUCT(a INTEGER)", normalised_type: "string" },
    { name: "attributes", physical_type: "MAP(VARCHAR, INTEGER)", normalised_type: "string" },
    { name: "payload", physical_type: "BLOB", normalised_type: "string" },
  ],
};

const fetchMock = vi.fn();
// A completed run of `events`, and the metrics it shows (DRIVN-63).
let runMode: "fast" | "full";
let runMetrics: unknown[];

function metric(column: string | null, name: string, value: unknown, scope: string) {
  return { column, metric: name, value, scope, precision: "exact" };
}

beforeEach(() => {
  runMode = "fast";
  runMetrics = [];
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/workspace") {
      return Response.json(workspace);
    }
    const ran = {
      id: "r1",
      source_id: "p1",
      mode: runMode,
      status: "completed",
      started_at: "2026-11-26T10:00:00+00:00",
      ended_at: "2026-11-26T10:00:01+00:00",
      current_settings: true,
    };
    if (url === "/api/profile-runs") {
      return Response.json(runMetrics.length ? [ran] : []);
    }
    if (url === "/api/profile-runs/r1") {
      return Response.json({ ...ran, metrics: runMetrics, file_changed: false });
    }
    if (url.startsWith("/api/findings?")) {
      return Response.json([]);
    }
    if (url === "/api/sources") {
      return Response.json([events]);
    }
    if (url === "/api/sources/p1/rows?offset=0") {
      return Response.json({
        columns: [{ value: "id", cut: false }, { value: "tags", cut: false }],
        rows: [[{ value: "18446744073709551615", cut: false }, { value: "[1, NULL, 3]", cut: false }]],
        offset: 0,
        has_next: true,
        skipped_lines: 0,
      });
    }
    return Response.json([]);
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

test("a Parquet file shows what its metadata says", async () => {
  // DRIVN-60/AC-07
  renderApp();

  const details = await screen.findByRole("region", { name: "events.parquet" });
  expect(within(details).getByText("Metadata")).toBeInTheDocument();
  expect(within(details).getByText("/data/events.parquet")).toBeInTheDocument();
  expect(within(details).getByText("1.9 GB")).toBeInTheDocument();
  expect(within(details).getByText("40,000,000 rows")).toBeInTheDocument();
  expect(within(details).getByText("326 row groups")).toBeInTheDocument();
  expect(within(details).getByText("Written by parquet-cpp-arrow version 17.0.0")).toBeInTheDocument();
  // A CSV's read settings mean nothing for a Parquet file.
  expect(details).not.toHaveTextContent(/delimiter|encoding/i);
});

test("each column shows its native type, its type, and whether it's a list, struct or map", async () => {
  // DRIVN-60/AC-09
  renderApp();

  const details = await screen.findByRole("region", { name: "events.parquet" });
  const rows = within(within(details).getByRole("table")).getAllByRole("row");
  expect(rows.slice(1).map((row) => row.textContent)).toEqual([
    "idintegerUBIGINT",
    "tagsstring (list)INTEGER[]",
    "devicestring (struct)STRUCT(a INTEGER)",
    "attributesstring (map)MAP(VARCHAR, INTEGER)",
    "payloadstringBLOB",
  ]);
});

test("a Parquet file's preview opens on its first page", async () => {
  // DRIVN-60/AC-10
  renderApp();

  const preview = await screen.findByRole("region", { name: "Preview" });
  const rows = await within(preview).findAllByRole("row");
  expect(rows.map((row) => row.textContent)).toEqual(["idtags", "18446744073709551615[1, NULL, 3]"]);
  expect(within(preview).getByRole("button", { name: "Next" })).toBeEnabled();
  // A Parquet file has no read settings to show, nor lines skipped above a table.
  expect(preview).not.toHaveTextContent(/skipped/i);
});

async function results() {
  renderApp();
  return screen.findByRole("tabpanel", { name: "Profile" });
}

test("a fast profile says it sampled by row group, and the row count is from the metadata", async () => {
  // DRIVN-60/AC-12
  runMetrics = [
    metric(null, "rows_read", 100_000, "sample"),
    metric(null, "row_count", 40_000_000, "metadata"),
    metric("id", "distinct_count", 100_000, "sample"),
  ];

  const panel = await results();

  expect(await within(panel).findByText("This file has 40,000,000 rows, from the file's metadata.")).toBeInTheDocument();
  const row = within(panel).getByRole("row", { name: /^id/ });
  expect(within(row).getByText("about 100,000 rows, sampled by row group")).toBeInTheDocument();
  expect(panel).not.toHaveTextContent(/not random|whole file/);
});

test("a file no larger than the sample says every row was read", async () => {
  // DRIVN-60/AC-30
  runMetrics = [
    metric(null, "rows_read", 60_000, "full"),
    metric(null, "row_count", 60_000, "metadata"),
    metric("id", "distinct_count", 60_000, "full"),
  ];

  const panel = await results();

  const row = await within(panel).findByRole("row", { name: /^id/ });
  expect(within(row).getByText("every row, 60,000 rows")).toBeInTheDocument();
});

test("a full profile's masked patterns say they're from the sample", async () => {
  // DRIVN-60/AC-12, with the patterns of a database's full profile (decided 2026-11-26)
  runMode = "full";
  runMetrics = [
    metric(null, "rows_read", 40_000_000, "full"),
    metric(null, "row_count", 40_000_000, "metadata"),
    metric(null, "sample_rows_read", 100_000, "sample"),
    metric("name", "distinct_count", 40_000_000, "full"),
    metric("name", "masked_patterns", [{ pattern: "aaaaa 9", count: 100_000, cut: false }], "sample"),
  ];

  const panel = await results();

  const row = await within(panel).findByRole("row", { name: /^name/ });
  expect(within(row).getByText("every row, 40,000,000 rows")).toBeInTheDocument();
  expect(within(row).getByText("1 pattern, from about 100,000 rows, sampled by row group")).toBeInTheDocument();
});

test("a sampled run's values say they're from its rows, sampled by row group", async () => {
  // DRIVN-60/AC-14: "values on request are computed now from … the same seeded row groups".
  runMetrics = [
    metric(null, "rows_read", 100_000, "sample"),
    metric(null, "row_count", 40_000_000, "metadata"),
    metric("id", "distinct_count", 100_000, "sample"),
  ];
  const answer = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
    url === "/api/profile-runs/r1/values?column=id"
      ? Response.json({
          profile_run_id: "r1",
          column: "id",
          scope: "sample",
          rows_read: 100_000,
          computed_at: "2026-11-27T11:00:00+00:00",
          minimum: { value: "3", cut: false },
          maximum: { value: "39999998", cut: false },
          frequent: [{ value: { value: "3", cut: false }, count: 1 }],
          histogram: null,
          mean: null,
          median: null,
          standard_deviation: null,
        })
      : answer?.(url, init),
  );
  const panel = await results();

  const row = await within(panel).findByRole("row", { name: /^id/ });
  await userEvent.click(within(row).getByRole("button", { name: "Show values of id" }));

  const shown = await within(panel).findByRole("region", { name: "Values in id" });
  expect(shown).toHaveTextContent(
    `Computed ${new Date("2026-11-27T11:00:00+00:00").toLocaleString()} from about 100,000 rows, sampled by row group, and not saved.`,
  );
  expect(shown).not.toHaveTextContent(/not random/);
});

test("a run of a file that has changed since says its results may be out of date", async () => {
  // DRIVN-60/AC-14
  runMetrics = [metric(null, "rows_read", 100_000, "sample"), metric("id", "distinct_count", 100_000, "sample")];
  const answer = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const response = await answer?.(url, init);
    return url === "/api/profile-runs/r1" ? Response.json({ ...(await response.json()), file_changed: true }) : response;
  });

  const panel = await results();

  expect(
    await within(panel).findByText(
      "This file has changed since this profile ran, so these results may be out of date.",
    ),
  ).toBeInTheDocument();
});

test("a sampled run's findings say they're from its rows, sampled by row group", async () => {
  // DRIVN-60/AC-14: the CSV profile's findings, observed in the rows the run read (DRIVN-9/AC-24).
  runMetrics = [metric(null, "rows_read", 100_000, "sample"), metric("note", "missing_count", 25_000, "sample")];
  const answer = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
    url.startsWith("/api/findings?")
      ? Response.json([
          {
            id: "f1",
            profile_run_id: "r1",
            kind: "missing_values",
            interpretation: "Significant missing values",
            why_it_matters: "Saved reason",
            recommended_action: "Saved action",
            state: "inferred",
            evidence: [
              { column: "note", metric: "missing_count", value: 25_000, scope: "sample", precision: "exact" },
              { column: "note", metric: "missing_percent", value: 25, scope: "sample", precision: "exact" },
            ],
          },
        ])
      : answer?.(url, init),
  );
  renderApp();

  const aside = await screen.findByRole("complementary", { name: "Intelligence & Review" });
  const shown = await within(within(aside).getByRole("region", { name: "Findings" })).findByRole("article");
  expect(shown).toHaveTextContent(
    "Observed: note is missing 25,000 values (25%) in about 100,000 rows, sampled by row group.",
  );
  expect(shown).not.toHaveTextContent(/first 100,000/);
});

test("a full run's values say they're from every row, as its columns do", async () => {
  // DRIVN-60/AC-14, worded as the run's own label (found in DRIVN-64's review).
  runMode = "full";
  runMetrics = [metric(null, "rows_read", 40_000_000, "full"), metric("id", "distinct_count", 40_000_000, "full")];
  const answer = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
    url === "/api/profile-runs/r1/values?column=id"
      ? Response.json({
          profile_run_id: "r1",
          column: "id",
          scope: "full",
          rows_read: 40_000_000,
          computed_at: "2026-11-27T11:00:00+00:00",
          minimum: { value: "0", cut: false },
          maximum: { value: "39999999", cut: false },
          frequent: [{ value: { value: "0", cut: false }, count: 1 }],
          histogram: null,
          mean: null,
          median: null,
          standard_deviation: null,
        })
      : answer?.(url, init),
  );
  const panel = await results();

  const row = await within(panel).findByRole("row", { name: /^id/ });
  await userEvent.click(within(row).getByRole("button", { name: "Show values of id" }));

  const shown = await within(panel).findByRole("region", { name: "Values in id" });
  expect(shown).toHaveTextContent(
    `Computed ${new Date("2026-11-27T11:00:00+00:00").toLocaleString()} from every row, 40,000,000 rows, and not saved.`,
  );
});
