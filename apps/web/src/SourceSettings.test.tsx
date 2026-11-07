import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import type { Source } from "./api";
import { Preview } from "./Preview";
import { SourceSettings } from "./SourceSettings";

const report: Source = {
  id: "s1",
  name: "report.csv",
  path: "/data/report.csv",
  delimiter: ",",
  encoding: "UTF-8",
  has_header: true,
  skip_lines: null,
  size_bytes: 1536,
  modified_at: "2024-03-05T10:00:00+00:00",
  available: true,
  columns: [{ name: "id", physical_type: "BIGINT", normalised_type: "integer" }],
};

const fetchMock = vi.fn();
let onSave: (body: unknown) => Response;
let skippedLines = 2;

beforeEach(() => {
  skippedLines = 2;
  onSave = () => Response.json(report);
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      return onSave(JSON.parse(String(init.body)));
    }
    if (url.includes("/rows")) {
      return Response.json({
        columns: [{ value: "id", cut: false }],
        rows: [],
        offset: 0,
        has_next: false,
        skipped_lines: skippedLines,
      });
    }
    return Response.json([report]);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function showSettings(source: Source = report) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SourceSettings source={source} />
    </QueryClientProvider>,
  );
  return client;
}

async function open() {
  await userEvent.click(screen.getByText("How this file is read"));
}

function savedBody() {
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(call?.[0]).toBe("/api/sources/s1/settings");
  return JSON.parse(String(call?.[1].body));
}

test("the settings stay out of the way until they're opened", () => {
  // CFX-APP-csv-profile/AC-01
  showSettings();

  expect(screen.queryByRole("combobox", { name: "Delimiter" })).not.toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});

test("the current settings are shown and can be changed", async () => {
  // CFX-APP-csv-profile/AC-01
  showSettings();
  await open();

  expect(screen.getByRole("checkbox", { name: "The first row holds column names" })).toBeChecked();
  expect(screen.getByRole("combobox", { name: "Delimiter" })).toHaveValue(",");
  expect(screen.getByRole("combobox", { name: "Encoding" })).toHaveValue("UTF-8");
  expect(
    within(screen.getByRole("combobox", { name: "Delimiter" }))
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["Comma", "Semicolon", "Tab", "Pipe"]);
  expect(
    within(screen.getByRole("combobox", { name: "Encoding" }))
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["UTF-8", "UTF-16 LE", "UTF-16 BE", "Latin-1", "Windows-1252"]);
  await waitFor(() =>
    expect(screen.getByRole("spinbutton", { name: "Lines to skip above the table" })).toHaveValue(2),
  );
  expect(screen.getByText("Worked out automatically")).toBeInTheDocument();
});

test("a number the user chose isn't described as automatic", async () => {
  // CFX-APP-csv-profile/AC-01
  showSettings({ ...report, skip_lines: 4 });
  await open();

  expect(screen.getByRole("spinbutton", { name: "Lines to skip above the table" })).toHaveValue(4);
  expect(screen.queryByText("Worked out automatically")).not.toBeInTheDocument();
});

