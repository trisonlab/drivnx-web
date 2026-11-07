import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId } from "react";
import {
  getProfileRun,
  getProfileRuns,
  type MaskedPattern,
  type Metric,
  type ProfileRun,
  RunRefusedError,
  type Source,
  startFastProfile,
} from "./api";

export const PROFILE_RUNS_QUERY = ["profile-runs"];
const POLL_MILLISECONDS = 1000;
const NOT_KNOWN = "Not known";
const NOT_SAVED = "\u2014";
const SCOPE_LABELS: Record<Metric["scope"], string> = {
  full: "whole file",
  sample: "first 100,000 rows, not random",
  metadata: "metadata",
};
const PRECISION_LABELS: Record<Metric["precision"], string> = {
  exact: "Exact",
  approximate: "Approximate",
};
const STATUS_LABELS: Record<Exclude<ProfileRun["status"], "running">, string> = {
  completed: "Completed",
  cancelled: "Cancelled",
  failed: "Failed",
  interrupted: "Interrupted",
};

export function useProfileRuns() {
  return useQuery({
    queryKey: PROFILE_RUNS_QUERY,
    queryFn: getProfileRuns,
    refetchInterval: (query) =>
      query.state.data?.some((run) => run.status === "running") ? POLL_MILLISECONDS : false,
  });
}

function runsWithCurrentSettings(runs: ProfileRun[] | undefined, source: Source): ProfileRun[] {
  return runs?.filter((run) => run.source_id === source.id && run.current_settings) ?? [];
}

/** One run at a time across the workspace, so a run in progress is named wherever the user is. */
export function profileStatus(
  runs: ProfileRun[] | undefined,
  sources: Source[] | undefined,
  chosen: Source | undefined,
): string {
  if (!runs) {
    return NOT_KNOWN;
  }
  const running = runs.find((run) => run.status === "running");
  if (running) {
    const name = sources?.find((source) => source.id === running.source_id)?.name;
    return name ? `Running on ${name}` : "Running";
  }
  const newest = chosen && runsWithCurrentSettings(runs, chosen)[0];
  if (!newest || newest.status === "running") {
    return "Not run yet";
  }
  return STATUS_LABELS[newest.status];
}

function startFailure(error: Error): string {
  return error instanceof RunRefusedError ? error.message : "Conflux couldn't start this profile. Start Conflux again.";
}

function shown(value: Metric["value"] | undefined): string {
  return typeof value === "number" ? value.toLocaleString() : NOT_SAVED;
}

function withPercent(count: Metric["value"] | undefined, percent: Metric["value"] | undefined): string {
  return typeof percent === "number" ? `${shown(count)} (${percent}%)` : shown(count);
}

