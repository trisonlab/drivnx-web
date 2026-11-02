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
