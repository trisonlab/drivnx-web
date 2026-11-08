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
  skip_lines: number | null;
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

export type Cell = { value: string | null; cut: boolean };

export type RowsPage = {
  columns: Cell[];
  rows: Cell[][];
  offset: number;
  has_next: boolean;
  skipped_lines: number;
};

/** The file can't answer right now. The message is written for the user. */
export class FileUnreadableError extends Error {}

export async function getRows(sourceId: string, offset: number): Promise<RowsPage> {
  const response = await fetch(`${SOURCES_PATH}/${sourceId}/rows?offset=${offset}`);
  if (response.status === 409) {
    const { detail } = await response.json();
    throw new FileUnreadableError(detail);
  }
  if (!response.ok) {
    throw new Error(`The server answered ${response.status}`);
  }
  return response.json();
}

export type SourceSettingsChoice = {
  has_header: boolean;
  delimiter: string;
  encoding: string;
  skip_lines: number | null;
};

export async function saveSourceSettings(sourceId: string, settings: SourceSettingsChoice): Promise<Source> {
  const response = await fetch(`${SOURCES_PATH}/${sourceId}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (response.status === 409 || response.status === 422 || response.status === 503) {
    const { detail } = await response.json();
    throw new FileUnreadableError(typeof detail === "string" ? detail : "DrivnX couldn't save these settings.");
  }
  if (!response.ok) {
    throw new Error(`The server answered ${response.status}`);
  }
  return response.json();
}

export type ProfileRun = {
  id: string;
  source_id: string;
  mode: "fast" | "full";
  status: "running" | "completed" | "cancelled" | "failed" | "interrupted";
  started_at: string;
  ended_at: string | null;
  current_settings: boolean;
  // Why it failed, when DrivnX knows. Not kept across a restart.
  failure: string | null;
};

export type MaskedPattern = { pattern: string; count: number; cut: boolean };

export type Metric = {
  column: string | null;
  metric: string;
  value: number | MaskedPattern[];
  scope: "full" | "sample" | "metadata";
  precision: "exact" | "approximate";
};

export type ProfileRunDetail = ProfileRun & { metrics: Metric[] };

const PROFILE_RUNS_PATH = "/api/profile-runs";

export async function getProfileRuns(): Promise<ProfileRun[]> {
  const response = await fetch(PROFILE_RUNS_PATH);
  if (!response.ok) {
    throw new Error(`The server answered ${response.status}`);
  }
  return response.json();
}

export async function getProfileRun(runId: string): Promise<ProfileRunDetail> {
  const response = await fetch(`${PROFILE_RUNS_PATH}/${runId}`);
  if (!response.ok) {
    throw new Error(`The server answered ${response.status}`);
  }
  return response.json();
}

export type ColumnValues = {
  profile_run_id: string;
  column: string;
  // The part of the file read, as a metric's scope says it.
  scope: "full" | "sample";
  rows_read: number;
  computed_at: string;
  minimum: Cell | null;
  maximum: Cell | null;
  frequent: { value: Cell; count: number }[];
  // Values above `low` up to `high`. The first bucket holds the minimum too.
  histogram: { low: Cell; high: Cell; count: number }[] | null;
  mean: Cell | null;
  median: Cell | null;
  standard_deviation: Cell | null;
};

/** Computed now from the file, and never saved (ADR 0004). `confirmed` is the extra confirmation a
 * possibly-sensitive column needs (AC-29). */
export async function getColumnValues(runId: string, column: string, confirmed: boolean): Promise<ColumnValues> {
  const params = new URLSearchParams({ column, ...(confirmed && { confirmed: "true" }) });
  const response = await fetch(`${PROFILE_RUNS_PATH}/${runId}/values?${params}`);
  if (response.status === 404 || response.status === 409) {
    const { detail } = await response.json();
    throw new FileUnreadableError(detail);
  }
  if (!response.ok) {
    throw new Error(`The server answered ${response.status}`);
  }
  return response.json();
}

/** The server wouldn't start this run. The message is written for the user. */
export class RunRefusedError extends Error {}

export type Finding = {
  id: string;
  profile_run_id: string;
  kind: string;
  interpretation: string;
  why_it_matters: string;
  recommended_action: string;
  state: "inferred";
  // The observations it cites, with the column's current name.
  evidence: Metric[];
};

export async function getFindings(runId: string): Promise<Finding[]> {
  const response = await fetch(`/api/findings?profile_run_id=${encodeURIComponent(runId)}`);
  if (!response.ok) {
    throw new Error(`The server answered ${response.status}`);
  }
  return response.json();
}

export async function cancelProfile(runId: string): Promise<ProfileRun> {
  const response = await fetch(`${PROFILE_RUNS_PATH}/${runId}/cancel`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`The server answered ${response.status}`);
  }
  return response.json();
}

export async function startProfile(sourceId: string, mode: ProfileRun["mode"]): Promise<ProfileRun> {
  const response = await fetch(PROFILE_RUNS_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_id: sourceId, mode }),
  });
  if (response.status === 409 || response.status === 503) {
    const { detail } = await response.json();
    throw new RunRefusedError(detail);
  }
  if (!response.ok) {
    throw new Error(`The server answered ${response.status}`);
  }
  return response.json();
}
