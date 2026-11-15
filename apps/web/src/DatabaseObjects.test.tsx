import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { type PostgresSource, UNTIL_STOP } from "./api";
import { PostgresDetails } from "./PostgresDetails";

const source: PostgresSource = {
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
  server_version: "18.1",
  encrypted: true,
};

function object(id: string, name: string, extra: object = {}) {
  return {
    id,
    name,
    kind: "table",
    estimated_rows: null,
    readable: true,
    populated: true,
    chosen: false,
    ...extra,
  };
}

const listed = {
  discovered_at: "2026-11-14T10:30:00+00:00",
  schemas: [
    {
      name: "sales",
      objects: [
        object("o1", "orders", { estimated_rows: 1200 }),
        object("o2", "events", { kind: "partitioned_table", estimated_rows: 7000 }),
        object("o3", "order_view", { kind: "view" }),
        object("o4", "order_totals", { kind: "materialized_view", estimated_rows: 2 }),
      ],
    },
    { name: "empty", objects: [] },
  ],
};

const fetchMock = vi.fn();
// What the server answers, by method and path.
let answers: Record<string, () => Response> = {};

beforeEach(() => {
  answers = { "GET /api/sources/p1/objects": () => Response.json(listed) };
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const answer = answers[`${init?.method ?? "GET"} ${url}`];
    if (answer === undefined) {
      throw new Error(`Nothing answers ${init?.method ?? "GET"} ${url}`);
    }
    return answer();
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderDetails(shown: PostgresSource = source) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PostgresDetails source={shown} />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

test("each schema is listed with its objects, their kinds and estimated rows labelled as estimates", async () => {
  // CFX-APP-postgres/AC-08
  renderDetails();

  const sales = await screen.findByRole("group", { name: "sales" });
  expect(within(sales).getByRole("listitem", { name: "orders" })).toHaveTextContent(
    "orders · table · about 1,200 rows (estimate)",
  );
  expect(within(sales).getByRole("listitem", { name: "events" })).toHaveTextContent(
    "events · partitioned table · about 7,000 rows (estimate)",
  );
  expect(within(sales).getByRole("listitem", { name: "order_view" })).toHaveTextContent(
    "order_view · view · no row estimate",
  );
  expect(within(sales).getByRole("listitem", { name: "order_totals" })).toHaveTextContent(
    "order_totals · materialized view · about 2 rows (estimate)",
  );
  expect(screen.getByRole("group", { name: "empty" })).toHaveTextContent("No tables or views");
});

test("a database not listed yet says so, and listing it shows its schemas", async () => {
  // CFX-APP-postgres/AC-08: listing when the connection was saved failed, so the user lists it.
  answers["GET /api/sources/p1/objects"] = () => Response.json({ discovered_at: null, schemas: [] });
  answers["POST /api/sources/p1/discover"] = () => Response.json(listed);
  const user = renderDetails();

  const section = await screen.findByRole("region", { name: "Schemas and tables" });
  expect(await within(section).findByText("DrivnX hasn't listed this database's schemas and tables yet.")).toBeInTheDocument();
  expect(within(section).queryByRole("button", { name: "Save selection" })).not.toBeInTheDocument();

  await user.click(within(section).getByRole("button", { name: "List schemas and tables" }));

  expect(await within(section).findByRole("group", { name: "sales" })).toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
});

test("a list the workspace can't load says so", async () => {
  // CFX-APP-postgres/AC-08: never an empty section with nothing to do.
  answers["GET /api/sources/p1/objects"] = () => Response.json({ detail: "Internal Server Error" }, { status: 500 });
  renderDetails();
  const section = await screen.findByRole("region", { name: "Schemas and tables" });

  expect(await within(section).findByRole("alert")).toHaveTextContent(
    "DrivnX couldn't load this database's schemas and tables. Start DrivnX again.",
  );
});

test("refreshing shows the database as it is now, and when it was listed", async () => {
  // CFX-APP-postgres/AC-13: the order view was dropped since the last listing.
  const now = {
    discovered_at: "2026-11-14T11:00:00+00:00",
    schemas: [{ name: "sales", objects: listed.schemas[0].objects.filter(({ name }) => name !== "order_view") }],
  };
  answers["POST /api/sources/p1/discover"] = () => Response.json(now);
  const user = renderDetails();
  const section = await screen.findByRole("region", { name: "Schemas and tables" });
  expect(await within(section).findByText(`Listed ${new Date(listed.discovered_at).toLocaleString()}`)).toBeInTheDocument();

  await user.click(within(section).getByRole("button", { name: "Refresh" }));

  expect(await within(section).findByText(`Listed ${new Date(now.discovered_at).toLocaleString()}`)).toBeInTheDocument();
  expect(within(section).queryByRole("listitem", { name: "order_view" })).not.toBeInTheDocument();
  expect(within(section).getByRole("listitem", { name: "orders" })).toBeInTheDocument();
});

test("a refresh that fails says why and keeps the list", async () => {
  // CFX-APP-postgres/AC-13
  answers["POST /api/sources/p1/discover"] = () =>
    Response.json({ detail: "Nothing answered at this host and port." }, { status: 422 });
  const user = renderDetails();
  const section = await screen.findByRole("region", { name: "Schemas and tables" });
  await within(section).findByRole("group", { name: "sales" });

  await user.click(within(section).getByRole("button", { name: "Refresh" }));

  expect(await within(section).findByRole("alert")).toHaveTextContent("Nothing answered at this host and port.");
  expect(within(section).getByRole("listitem", { name: "orders" })).toBeInTheDocument();
});

const withAccess = {
  discovered_at: "2026-11-14T10:30:00+00:00",
  schemas: [
    {
      name: "sales",
      objects: [
        object("o1", "orders", { chosen: true }),
        object("o5", "secret", { readable: false }),
        object("o6", "regions", { kind: "materialized_view", populated: false }),
        object("o7", "customers"),
      ],
    },
    { name: "audit", objects: [object("o8", "log"), object("o9", "hidden", { readable: false })] },
  ],
};

test("an object without read access, or never populated, is listed and can't be chosen", async () => {
  // CFX-APP-postgres/AC-10
  answers["GET /api/sources/p1/objects"] = () => Response.json(withAccess);
  renderDetails();

  const secret = await screen.findByRole("listitem", { name: "secret" });
  expect(secret).toHaveTextContent("No read access");
  expect(within(secret).getByRole("checkbox", { name: "Choose secret" })).toBeDisabled();
  const regions = screen.getByRole("listitem", { name: "regions" });
  expect(regions).toHaveTextContent("Not populated: refresh the materialized view in PostgreSQL first");
  expect(within(regions).getByRole("checkbox", { name: "Choose regions" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "Choose orders" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Choose customers" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Choose customers" })).toBeEnabled();
});

function put(path: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([url, init]) => url === path && init?.method === "PUT")
    .map(([, init]) => JSON.parse(init.body));
}

test("choosing a whole schema or single objects and saving sends exactly the chosen objects", async () => {
  // CFX-APP-postgres/AC-11: a whole schema is its objects that can be chosen, as they are now.
  answers["GET /api/sources/p1/objects"] = () => Response.json(withAccess);
  const saved = {
    ...withAccess,
    schemas: withAccess.schemas.map((schema) => ({
      ...schema,
      objects: schema.objects.map((found) => ({ ...found, chosen: ["o7", "o8"].includes(found.id) })),
    })),
  };
  answers["PUT /api/sources/p1/selection"] = () => Response.json(saved);
  const user = renderDetails();
  const sales = await screen.findByRole("checkbox", { name: "Choose all of sales" });
  expect(sales).not.toBeChecked();

  await user.click(sales);

  expect(sales).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Choose customers" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Choose secret" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Choose regions" })).not.toBeChecked();

  await user.click(screen.getByRole("checkbox", { name: "Choose orders" }));
  await user.click(screen.getByRole("checkbox", { name: "Choose log" }));

  expect(sales).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Choose all of audit" })).toBeChecked();

  await user.click(screen.getByRole("button", { name: "Save selection" }));

  expect(await screen.findByRole("status")).toHaveTextContent("Selection saved.");
  expect(put("/api/sources/p1/selection")).toEqual([{ object_ids: ["o7", "o8"] }]);
});

test("unticking a whole schema leaves none of it chosen", async () => {
  // CFX-APP-postgres/AC-11
  answers["GET /api/sources/p1/objects"] = () =>
    Response.json({ ...withAccess, schemas: [{ name: "audit", objects: [object("o8", "log", { chosen: true })] }] });
  const user = renderDetails();
  const audit = await screen.findByRole("checkbox", { name: "Choose all of audit" });
  expect(audit).toBeChecked();

  await user.click(audit);

  expect(screen.getByRole("checkbox", { name: "Choose log" })).not.toBeChecked();
});

test("a selection the server refuses says why", async () => {
  // CFX-APP-postgres/AC-10: chosen in another window, then its access was taken away.
  answers["GET /api/sources/p1/objects"] = () => Response.json(withAccess);
  answers["PUT /api/sources/p1/selection"] = () =>
    Response.json({ detail: "Some of these objects can't be chosen. Refresh and choose again." }, { status: 422 });
  const user = renderDetails();

  await user.click(await screen.findByRole("button", { name: "Save selection" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Some of these objects can't be chosen. Refresh and choose again.",
  );
});

const orders = {
  ...object("o1", "orders", { estimated_rows: 1200 }),
  schema: "sales",
  columns: [
    { name: "id", physical_type: "bigint", normalised_type: "integer", allows_nulls: false },
    { name: "region", physical_type: "text", normalised_type: "string", allows_nulls: true },
  ],
  keys: [
    { kind: "primary_key", name: "orders_key", columns: ["id"], references: null },
    { kind: "unique", name: "orders, odd", columns: ["region", "id"], references: null },
    {
      kind: "foreign_key",
      name: "orders_region",
      columns: ["region"],
      references: { schema: "ref", object: "regions", columns: ["name"] },
    },
  ],
};

test("opening an object shows its columns and declared keys, and reads no row", async () => {
  // CFX-APP-postgres/AC-09
  answers["GET /api/sources/p1/objects/o1"] = () => Response.json(orders);
  const user = renderDetails();

  await user.click(await screen.findByRole("button", { name: "orders" }));

  const opened = await screen.findByRole("region", { name: "sales.orders" });
  const rows = within(opened)
    .getAllByRole("row")
    .map((row) => [...row.querySelectorAll("th, td")].map((cell) => cell.textContent));
  expect(rows).toEqual([
    ["Column", "PostgreSQL type", "Type", "Allows nulls"],
    ["id", "bigint", "integer", "No"],
    ["region", "text", "string", "Yes"],
  ]);
  expect(within(opened).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
    "Primary key orders_key: id",
    "Unique constraint orders, odd: region, id",
    "Foreign key orders_region: region → ref.regions (name)",
  ]);
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/sources/p1/objects", "/api/sources/p1/objects/o1"]);
});

test("a view's columns say PostgreSQL doesn't record whether they allow nulls", async () => {
  // CFX-APP-postgres/AC-09: the catalog says every view column allows nulls, which isn't known.
  answers["GET /api/sources/p1/objects/o3"] = () =>
    Response.json({ ...orders, ...object("o3", "order_view", { kind: "view" }), keys: [] });
  const user = renderDetails();

  await user.click(await screen.findByRole("button", { name: "order_view" }));

  const opened = await screen.findByRole("region", { name: "sales.order_view" });
  expect(within(opened).getAllByRole("cell", { name: "Not recorded" })).toHaveLength(2);
  expect(opened).toHaveTextContent("PostgreSQL doesn't record whether a view's columns allow nulls.");
  expect(opened).toHaveTextContent("No declared keys.");
});

test("a materialized view's columns say PostgreSQL doesn't record whether they allow nulls", async () => {
  // CFX-APP-postgres/AC-09: as for a view, NOT NULL is never recorded for a materialized view's columns.
  answers["GET /api/sources/p1/objects/o4"] = () =>
    Response.json({ ...orders, ...object("o4", "order_totals", { kind: "materialized_view" }), keys: [] });
  const user = renderDetails();

  await user.click(await screen.findByRole("button", { name: "order_totals" }));

  const opened = await screen.findByRole("region", { name: "sales.order_totals" });
  expect(within(opened).getAllByRole("cell", { name: "Not recorded" })).toHaveLength(2);
  expect(opened).toHaveTextContent("PostgreSQL doesn't record whether a materialized view's columns allow nulls.");
});

test("an object no longer listed says to refresh", async () => {
  // CFX-APP-postgres/AC-13: refreshed in another window since this list was shown.
  answers["GET /api/sources/p1/objects/o1"] = () =>
    Response.json({ detail: "There's no such object. Refresh the list." }, { status: 404 });
  const user = renderDetails();

  await user.click(await screen.findByRole("button", { name: "orders" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("This object is no longer listed. Refresh the list.");
});

test("a refresh closes the opened object and starts again from the saved selection", async () => {
  // CFX-APP-postgres/AC-13: the refresh may have replaced the object, and unsaved ticks may name
  // objects that are gone.
  answers["GET /api/sources/p1/objects"] = () => Response.json(withAccess);
  answers["GET /api/sources/p1/objects/o1"] = () => Response.json(orders);
  answers["POST /api/sources/p1/discover"] = () =>
    Response.json({ ...withAccess, discovered_at: "2026-11-14T11:00:00+00:00" });
  const user = renderDetails();
  await user.click(await screen.findByRole("button", { name: "orders" }));
  await screen.findByRole("region", { name: "sales.orders" });
  await user.click(screen.getByRole("checkbox", { name: "Choose customers" }));

  await user.click(screen.getByRole("button", { name: "Refresh" }));

  expect(await screen.findByText(`Listed ${new Date("2026-11-14T11:00:00+00:00").toLocaleString()}`)).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "sales.orders" })).not.toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Choose customers" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Choose orders" })).toBeChecked();
});

test("changing the selection after saving it no longer says it's saved", async () => {
  // CFX-APP-postgres/AC-11
  answers["GET /api/sources/p1/objects"] = () => Response.json(withAccess);
  answers["PUT /api/sources/p1/selection"] = () => Response.json(withAccess);
  const user = renderDetails();
  await user.click(await screen.findByRole("button", { name: "Save selection" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Selection saved.");

  await user.click(screen.getByRole("checkbox", { name: "Choose customers" }));

  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});
