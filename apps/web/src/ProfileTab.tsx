import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId } from "react";
import { getProfileRun, getProfileRuns, type Metric, type ProfileRun, RunRefusedError, type Source, startFastProfile } from "./api";

export const PROFILE_RUNS_QUERY = ["profile-runs"];
const POLL_MILLISECONDS = 1000;
const NOT_KNOWN = "Not known";
const METRIC_NAMES: Record<string, string> = {
  rows_read: "Rows read",
  missing_count: "Missing values",
};
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

function Results({ run }: { run: ProfileRun }) {
  const results = useQuery({ queryKey: ["profile-run", run.id], queryFn: () => getProfileRun(run.id) });
  if (results.isError) {
    return <p role="alert">Conflux couldn't load these results. Start Conflux again.</p>;
  }
  if (!results.data) {
    return null;
  }
  return (
    <div className="table-scroll">
      <table className="column-table">
        <caption>
          Run {run.id}, finished {new Date(run.ended_at ?? run.started_at).toLocaleString()}
        </caption>
        <thead>
          <tr>
            <th scope="col">Column</th>
            <th scope="col">Metric</th>
            <th scope="col">Value</th>
            <th scope="col">Measured on</th>
            <th scope="col">Precision</th>
          </tr>
        </thead>
        <tbody>
          {results.data.metrics.map((metric) => (
            <tr key={`${metric.column}|${metric.metric}`}>
              <td>{metric.column ?? "All columns"}</td>
              <td>{METRIC_NAMES[metric.metric] ?? metric.metric}</td>
              <td>{metric.value.toLocaleString()}</td>
              <td>{SCOPE_LABELS[metric.scope]}</td>
              <td>{PRECISION_LABELS[metric.precision]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
      {mine[0]?.status === "failed" && <p role="alert">This profile couldn't finish, so it has no results.</p>}
      {completed ? (
        <Results run={completed} />
      ) : (
        runs.isSuccess && mine.length === 0 && <p>No profile has been run with these settings yet.</p>
      )}
    </section>
  );
}
