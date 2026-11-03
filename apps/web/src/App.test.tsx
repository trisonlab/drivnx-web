import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";

const LOCATION = "/home/me/Conflux/default";
const fetchMock = vi.fn();

type Workspace = {
  location: string;
  name: string | null;
  ai_mode: "off" | "external" | "local";
  setup_complete: boolean;
};

const newWorkspace: Workspace = {
  location: LOCATION,
  name: null,
  ai_mode: "off",
  setup_complete: false,
};

function workspaceNamed(name: string, ai_mode: Workspace["ai_mode"] = "off"): Workspace {
  return { location: LOCATION, name, ai_mode, setup_complete: true };
}

function serverHas(workspace: Workspace, onSave?: (body: unknown) => Response) {
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === "/api/sources") {
      return Response.json([]);
    }
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      return onSave?.(body) ?? Response.json({ ...workspace, ...body, setup_complete: true });
    }
    return Response.json(workspace);
  });
}

function renderApp(signedIn = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <App signedIn={signedIn} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("tells a signed-out user how to get back in, and asks the server nothing", () => {
  renderApp(false);

  expect(screen.getByRole("alert")).toHaveTextContent(/start conflux again/i);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("a new workspace shows its location and asks for a name and AI mode", async () => {
  // CFX-APP-first-slice/AC-14
  serverHas(newWorkspace);
  renderApp();

  expect(await screen.findByText(LOCATION)).toBeInTheDocument();
  expect(screen.getByText(/no credentials are stored/i)).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Workspace name" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Off" })).toBeChecked();
  expect(screen.getByRole("radio", { name: "External" })).not.toBeChecked();
  expect(screen.getByRole("radio", { name: "Local" })).not.toBeChecked();
});

test("setup says that no AI request will be made in any mode", async () => {
  // CFX-APP-first-slice/AC-16
  serverHas(newWorkspace);
  renderApp();

  expect(await screen.findByText(/nothing is sent whichever you choose/i)).toBeInTheDocument();
});

test.each([
  ["Off", "off"],
  ["External", "external"],
  ["Local", "local"],
])("setup saves the name and the %s mode, then opens the workspace", async (label, mode) => {
  // CFX-APP-first-slice/AC-14
  const user = userEvent.setup();
  serverHas(newWorkspace);
  renderApp();

  await user.type(await screen.findByRole("textbox", { name: "Workspace name" }), "Customer data");
  await user.click(screen.getByRole("radio", { name: label }));
  await user.click(screen.getByRole("button", { name: "Create workspace" }));

  expect(await screen.findByRole("heading", { name: "Customer data" })).toBeInTheDocument();
  const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(put?.[0]).toBe("/api/workspace");
  expect(JSON.parse(put?.[1].body)).toEqual({ name: "Customer data", ai_mode: mode });
});

test("an empty name isn't sent", async () => {
  const user = userEvent.setup();
  serverHas(newWorkspace);
  renderApp();

  await screen.findByRole("textbox", { name: "Workspace name" });
  await user.click(screen.getByRole("button", { name: "Create workspace" }));

  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
});

