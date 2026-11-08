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

function source(id: string, name: string) {
  return {
    id,
    name,
    path: `/data/${name}`,
    delimiter: ",",
    encoding: "UTF-8",
    has_header: true,
    size_bytes: 1536,
    modified_at: "2024-03-05T10:00:00+00:00",
    available: true,
    columns: [{ name: "id", physical_type: "BIGINT", normalised_type: "integer" }],
  };
}

function cell(value: string | null, cut = false) {
  return { value, cut };
}

function rowsFrom(first: number, count: number) {
  return Array.from({ length: count }, (_, index) => [
    cell(String(first + index)),
    cell(`name${first + index}`),
  ]);
}

/** 120 rows, 50 to a page. */
function peoplePage(offset: number) {
  return {
    columns: [cell("id"), cell("name")],
    rows: rowsFrom(offset + 1, Math.min(50, 120 - offset)),
    offset,
    has_next: offset + 50 < 120,
    skipped_lines: 0,
  };
}

function onePage(columns: string[], rows: unknown[][], offset = 0) {
  return { columns: columns.map((name) => cell(name)), rows, offset, has_next: false, skipped_lines: 0 };
}

const fetchMock = vi.fn();
let onRows: (id: string, offset: number) => Response | Promise<Response>;

beforeEach(() => {
  onRows = (_id, offset) => Response.json(peoplePage(offset));
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/workspace") {
      return Response.json(workspace);
    }
    const rows = url.match(/^\/api\/sources\/(\w+)\/rows\?offset=(\d+)$/);
    if (rows) {
      return onRows(rows[1], Number(rows[2]));
    }
    return Response.json([source("s1", "people.csv"), source("s2", "other.csv")]);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function openPreview() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <App signedIn />
    </QueryClientProvider>,
  );
  return screen.findByRole("region", { name: "Preview" });
}

function button(name: string) {
  return screen.getByRole("button", { name });
}

test("the preview opens on the first 50 rows, in the file's columns", async () => {
  // CFX-APP-first-slice/AC-27
  const preview = await openPreview();

  const rows = await within(preview).findAllByRole("row");
  expect(rows).toHaveLength(51);
  expect(rows[0]).toHaveTextContent("idname");
  expect(rows[1]).toHaveTextContent("1name1");
  expect(rows[50]).toHaveTextContent("50name50");
  expect(within(preview).getByText("Rows 1 to 50")).toBeInTheDocument();
});

test("next and previous show the adjacent pages, with no total", async () => {
  // CFX-APP-first-slice/AC-27
  const user = userEvent.setup();
  const preview = await openPreview();
  await within(preview).findByText("name1");
  expect(button("Previous")).toBeDisabled();

  await user.click(button("Next"));
  expect(await within(preview).findByText("name51")).toBeInTheDocument();
  expect(within(preview).queryByText("name1")).not.toBeInTheDocument();
  expect(within(preview).getByText("Rows 51 to 100")).toBeInTheDocument();
  expect(preview).not.toHaveTextContent(/\bof\b|total|page \d/i);

  await user.click(button("Previous"));
  expect(await within(preview).findByText("name1")).toBeInTheDocument();
  expect(button("Previous")).toBeDisabled();
});

test("next is disabled on the last page", async () => {
  // CFX-APP-first-slice/AC-27
  const user = userEvent.setup();
  const preview = await openPreview();
  await within(preview).findByText("name1");

  await user.click(button("Next"));
  await within(preview).findByText("name51");
  expect(button("Next")).toBeEnabled();
  await user.click(button("Next"));
  await within(preview).findByText("name120");

  expect(button("Next")).toBeDisabled();
  expect(button("Previous")).toBeEnabled();
  expect(within(preview).getByText("Rows 101 to 120")).toBeInTheDocument();
});

test("script text in a cell is shown as text and nothing runs", async () => {
  // CFX-APP-first-slice/AC-29
  const script = '<script>window.ran = true</script><img src=x onerror="window.ran = true">';
  onRows = () => Response.json(onePage([script], [[cell(script)]]));

  const preview = await openPreview();

  expect(await within(preview).findByRole("cell", { name: script })).toBeInTheDocument();
  expect(within(preview).getByRole("columnheader", { name: script })).toBeInTheDocument();
  expect(preview.querySelector("script, img")).toBeNull();
  expect((window as unknown as { ran?: boolean }).ran).toBeUndefined();
});

