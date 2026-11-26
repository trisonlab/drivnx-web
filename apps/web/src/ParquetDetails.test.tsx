import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
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

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/workspace") {
      return Response.json(workspace);
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