test("a refused name keeps the form and says what went wrong", async () => {
  // Spaces only pass the browser's check and are refused by the server.
  const user = userEvent.setup();
  serverHas(newWorkspace, () => Response.json({ detail: [] }, { status: 422 }));
  renderApp();

  const name = await screen.findByRole("textbox", { name: "Workspace name" });
  await user.type(name, "   ");
  await user.click(screen.getByRole("button", { name: "Create workspace" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't save/i);
  expect(name).toHaveValue("   ");
});

test("a refused name asks the user to check it", async () => {
  const user = userEvent.setup();
  serverHas(newWorkspace, () => Response.json({ detail: [] }, { status: 422 }));
  renderApp();

  await user.type(await screen.findByRole("textbox", { name: "Workspace name" }), "Fine");
  await user.click(screen.getByRole("button", { name: "Create workspace" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/check the workspace name/i);
});

test.each([
  ["the session has ended", 401],
  ["the request was refused", 403],
  ["the server failed", 500],
])("when %s, it doesn't blame the name", async (_case, status) => {
  // Found in the T04 review.
  const user = userEvent.setup();
  serverHas(newWorkspace, () => Response.json({ detail: "no" }, { status }));
  renderApp();

  await user.type(await screen.findByRole("textbox", { name: "Workspace name" }), "Fine");
  await user.click(screen.getByRole("button", { name: "Create workspace" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/start conflux again/i);
  expect(alert).not.toHaveTextContent(/workspace name/i);
});

test("when the runtime has stopped, it doesn't blame the name", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      throw new TypeError("failed to fetch");
    }
    return Response.json(newWorkspace);
  });
  renderApp();

  await user.type(await screen.findByRole("textbox", { name: "Workspace name" }), "Fine");
  await user.click(screen.getByRole("button", { name: "Create workspace" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/start conflux again/i);
  expect(alert).not.toHaveTextContent(/workspace name/i);
});

test("when the workspace can't be loaded, it says so", async () => {
  fetchMock.mockRejectedValue(new TypeError("failed to fetch"));
  renderApp();

  expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't load/i);
});

test("a set-up workspace skips setup and shows the app layout", async () => {
  // CFX-APP-first-slice/AC-15 and CFX-APP-first-slice/AC-32
  serverHas(workspaceNamed("Sales"));
  renderApp();

  const banner = await screen.findByRole("banner");
  expect(screen.queryByRole("textbox", { name: "Workspace name" })).not.toBeInTheDocument();
  expect(within(banner).getByRole("heading", { name: "Sales" })).toBeInTheDocument();
  expect(within(banner).getByText(/profile status/i)).toBeInTheDocument();
  expect(within(banner).getByText("AI mode: Off")).toBeInTheDocument();
  expect(within(banner).getByRole("status")).toBeInTheDocument();
  expect(within(banner).getByRole("button", { name: /export/i })).toBeInTheDocument();
  expect(screen.getByRole("navigation", { name: "Data Explorer" })).toBeInTheDocument();
  expect(screen.getByRole("main")).toBeInTheDocument();
  expect(screen.getByRole("complementary", { name: "Intelligence & Review" })).toBeInTheDocument();
  for (const tab of ["Overview", "Profile", "Dictionary", "Contract"]) {
    expect(screen.getByRole("tab", { name: new RegExp(tab) })).toBeInTheDocument();
  }
});

test("everything not built is marked and has no working action", async () => {
  // CFX-APP-first-slice/AC-33
  serverHas(workspaceNamed("Sales"));
  renderApp();

  const banner = await screen.findByRole("banner");
  expect(within(banner).getByText(/profile status/i)).toHaveTextContent(/not available yet/i);
  const exportButton = within(banner).getByRole("button", { name: /export/i });
  expect(exportButton).toBeDisabled();
  expect(exportButton).toHaveAccessibleName(/not available yet/i);
  for (const tab of screen.getAllByRole("tab")) {
    expect(tab).toBeDisabled();
    expect(tab).toHaveTextContent(/not available yet/i);
  }
  expect(
    within(screen.getByRole("complementary", { name: "Intelligence & Review" })).getByText(
      /not available yet/i,
    ),
  ).toBeInTheDocument();
});

test.each([
  ["off", "Off", "Processing: Local · External AI: Off"],
  ["external", "External", "Processing: Local · External AI: On"],
  ["local", "Local", "Processing: Local · Local AI: On"],
] as const)("in %s mode the header shows the mode and privacy state", async (mode, label, text) => {
  // CFX-APP-first-slice/AC-34
  serverHas(workspaceNamed("Sales", mode));
  renderApp();

  const banner = await screen.findByRole("banner");
  expect(within(banner).getByRole("status")).toHaveTextContent(text);
  expect(within(banner).getByText(`AI mode: ${label}`)).toBeInTheDocument();
});

test("a name containing markup is shown as plain text", async () => {
  serverHas(workspaceNamed("<img src=x onerror=alert(1)>"));
  renderApp();

  expect(
    await screen.findByRole("heading", { name: "<img src=x onerror=alert(1)>" }),
  ).toBeInTheDocument();
  expect(document.querySelector("img")).toBeNull();
});