test("a cut cell is visibly marked and others are not", async () => {
  // CFX-APP-first-slice/AC-29
  onRows = () =>
    Response.json(
      onePage(
        ["id", "note"],
        [
          [cell("1"), cell("x".repeat(1000), true)],
          [cell("2"), cell(null)],
        ],
      ),
    );

  const preview = await openPreview();

  const rows = await within(preview).findAllByRole("row");
  expect(within(rows[1]).getByText(/cut at 1,000 characters/i)).toBeInTheDocument();
  expect(rows[2]).not.toHaveTextContent(/cut at/i);
  expect(within(preview).getAllByText(/cut at 1,000 characters/i)).toHaveLength(1);
});

test("a file that can't be read gives the reason, and the user can go back", async () => {
  // CFX-APP-first-slice/AC-31
  const user = userEvent.setup();
  const reason = "DrivnX can't find this file. It may have moved or been deleted.";
  const preview = await openPreview();
  await within(preview).findByText("name1");
  onRows = (_id, offset) =>
    offset === 0 ? Response.json(peoplePage(0)) : Response.json({ detail: reason }, { status: 409 });

  await user.click(button("Next"));

  expect(await within(preview).findByRole("alert")).toHaveTextContent(reason);
  expect(within(preview).queryByText("name1")).not.toBeInTheDocument();
  expect(button("Next")).toBeDisabled();

  await user.click(button("Previous"));
  expect(await within(preview).findByText("name1")).toBeInTheDocument();
  expect(within(preview).queryByRole("alert")).not.toBeInTheDocument();
});

test("any other failure says to start DrivnX again, without raw errors", async () => {
  // CFX-APP-first-slice/AC-31
  onRows = () => {
    throw new TypeError("Failed to fetch");
  };

  const preview = await openPreview();

  const alert = await within(preview).findByRole("alert");
  expect(alert).toHaveTextContent("Start DrivnX again");
  expect(alert).not.toHaveTextContent("Failed to fetch");
});

test("a page with no rows says so and keeps the way back", async () => {
  // CFX-APP-first-slice/AC-31: a file can shrink between pages.
  const user = userEvent.setup();
  const preview = await openPreview();
  await within(preview).findByText("name1");
  onRows = (_id, offset) =>
    Response.json(offset === 0 ? peoplePage(0) : onePage(["id", "name"], [], offset));

  await user.click(button("Next"));

  expect(await within(preview).findByText("No rows to show here.")).toBeInTheDocument();
  expect(button("Previous")).toBeEnabled();
  expect(button("Next")).toBeDisabled();
});

test("choosing another source opens its first page", async () => {
  // CFX-APP-first-slice/AC-27
  const user = userEvent.setup();
  const preview = await openPreview();
  await within(preview).findByText("name1");
  await user.click(button("Next"));
  await within(preview).findByText("name51");
  onRows = (id, offset) =>
    Response.json(onePage(["city"], [[cell(`${id} at ${offset}`)]], offset));

  await user.click(button("other.csv"));

  expect(await screen.findByText("s2 at 0")).toBeInTheDocument();
});

function rowRequests() {
  return fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.includes("/rows"));
}

test("while the next page loads, the label matches the rows still on screen", async () => {
  // CFX-APP-first-slice/AC-27: found verifying T06, deep pages can take seconds to arrive.
  const user = userEvent.setup();
  const preview = await openPreview();
  await within(preview).findByText("name1");
  let arrive = () => {};
  onRows = (_id, offset) =>
    new Promise((resolve) => {
      arrive = () => resolve(Response.json(peoplePage(offset)));
    });

  await user.click(button("Next"));

  expect(within(preview).getByText("name1")).toBeInTheDocument();
  expect(within(preview).getByText("Rows 1 to 50")).toBeInTheDocument();
  expect(within(preview).getByText(/Loading/)).toBeInTheDocument();
  expect(button("Next")).toBeEnabled();

  arrive();
  expect(await within(preview).findByText("name51")).toBeInTheDocument();
  expect(within(preview).getByText("Rows 51 to 100")).toBeInTheDocument();
  expect(within(preview).queryByText(/Loading/)).not.toBeInTheDocument();
});