function Patterns({ patterns }: { patterns: MaskedPattern[] }) {
  return (
    <details>
      <summary>{patterns.length === 1 ? "1 pattern" : `${patterns.length} patterns`}</summary>
      <ul className="masked-patterns">
        {patterns.map((found) => (
          <li key={`${found.pattern}|${found.cut}`}>
            <code>{found.pattern}</code> &times; {found.count.toLocaleString()}
            {found.cut && <span className="cut-mark"> … cut at 40 characters</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}

function ColumnRow({ name, metrics, rowsRead }: { name: string; metrics: Metric[]; rowsRead: Metric["value"] | undefined }) {
  const value = (metric: string) => metrics.find((candidate) => candidate.metric === metric)?.value;
  const patterns = value("masked_patterns");
  return (
    <tr>
      <th scope="row">{name}</th>
      <td>{SCOPE_LABELS[metrics[0].scope]}</td>
      <td>{PRECISION_LABELS[metrics[0].precision]}</td>
      <td>{shown(rowsRead)}</td>
      <td>{withPercent(value("missing_count"), value("missing_percent"))}</td>
      <td>{withPercent(value("distinct_count"), value("distinct_percent"))}</td>
      <td>{shown(value("not_fitting_count"))}</td>
      <td>{shown(value("shortest_length"))}</td>
      <td>{shown(value("longest_length"))}</td>
      <td>{shown(value("average_length"))}</td>
      <td>{Array.isArray(patterns) ? <Patterns patterns={patterns} /> : NOT_SAVED}</td>
    </tr>
  );
}

function extraColumnsNote(count: number): string {
  return count === 1
    ? "Some rows have 1 more field than the columns shown, and it isn't measured."
    : `Some rows have ${count} more fields than the columns shown, and they aren't measured.`;
}

function skippedLinesNote(lines: number): string {
  return lines === 1
    ? "1 line above the table was left out, as in the preview."
    : `${lines} lines above the table were left out, as in the preview.`;
}

function Results({ run }: { run: ProfileRun }) {
  const results = useQuery({ queryKey: ["profile-run", run.id], queryFn: () => getProfileRun(run.id) });
  if (results.isError) {
    return <p role="alert">Conflux couldn't load these results. Start Conflux again.</p>;
  }
  if (!results.data) {
    return null;
  }
  const metrics = results.data.metrics;
  const ofTheFile = (name: string) => metrics.find((metric) => metric.column === null && metric.metric === name)?.value;
  const extraColumns = ofTheFile("extra_columns");
  const skippedLines = ofTheFile("skipped_lines");
  const columns = [...new Set(metrics.flatMap((metric) => metric.column ?? []))];
  return (
    <>
      {typeof skippedLines === "number" && <p>{skippedLinesNote(skippedLines)}</p>}
      {typeof extraColumns === "number" && <p>{extraColumnsNote(extraColumns)}</p>}
      <div className="table-scroll">
        <table className="column-table">
          <caption>
            Run {run.id}, finished {new Date(run.ended_at ?? run.started_at).toLocaleString()}
          </caption>
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Measured on</th>
              <th scope="col">Precision</th>
              <th scope="col">Rows read</th>
              <th scope="col">Missing</th>
              <th scope="col">Distinct</th>
              <th scope="col">Not fitting</th>
              <th scope="col">Shortest</th>
              <th scope="col">Longest</th>
              <th scope="col">Average</th>
              <th scope="col">Patterns</th>
            </tr>
          </thead>
          <tbody>
            {columns.map((column) => (
              <ColumnRow
                key={column}
                name={column}
                metrics={metrics.filter((metric) => metric.column === column)}
                rowsRead={ofTheFile("rows_read")}
              />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function ProfileTab({ source }: { source: Source }) {
  const headingId = useId();
  const queryClient = useQueryClient();
  const runs = useProfileRuns();
  const start = useMutation({
    mutationFn: () => startFastProfile(source.id),
    // Also after a refusal, which can come from a run this page hasn't heard of.
    onSettled: () => queryClient.invalidateQueries({ queryKey: PROFILE_RUNS_QUERY }),
  });
  const { reset: forgetRefusal } = start;
  const running = runs.data?.find((run) => run.status === "running");
  const anyRunning = running !== undefined;
  // A refusal because a run is in progress stops being true when that run ends.
  useEffect(() => {
    if (!anyRunning) {
      forgetRefusal();
    }
  }, [anyRunning, forgetRefusal]);
  const mine = runsWithCurrentSettings(runs.data, source);
  const completed = mine.find((run) => run.status === "completed");

  return (
    <section className="profile-tab" aria-labelledby={headingId}>
      <h2 id={headingId}>Profile</h2>
      <p>
        <button type="button" onClick={() => start.mutate()}>
          Fast profile
        </button>
      </p>
      {start.isError && <p role="alert">{startFailure(start.error)}</p>}
      {running && (
        <p role="status">
          {running.source_id === source.id
            ? "A profile is running. Results appear here when it finishes."
            : "A profile is running on another file."}
        </p>
      )}
      {mine[0]?.status === "failed" && (
        <p role="alert">
          {mine[0].failure
            ? `This profile couldn't finish. ${mine[0].failure}`
            : "This profile couldn't finish, so it has no results."}
        </p>
      )}
      {mine[0]?.status === "failed" && completed && <p>The results below are from the run before.</p>}
      {completed ? (
        <Results run={completed} />
      ) : (
        runs.isSuccess && mine.length === 0 && <p>No profile has been run with these settings yet.</p>
      )}
    </section>
  );
}
