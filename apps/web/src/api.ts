export type AiMode = "off" | "external" | "local";

export type Workspace = {
  location: string;
  name: string | null;
  ai_mode: AiMode;
  setup_complete: boolean;
};

const WORKSPACE_PATH = "/api/workspace";

export class InvalidInputError extends Error {}

async function workspaceFrom(response: Response): Promise<Workspace> {
  if (response.status === 422) {
    throw new InvalidInputError();
  }
  if (!response.ok) {
    throw new Error(`The server answered ${response.status}`);
  }
  return response.json();
}

export async function getWorkspace(): Promise<Workspace> {
  return workspaceFrom(await fetch(WORKSPACE_PATH));
}

export async function saveWorkspace(settings: { name: string; ai_mode: AiMode }): Promise<Workspace> {
  return workspaceFrom(
    await fetch(WORKSPACE_PATH, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
  );
}

export type Column = {
  name: string;
  physical_type: string;
  normalised_type: string;
};

export type Source = {
  id: string;
  name: string;
  path: string;
  delimiter: string;
  encoding: string;
  has_header: boolean;
  size_bytes: number;
  modified_at: string;
  available: boolean;
  columns: Column[];
};

const SOURCES_PATH = "/api/sources";

export async function getSources(): Promise<Source[]> {
  const response = await fetch(SOURCES_PATH);
  if (!response.ok) {
    throw new Error(`The server answered ${response.status}`);
  }
  return response.json();
}

/** The server refused this path, and may say why in words written for the user. */
export class RefusedPathError extends Error {
  readonly reason: string | null;

  constructor(reason: string | null) {
    super(reason ?? "Refused");
    this.reason = reason;
  }
}

export async function connectSource(path: string): Promise<Source> {
  const response = await fetch(SOURCES_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (response.status === 422) {
    const { detail } = await response.json();
    throw new RefusedPathError(typeof detail === "string" ? detail : null);
  }
  if (!response.ok) {
    throw new Error(`The server answered ${response.status}`);
  }
  return response.json();
}