test("saving sends what the user chose", async () => {
  // CFX-APP-csv-profile/AC-02
  showSettings();
  await open();

  await userEvent.click(screen.getByRole("checkbox", { name: "The first row holds column names" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Delimiter" }), "Tab");
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Encoding" }), "Latin-1");
  const skip = screen.getByRole("spinbutton", { name: "Lines to skip above the table" });
  await userEvent.clear(skip);
  await userEvent.type(skip, "3");
  await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

  expect(await screen.findByRole("status")).toHaveTextContent("Settings saved");
  expect(savedBody()).toEqual({
    has_header: false,
    delimiter: "\t",
    encoding: "Latin-1",
    skip_lines: 3,
  });
});

test("lines to skip stay automatic unless the user changes them", async () => {
  // CFX-APP-csv-profile/AC-02: with the right delimiter, the number can come out differently.
  showSettings();
  await open();

  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Delimiter" }), "Tab");
  await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

  await screen.findByRole("status");
  expect(savedBody().skip_lines).toBeNull();
});

test("after saving, the app shows the new settings and starts the preview again", async () => {
  // CFX-APP-csv-profile/AC-02
  let current = report;
  onSave = () => {
    current = { ...report, delimiter: "\t", columns: [{ ...report.columns[0], name: "corrected" }] };
    return Response.json(current);
  };
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      return onSave(JSON.parse(String(init.body)));
    }
    if (url === "/api/workspace") {
      return Response.json({ location: "/w", name: "Sales", ai_mode: "off", setup_complete: true });
    }
    if (url.includes("/rows")) {
      return Response.json({
        columns: [{ value: "id", cut: false }],
        rows: [[{ value: url.split("offset=")[1], cut: false }]],
        offset: Number(url.split("offset=")[1]),
        has_next: true,
        skipped_lines: 0,
      });
    }
    return Response.json([current]);
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <App signedIn />
    </QueryClientProvider>,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Next" }));
  expect(await screen.findByRole("cell", { name: "50" })).toBeInTheDocument();

  await userEvent.click(screen.getByText("How this file is read"));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Delimiter" }), "Tab");
  await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

  expect(await screen.findByText(/Settings saved/)).toBeInTheDocument();
  const details = screen.getByRole("region", { name: "report.csv" });
  expect(await within(details).findByRole("cell", { name: "corrected" })).toBeInTheDocument();
  expect(within(details).getAllByText("Tab").map((element) => element.tagName)).toContain("DD");
  expect(await screen.findByRole("cell", { name: "0" })).toBeInTheDocument();
  expect(screen.queryByRole("cell", { name: "50" })).not.toBeInTheDocument();
});

test("settings the file can't be read with are refused with the server's words", async () => {
  // CFX-APP-csv-profile/AC-03
  onSave = () =>
    Response.json(
      { detail: "Conflux can't read this file with these settings. Your previous settings are still in use." },
      { status: 422 },
    );
  showSettings();
  await open();

  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Encoding" }), "UTF-16 BE");
  await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Your previous settings are still in use");
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Encoding" })).toHaveValue("UTF-16 BE");
});

test("a file that has gone says so when saving", async () => {
  // CFX-APP-csv-profile/AC-03
  onSave = () =>
    Response.json({ detail: "Conflux can't find this file. It may have moved or been deleted." }, { status: 409 });
  showSettings();
  await open();

  await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Conflux can't find this file");
});

test("a busy workspace says so when saving, so the user tries again", async () => {
  // Found reviewing T07: the server says it's busy, and "Start Conflux again" was the wrong advice.
  onSave = () => Response.json({ detail: "Conflux is busy writing to this workspace. Try again." }, { status: 503 });
  showSettings();
  await open();

  await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Conflux is busy writing to this workspace. Try again.");
});

test.each(["", "1001", "2.5"])("%j isn't accepted as lines to skip", async (typed) => {
  // CFX-APP-csv-profile/AC-01
  showSettings();
  await open();

  const skip = screen.getByRole("spinbutton", { name: "Lines to skip above the table" });
  await userEvent.clear(skip);
  if (typed) {
    await userEvent.type(skip, typed);
  }
  await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Enter a whole number from 0 to 1,000.");
  expect(skip).toHaveAttribute("aria-invalid", "true");
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
});

test("settings can't be changed while the file can't be found", async () => {
  // CFX-APP-csv-profile/AC-03
  showSettings({ ...report, available: false });
  await open();

  expect(screen.getByText(/can't be changed until Conflux can find this file/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save settings" })).not.toBeInTheDocument();
});

test("saving the same settings after the file changed reads the rows again", async () => {
  // CFX-APP-csv-profile/AC-02: the settings didn't change, so nothing else would refresh the preview.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SourceSettings source={report} />
      <Preview sourceId="s1" />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  await open();
  await screen.findByText("Worked out automatically");
  await waitFor(() => expect(client.isFetching()).toBe(0));
  fetchMock.mockClear();

  await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

  await screen.findByRole("status");
  await waitFor(() =>
    expect(fetchMock.mock.calls.map(([url]) => url)).toContain("/api/sources/s1/rows?offset=0"),
  );
});