test("pressing next again while a page loads doesn't move further on", async () => {
  // CFX-APP-first-slice/AC-27: found verifying T06, this could run past the last page.
  const user = userEvent.setup();
  const preview = await openPreview();
  await within(preview).findByText("name1");
  let arrive = () => {};
  onRows = (_id, offset) =>
    new Promise((resolve) => {
      arrive = () => resolve(Response.json(peoplePage(offset)));
    });

  await user.click(button("Next"));
  await user.click(button("Next"));
  await user.click(button("Next"));
  arrive();

  expect(await within(preview).findByText("name51")).toBeInTheDocument();
  expect(rowRequests().filter((url) => url.endsWith("offset=50"))).toHaveLength(1);
  expect(rowRequests().filter((url) => url.endsWith("offset=100"))).toHaveLength(0);
});

test.each([
  [500, "500 lines above this table aren't shown."],
  [1, "1 line above this table isn't shown."],
])("skipped lines are stated on every page (%i)", async (skipped_lines, note) => {
  // CFX-APP-first-slice/AC-27: they can be real rows, so they must not vanish silently.
  const user = userEvent.setup();
  onRows = (_id, offset) => Response.json({ ...peoplePage(offset), skipped_lines });
  const preview = await openPreview();

  expect(await within(preview).findByText(note)).toBeInTheDocument();

  await user.click(button("Next"));
  await within(preview).findByText("name51");
  expect(within(preview).getByText(note)).toBeInTheDocument();
});

test("no note appears when nothing was skipped", async () => {
  // CFX-APP-first-slice/AC-27
  const preview = await openPreview();
  await within(preview).findByText("name1");

  expect(preview).not.toHaveTextContent(/aren't shown|isn't shown/);
});

test("skipped lines are stated even when no rows follow them", async () => {
  // CFX-APP-first-slice/AC-27
  onRows = () => Response.json({ ...onePage(["id"], []), skipped_lines: 3 });

  const preview = await openPreview();

  expect(await within(preview).findByText("3 lines above this table aren't shown.")).toBeInTheDocument();
  expect(within(preview).getByText("No rows to show here.")).toBeInTheDocument();
});

test("a cut column name is visibly marked", async () => {
  // CFX-APP-first-slice/AC-29: found reviewing T06.
  onRows = () =>
    Response.json({
      ...onePage(["id"], [[cell("1"), cell("a")]]),
      columns: [cell("id"), cell("h".repeat(1000), true)],
    });

  const preview = await openPreview();

  const headers = await within(preview).findAllByRole("columnheader");
  expect(headers[1]).toHaveTextContent(/cut at 1,000 characters/i);
  expect(headers[0]).not.toHaveTextContent(/cut at/i);
});

test("the first page says it's loading", async () => {
  // Found reviewing T06: the preview showed nothing but its buttons until the rows arrived.
  let arrive = () => {};
  onRows = (_id, offset) =>
    new Promise((resolve) => {
      arrive = () => resolve(Response.json(peoplePage(offset)));
    });

  const preview = await openPreview();

  expect(await within(preview).findByRole("status")).toHaveTextContent("Loading…");
  arrive();
  expect(await within(preview).findByText("name1")).toBeInTheDocument();
  expect(within(preview).getByRole("status")).not.toHaveTextContent("Loading");
});

test("after an error the user can try again, without leaving the page", async () => {
  // CFX-APP-first-slice/AC-31: found reviewing T06, a restored file needed a reload to show.
  const user = userEvent.setup();
  const reason = "DrivnX can't find this file. It may have moved or been deleted.";
  onRows = () => Response.json({ detail: reason }, { status: 409 });
  const preview = await openPreview();
  await within(preview).findByRole("alert");
  expect(button("Next")).toBeDisabled();
  expect(button("Previous")).toBeDisabled();

  onRows = (_id, offset) => Response.json(peoplePage(offset));
  await user.click(button("Try again"));

  expect(await within(preview).findByText("name1")).toBeInTheDocument();
  expect(within(preview).queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
});
