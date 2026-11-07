import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import {
  type Cell,
  cancelProfile,
  FileUnreadableError,
  getColumnValues,
  getFindings,
  getProfileRun,
  getProfileRuns,
  type MaskedPattern,
  type Metric,
  type ProfileRun,
  RunRefusedError,
  type Source,
  startProfile,
} from "./api";
import { CellText } from "./Preview";

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

/** The run whose results are shown: the newest completed one with the current settings. */
export function shownRun(runs: ProfileRun[] | undefined, source: Source): ProfileRun | undefined {
  return runsWithCurrentSettings(runs, source).find((run) => run.status === "completed");
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

function Patterns({ patterns, measuredOn }: { patterns: MaskedPattern[]; measuredOn?: string }) {
  const count = patterns.length === 1 ? "1 pattern" : `${patterns.length} patterns`;
  return (
    <details>
      <summary>{measuredOn ? `${count}, from the ${measuredOn}` : count}</summary>
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

function ColumnRow({
  name,
  metrics,
  rowsRead,
  buttonId,
  onShowValues,
}: {
  name: string;
  metrics: Metric[];
  rowsRead: Metric["value"] | undefined;
  buttonId: string;
  onShowValues: () => void;
}) {
  const value = (metric: string) => metrics.find((candidate) => candidate.metric === metric)?.value;
  const patterns = value("masked_patterns");
  // A full profile still takes its patterns from the first rows only.
  const patternsScope = metrics.find((candidate) => candidate.metric === "masked_patterns")?.scope;
  return (
    <tr>
      <th scope="row">{name}</th>
      <td>{SCOPE_LABELS[metrics[0].scope]}</td>
      <td>{PRECISION_LABELS[metrics[0].precision]}</td>
      <td>{shown(rowsRead)}</td>
      <td id={evidenceId(name, "missing_count")} tabIndex={-1}>
        {withPercent(value("missing_count"), value("missing_percent"))}
      </td>
      <td id={evidenceId(name, "distinct_count")} tabIndex={-1}>
        {withPercent(value("distinct_count"), value("distinct_percent"))}
      </td>
      <td id={evidenceId(name, "not_fitting_count")} tabIndex={-1}>
        {shown(value("not_fitting_count"))}
      </td>
      <td>{shown(value("shortest_length"))}</td>
      <td>{shown(value("longest_length"))}</td>
      <td>{shown(value("average_length"))}</td>
      <td id={evidenceId(name, "masked_patterns")} tabIndex={-1}>
        {Array.isArray(patterns) ? (
          <Patterns
            patterns={patterns}
            measuredOn={patternsScope && patternsScope !== metrics[0].scope ? SCOPE_LABELS[patternsScope] : undefined}
          />
        ) : (
          NOT_SAVED
        )}
      </td>
      <td>
        <button type="button" id={buttonId} aria-label={`Show values of ${name}`} onClick={onShowValues}>
          Show values
        </button>
      </td>
    </tr>
  );
}

function extraColumnsNote(count: number): string {
  return count === 1
    ? "Some rows have 1 more field than the columns shown, and it isn't measured."
    : `Some rows have ${count} more fields than the columns shown, and they aren't measured.`;
}

function duplicateRowsNote(copies: number): string {
  if (copies === 0) {
    return "No row is an exact copy of another row.";
  }
  return copies === 1
    ? "1 row is an exact copy of another row."
    : `${copies.toLocaleString()} rows are exact copies of another row.`;
}

/** Where a metric is shown, so a finding's evidence link can go to it. */
export function evidenceId(column: string | null, metric: string): string {
  return `evidence-${metric.replace(/_(count|percent)$/, "")}-${encodeURIComponent(column ?? "")}`;
}

function skippedLinesNote(lines: number): string {
  return lines === 1
    ? "1 line above the table was left out, as in the preview."
    : `${lines} lines above the table were left out, as in the preview.`;
}

const VALUES_FAILED = "Conflux couldn't work out these values. Start Conflux again.";

/** A column's values, worked out from the file when asked for. They're forgotten once closed, as
 * nothing keeps them (ADR 0004). */
function ColumnValuesSection({
  run,
  column,
  confirmed,
  onClose,
}: {
  run: ProfileRun;
  column: string;
  confirmed: boolean;
  onClose: () => void;
}) {
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const values = useQuery({
    queryKey: ["values", run.id, column, confirmed],
    queryFn: () => getColumnValues(run.id, column, confirmed),
    gcTime: 0,
    retry: false,
  });
  useEffect(() => heading.current?.focus(), []);
  const found = values.data;
  const terms: [string, Cell | null][] = found
    ? [
        ["Minimum", found.minimum],
        ["Maximum", found.maximum],
        ["Mean", found.mean],
        ["Median", found.median],
        ["Standard deviation", found.standard_deviation],
      ]
    : [];
  return (
    <section className="column-values" aria-labelledby={headingId}>
      <h3 id={headingId} ref={heading} tabIndex={-1}>
        Values in {column}
      </h3>
      {values.isPending && <p role="status">Working out the values…</p>}
      {values.isError && (
        <p role="alert">{values.error instanceof FileUnreadableError ? values.error.message : VALUES_FAILED}</p>
      )}
      {found && (
        <>
          {/* AC-30: the part of the file and the time, as they can differ from the run's. */}
          <p>
            Computed {new Date(found.computed_at).toLocaleString()} from{" "}
            {found.scope === "full" ? "the whole file" : "the first 100,000 rows, not random"}, and not saved.
          </p>
          {found.frequent.length === 0 ? (
            <p>This column has no values in that part of the file.</p>
          ) : (
            <>
              <dl>
                {terms.map(
                  ([term, value]) =>
                    value && (
                      <div key={term}>
                        <dt>{term}</dt>
                        <dd>
                          <CellText cell={value} />
                        </dd>
                      </div>
                    ),
                )}
              </dl>
              <table>
                <caption>Most frequent values</caption>
                <thead>
                  <tr>
                    <th scope="col">Value</th>
                    <th scope="col">Count</th>
                  </tr>
                </thead>
                <tbody>
                  {/* By position, as two values cut at 1,000 characters can read the same. */}
                  {found.frequent.map((frequent, rank) => (
                    <tr key={rank}>
                      <td>
                        <CellText cell={frequent.value} />
                      </td>
                      <td>{frequent.count.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {found.histogram && found.histogram.length > 0 && (
            <>
              <table>
                <caption>Histogram</caption>
                <thead>
                  <tr>
                    <th scope="col">From</th>
                    <th scope="col">To</th>
                    <th scope="col">Count</th>
                  </tr>
                </thead>
                <tbody>
                  {/* By position, as edges printed to 15 digits can read the same. */}
                  {found.histogram.map((bucket, position) => (
                    <tr key={position}>
                      <td>
                        <CellText cell={bucket.low} />
                      </td>
                      <td>
                        <CellText cell={bucket.high} />
                      </td>
                      <td>{bucket.count.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p>Each range holds values above From, up to and including To. The first includes From too.</p>
            </>
          )}
        </>
      )}
      <p>
        <button type="button" onClick={onClose}>
          Close values
        </button>
      </p>
    </section>
  );
}

/** AC-29: asked before a possibly-sensitive column's values are worked out. */
function SensitiveConfirmation({ onShow, onDecline }: { onShow: () => void; onDecline: () => void }) {
  const questionId = useId();
  const decline = useRef<HTMLButtonElement>(null);
  useEffect(() => decline.current?.focus(), []);
  return (
    <div role="alertdialog" aria-labelledby={questionId} className="sensitive-confirmation">
      <p id={questionId}>This column may hold personal data. Show its values?</p>
      <p>
        <button type="button" onClick={onShow}>
          Show values
        </button>{" "}
        <button type="button" ref={decline} onClick={onDecline}>
          Don't show
        </button>
      </p>
    </div>
  );
}

function Results({ run }: { run: ProfileRun }) {
  const results = useQuery({ queryKey: ["profile-run", run.id], queryFn: () => getProfileRun(run.id) });
  // The same query the findings panel makes, so the server's refusal is rarely what asks.
  const findings = useQuery({ queryKey: ["findings", run.id], queryFn: () => getFindings(run.id) });
  const [asked, setAsked] = useState<{ column: string; confirmed: boolean } | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const buttonsId = useId();
  const flagged = new Set(
    findings.data
      ?.filter((finding) => finding.kind === "possibly_sensitive")
      .flatMap((finding) => finding.evidence.flatMap((cited) => cited.column ?? [])),
  );
  const showValues = (column: string) => {
    setAsked(flagged.has(column) ? null : { column, confirmed: false });
    setConfirming(flagged.has(column) ? column : null);
  };
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
  const duplicates = ofTheFile("duplicate_rows");
  const rowsRead = metrics.find((metric) => metric.column === null && metric.metric === "rows_read");
  // Only a run that read every row knows how many the file has (first slice AC-25).
  const fileRows = rowsRead?.scope === "full" && typeof rowsRead.value === "number" ? rowsRead.value : null;
  const columns = [...new Set(metrics.flatMap((metric) => metric.column ?? []))];
  const buttonOf = (column: string) => `${buttonsId}-${columns.indexOf(column)}`;
  // Back where it was once what opened from it closes, as for a keyboard it would be lost.
  const focusButtonOf = (column: string) => document.getElementById(buttonOf(column))?.focus();
  return (
    <>
      {fileRows !== null && (
        <p>{fileRows === 1 ? "This file has 1 row." : `This file has ${fileRows.toLocaleString()} rows.`}</p>
      )}
      {typeof duplicates === "number" && (
        <p id={evidenceId(null, "duplicate_rows")} tabIndex={-1}>
          {duplicateRowsNote(duplicates)}
        </p>
      )}
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
              <th scope="col">Values</th>
            </tr>
          </thead>
          <tbody>
            {columns.map((column) => (
              <ColumnRow
                key={column}
                name={column}
                metrics={metrics.filter((metric) => metric.column === column)}
                rowsRead={ofTheFile("rows_read")}
                buttonId={buttonOf(column)}
                onShowValues={() => showValues(column)}
              />
            ))}
          </tbody>
        </table>
      </div>
      {confirming && (
        <SensitiveConfirmation
          onShow={() => {
            setAsked({ column: confirming, confirmed: true });
            setConfirming(null);
          }}
          onDecline={() => {
            setConfirming(null);
            focusButtonOf(confirming);
          }}
        />
      )}
      {asked && (
        <ColumnValuesSection
          key={`${asked.column}-${asked.confirmed}`}
          run={run}
          column={asked.column}
          confirmed={asked.confirmed}
          onClose={() => {
            setAsked(null);
            focusButtonOf(asked.column);
          }}
        />
      )}
    </>
  );
}

export function ProfileTab({ source }: { source: Source }) {
  const headingId = useId();
  const queryClient = useQueryClient();
  const runs = useProfileRuns();
  const start = useMutation({
    mutationFn: (mode: ProfileRun["mode"]) => startProfile(source.id, mode),
    // Also after a refusal, which can come from a run this page hasn't heard of.
    onSettled: () => queryClient.invalidateQueries({ queryKey: PROFILE_RUNS_QUERY }),
  });
  const cancel = useMutation({
    mutationFn: cancelProfile,
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
  const completed = shownRun(runs.data, source);

  return (
    <section className="profile-tab" aria-labelledby={headingId}>
      <h2 id={headingId}>Profile</h2>
      <p>
        <button type="button" onClick={() => start.mutate("fast")}>
          Fast profile
        </button>{" "}
        <button type="button" onClick={() => start.mutate("full")}>
          Full profile
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
      {running?.source_id === source.id && (
        <p>
          <button type="button" onClick={() => cancel.mutate(running.id)}>
            Cancel profile
          </button>
        </p>
      )}
      {mine[0]?.status === "failed" && (
        <p role="alert">
          {mine[0].failure
            ? `This profile couldn't finish. ${mine[0].failure}`
            : "This profile couldn't finish, so it has no results."}
        </p>
      )}
      {mine[0]?.status === "cancelled" && <p>This profile was cancelled.</p>}
      {(mine[0]?.status === "failed" || mine[0]?.status === "cancelled") && completed && (
        <p>The results below are from the run before.</p>
      )}
      {completed ? (
        <Results key={completed.id} run={completed} />
      ) : (
        runs.isSuccess && mine.length === 0 && <p>No profile has been run with these settings yet.</p>
      )}
    </section>
  );
}
