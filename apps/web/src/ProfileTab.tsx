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
  type ProfileRunDetail,
  RunRefusedError,
  type Source,
  startProfile,
  type WriteAccess,
  WriteAccessError,
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
const MODE_LABELS: Record<ProfileRun["mode"], string> = {
  fast: "Fast profile",
  full: "Full profile",
};

export function useProfileRuns() {
  return useQuery({
    queryKey: PROFILE_RUNS_QUERY,
    queryFn: getProfileRuns,
    refetchInterval: (query) =>
      query.state.data?.some((run) => run.status === "running") ? POLL_MILLISECONDS : false,
  });
}

/** The runs the tab and the header go by, newest first: a file's with its current settings
 * (DRIVN-9 AC-35), and every one of a database's, which says when its objects have changed
 * since (DRIVN-45 AC-31, AC-32). */
function runsOfSource(runs: ProfileRun[] | undefined, source: Source): ProfileRun[] {
  return (
    runs?.filter((run) => run.source_id === source.id && (source.kind === "postgres" || run.current_settings)) ?? []
  );
}

function newestCompleted(runs: ProfileRun[] | undefined, source: Source): ProfileRun | undefined {
  return runsOfSource(runs, source).find((run) => run.status === "completed");
}

/** The run whose results are shown: the one the user opened, or else the newest completed one of
 * `runsOfSource`. The findings panel shows the same one. */
export function shownRun(
  runs: ProfileRun[] | undefined,
  source: Source,
  openedId: string | null,
): ProfileRun | undefined {
  const opened = runs?.find(
    (run) => run.id === openedId && run.source_id === source.id && run.status === "completed",
  );
  return opened ?? newestCompleted(runs, source);
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
    // A database's run also names the object it's reading (DRIVN-45 AC-17).
    const on = running.current_object ? `${name}: ${running.current_object}` : name;
    return name ? `Running on ${on}` : "Running";
  }
  const newest = chosen && runsOfSource(runs, chosen)[0];
  if (!newest || newest.status === "running") {
    return "Not run yet";
  }
  return STATUS_LABELS[newest.status];
}

const MINUTES_NEEDED = "Enter a whole number of minutes from 1 to 120.";

/** A full profile's time limit as the API keeps it, 1 to 120 whole minutes, or null. */
function timeLimit(typed: string): number | null {
  const minutes = Number(typed);
  return /^\d+$/.test(typed.trim()) && minutes >= 1 && minutes <= 120 ? minutes : null;
}

function startFailure(error: Error): string {
  return error instanceof RunRefusedError ? error.message : "DrivnX couldn't start this profile. Start DrivnX again.";
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
      <summary>{measuredOn ? `${count}, from ${measuredOn}` : count}</summary>
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
  labels,
  buttonId,
  onShowValues,
}: {
  name: string;
  metrics: Metric[];
  rowsRead: Metric["value"] | undefined;
  labels: Labels;
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
      <td>{labels.measuredOn[metrics[0].scope]}</td>
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
            measuredOn={
              patternsScope && patternsScope !== metrics[0].scope ? labels.patternsFrom[patternsScope] : undefined
            }
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

/** Where a metric is shown, so a finding's evidence link can go to it. A database run's metric is
 * of one object, which a column's encoded name can't run into, as `/` is encoded. */
export function evidenceId(column: string | null, metric: string, object?: string | null): string {
  const of = object ? `${encodeURIComponent(object)}/` : "";
  return `evidence-${metric.replace(/_(count|percent)$/, "")}-${of}${encodeURIComponent(column ?? "")}`;
}

function skippedLinesNote(lines: number): string {
  return lines === 1
    ? "1 line above the table was left out, as in the preview."
    : `${lines} lines above the table were left out, as in the preview.`;
}

const VALUES_FAILED = "DrivnX couldn't work out these values. Start DrivnX again.";

/** A column's values, worked out from the file when asked for. They're forgotten once closed, as
 * nothing keeps them (ADR 0004). */
function ColumnValuesSection({
  run,
  column,
  confirmed,
  of,
  parquet,
  onClose,
}: {
  run: ProfileRun;
  column: string;
  confirmed: boolean;
  // A database run's object, read again as the run read it (DRIVN-56).
  of?: DatabaseObject;
  // A Parquet run's sample is its seeded slices (DRIVN-60 AC-14).
  parquet: boolean;
  onClose: () => void;
}) {
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const values = useQuery({
    queryKey: ["values", run.id, of?.object, column, confirmed],
    queryFn: () => getColumnValues(run.id, column, confirmed, of),
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
        {of && ` of ${of.object}`}
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
            {found.method
              ? READ_AS[found.method](shown(found.rows_read))
              : parquet
                ? found.scope === "full"
                  ? `every row, ${shown(found.rows_read)} rows`
                  : `about ${shown(found.rows_read)} rows, sampled by row group`
                : found.scope === "full"
                  ? "the whole file"
                  : "the first 100,000 rows, not random"}
            , and not saved.
            {/* DRIVN-45 AC-28: read again now, so only as the run's rows while the object is unchanged,
                and a view's rows can differ even then. */}
            {of &&
              (found.kind === "view"
                ? " The view may return other rows than it did for this run."
                : ` These are the same rows as this run's only if ${of.object} hasn't changed since this run.`)}
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

/** Before a database is profiled with a role that could change the chosen objects (DRIVN-45
 * AC-16): which, and with what. DrivnX itself only reads. */
function WriteAccessConfirmation({
  access,
  onProfile,
  onCancel,
}: {
  access: WriteAccess;
  onProfile: () => void;
  onCancel: () => void;
}) {
  const questionId = useId();
  // Focus on the choice that changes nothing, as `SensitiveConfirmation` does.
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => cancel.current?.focus(), []);
  return (
    <div role="alertdialog" aria-labelledby={questionId}>
      {access.superuser ? (
        // It could change anything, so the role is the reason, whatever the objects.
        <>
          <p id={questionId}>
            {access.role} is a superuser, so it can change anything in this database. Profile it anyway?
          </p>
          <p>DrivnX opens every session read-only and only reads it.</p>
        </>
      ) : (
        <>
          <p id={questionId}>{access.role} can change some of the chosen objects. Profile them anyway?</p>
          <ul>
            {access.objects.map(({ object, privileges }) => (
              <li key={object}>
                {object}: {privileges.map((privilege) => privilege.toLowerCase()).join(", ")}
              </li>
            ))}
          </ul>
          <p>DrivnX opens every session read-only and only reads them.</p>
        </>
      )}
      <p>
        <button type="button" onClick={onProfile}>
          Profile anyway
        </button>{" "}
        <button type="button" ref={cancel} onClick={onCancel}>
          Cancel
        </button>
      </p>
    </div>
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

type AskedValues = { column: string; of?: DatabaseObject };

/** A column's values on request: asked about first if the run found the column possibly sensitive
 * (AC-29, DRIVN-45 AC-27), and the focus back on its button once they close. `buttonId` names
 * each column's button; `show` asks for its values; `section` is what then shows. */
function useValuesOnRequest(run: ProfileRun, parquet = false) {
  // The same query the findings panel makes, so the server's refusal is rarely what asks.
  const findings = useQuery({ queryKey: ["findings", run.id], queryFn: () => getFindings(run.id) });
  const [asked, setAsked] = useState<(AskedValues & { confirmed: boolean }) | null>(null);
  const [confirming, setConfirming] = useState<AskedValues | null>(null);
  const buttonsId = useId();
  // By object and column, as two of a database's objects can have a column of the same name.
  const key = (column: string, object?: string | null) => `${object ?? ""}\u0000${column}`;
  const flagged = new Set(
    findings.data
      ?.filter((finding) => finding.kind === "possibly_sensitive")
      .flatMap((finding) => finding.evidence.flatMap((cited) => (cited.column ? [key(cited.column, cited.object)] : []))),
  );
  const buttonId = (column: string, of?: DatabaseObject) =>
    `${buttonsId}-${encodeURIComponent(key(column, of?.object))}`;
  // Back where it was once what opened from it closes, as for a keyboard it would be lost.
  const focusButtonOf = ({ column, of }: AskedValues) => document.getElementById(buttonId(column, of))?.focus();
  const show = (wanted: AskedValues) => {
    const sensitive = flagged.has(key(wanted.column, wanted.of?.object));
    setAsked(sensitive ? null : { ...wanted, confirmed: false });
    setConfirming(sensitive ? wanted : null);
  };
  const section = (
    <>
      {confirming && (
        <SensitiveConfirmation
          onShow={() => {
            setAsked({ ...confirming, confirmed: true });
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
          key={`${key(asked.column, asked.of?.object)}-${asked.confirmed}`}
          run={run}
          column={asked.column}
          confirmed={asked.confirmed}
          of={asked.of}
          parquet={parquet}
          onClose={() => {
            setAsked(null);
            focusButtonOf(asked);
          }}
        />
      )}
    </>
  );
  return { buttonId, show, section };
}

/** AC-35: every run of this file, newest first. A completed one that isn't shown can be opened. */
// A run's objects named in the runs list; its results name every one (decided for DRIVN-57).
const OBJECTS_NAMED = 3;

function namedObjects(names: string[]): string {
  const more = names.length - OBJECTS_NAMED;
  return more > 0 ? `${names.slice(0, OBJECTS_NAMED).join(", ")} and ${more} more` : names.join(", ");
}

function RunsList({
  runs,
  shown,
  onOpen,
  of,
}: {
  runs: ProfileRun[];
  shown: ProfileRun | undefined;
  onOpen: (runId: string) => void;
  of: "file" | "database";
}) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId}>
      <h3 id={headingId}>Runs of this {of}</h3>
      <ol className="runs-list" aria-labelledby={headingId}>
        {runs.map((run) => {
          const started = new Date(run.started_at).toLocaleString();
          return (
            <li key={run.id}>
              {MODE_LABELS[run.mode]}, {run.status === "running" ? "Running" : STATUS_LABELS[run.status]}, started{" "}
              {started}
              {/* DRIVN-45 AC-31 */}
              {run.object_names && `, of ${namedObjects(run.object_names)}`}
              {/* AC-36; for a database, a refresh has since changed one of its objects (DRIVN-45 AC-32). */}
              {!run.current_settings && (of === "database" ? " · Out of date" : " · Used earlier settings")}
              {run.id === shown?.id ? (
                " · Shown"
              ) : (
                run.status === "completed" && (
                  <>
                    {" "}
                    <button type="button" aria-label={`Open the ${MODE_LABELS[run.mode].toLowerCase()} started ${started}`} onClick={() => onOpen(run.id)}>
                      Open
                    </button>
                  </>
                )
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** What a file run's metrics were measured on, and where a full run's patterns are from. */
type Labels = { measuredOn: Record<Metric["scope"], string>; patternsFrom: Record<Metric["scope"], string> };

const CSV_LABELS: Labels = {
  measuredOn: SCOPE_LABELS,
  patternsFrom: {
    full: `the ${SCOPE_LABELS.full}`,
    sample: `the ${SCOPE_LABELS.sample}`,
    metadata: `the ${SCOPE_LABELS.metadata}`,
  },
};

/** A Parquet file's: a sample of seeded slices, or every row (DRIVN-60 AC-12, AC-30). */
function parquetLabels(rowsRead: Metric["value"] | undefined, sampleRows: Metric["value"] | undefined): Labels {
  const labels = {
    full: `every row, ${shown(rowsRead)} rows`,
    sample: `about ${shown(sampleRows)} rows, sampled by row group`,
    metadata: "the file's metadata",
  };
  return { measuredOn: labels, patternsFrom: labels };
}

function Results({ run, parquet = false }: { run: ProfileRun; parquet?: boolean }) {
  const results = useQuery({ queryKey: ["profile-run", run.id], queryFn: () => getProfileRun(run.id) });
  const values = useValuesOnRequest(run, parquet);
  if (results.isError) {
    return <p role="alert">DrivnX couldn't load these results. Start DrivnX again.</p>;
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
  // A Parquet file's comes from its metadata (DRIVN-60 AC-12).
  const rowCount = ofTheFile("row_count");
  // Only a run that read every row knows how many the file has (first slice AC-25).
  const fileRows =
    !parquet && rowsRead?.scope === "full" && typeof rowsRead.value === "number" ? rowsRead.value : null;
  const labels = parquet
    ? parquetLabels(rowsRead?.value, rowsRead?.scope === "sample" ? rowsRead.value : ofTheFile("sample_rows_read"))
    : CSV_LABELS;
  const columns = [...new Set(metrics.flatMap((metric) => metric.column ?? []))];
  return (
    <>
      {fileRows !== null && (
        <p>{fileRows === 1 ? "This file has 1 row." : `This file has ${fileRows.toLocaleString()} rows.`}</p>
      )}
      {typeof rowCount === "number" && (
        <p>
          {rowCount === 1
            ? "This file has 1 row, from the file's metadata."
            : `This file has ${rowCount.toLocaleString()} rows, from the file's metadata.`}
        </p>
      )}
      {typeof duplicates === "number" && (
        <p id={evidenceId(null, "duplicate_rows")} tabIndex={-1}>
          {duplicateRowsNote(duplicates)}
        </p>
      )}
      {typeof skippedLines === "number" && <p>{skippedLinesNote(skippedLines)}</p>}
      {typeof extraColumns === "number" && <p>{extraColumnsNote(extraColumns)}</p>}
      {/* AC-36 */}
      {!run.current_settings && <p>This profile used earlier settings, so its findings aren't current.</p>}
      {/* AC-37: `null` when the file can't be looked at, which the source already says. */}
      {results.data.file_changed && (
        <p>This file has changed since this profile ran, so these results may be out of date.</p>
      )}
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
                labels={labels}
                buttonId={values.buttonId(column)}
                onShowValues={() => values.show({ column })}
              />
            ))}
          </tbody>
        </table>
      </div>
      {values.section}
    </>
  );
}

type DatabaseObject = NonNullable<ProfileRunDetail["objects"]>[number];

// How a database run read an object, with the rows it read (DRIVN-45 AC-18, AC-24).
const READ_AS: Record<NonNullable<ProfileRunDetail["objects"]>[number]["method"], (rows: string) => string> = {
  page_sample: (rows) => `about ${rows} rows, sampled by page`,
  whole: (rows) => `the whole object, ${rows} rows`,
  first_rows: (rows) => `the first ${rows} rows returned, not random`,
  view_first_rows: (rows) => `the first ${rows} rows the view returned, not random`,
  full: (rows) => `every row, ${rows} rows`,
};

/** How a database run read an object, or, for its masked patterns on a full profile, the fast
 * reading they're from. */
export function objectReading(
  detail: ProfileRunDetail,
  found: NonNullable<ProfileRunDetail["objects"]>[number],
  patterns = false,
): string {
  if (patterns && found.patterns_from) {
    return READ_AS[found.patterns_from.method](shown(found.patterns_from.rows));
  }
  const rows = detail.metrics.find(
    (metric) => metric.object === found.object && metric.column === null && metric.metric === "rows_read",
  )?.value;
  return READ_AS[found.method](shown(rows));
}

function outOfDateNote(objects: string[]): string {
  const named = objects.join(", ");
  return objects.length === 1
    ? `${named} has changed or is gone since this run, so its results are out of date and this run's findings aren't current. Profile again to see it as it is now.`
    : `${named} have changed or are gone since this run, so their results are out of date and this run's findings aren't current. Profile again to see them as they are now.`;
}

/** A database run's results: one table per object, with how its rows were read, and each column's
 * values on request (DRIVN-56). No values that don't fit, which a typed column can't hold. */
function DatabaseResults({ run }: { run: ProfileRun }) {
  const results = useQuery({ queryKey: ["profile-run", run.id], queryFn: () => getProfileRun(run.id) });
  const values = useValuesOnRequest(run);
  if (results.isError) {
    return <p role="alert">DrivnX couldn't load these results. Start DrivnX again.</p>;
  }
  if (!results.data) {
    return null;
  }
  const { metrics, objects = [] } = results.data;
  // A full profile stopped these at their time limit, so they have no results (AC-20).
  const stopped = objects.filter((found) => found.stopped_at_time_limit).map((found) => found.object);
  // A refresh has since found these gone or with other columns (DRIVN-45 AC-32).
  const outOfDate = objects.filter((found) => found.out_of_date).map((found) => found.object);
  return (
    <>
      <p>
        Run {run.id}, finished {new Date(run.ended_at ?? run.started_at).toLocaleString()}
      </p>
      {/* DRIVN-45 AC-32 */}
      <p>
        These results describe each object as it was when this run read it, on{" "}
        {new Date(run.started_at).toLocaleString()}.
      </p>
      {outOfDate.length > 0 && <p>{outOfDateNote(outOfDate)}</p>}
      {stopped.length > 0 && (
        <p>No results for these objects, stopped at the time limit: {stopped.join(", ")}.</p>
      )}
      {objects.map((found) => {
        const { object, method, stopped_at_time_limit, patterns_from, out_of_date } = found;
        if (stopped_at_time_limit) {
          return <p key={object}>{object}: stopped at the time limit, no results</p>;
        }
        const own = metrics.filter((metric) => metric.object === object);
        const columns = [...new Set(own.flatMap((metric) => metric.column ?? []))];
        // Only a full profile counts them (DRIVN-45 AC-26).
        const duplicates = own.find((metric) => metric.column === null && metric.metric === "duplicate_rows")?.value;
        // A full profile's masked patterns are its fast reading's (DRIVN-45, the Assumptions).
        const patternsReadAs = patterns_from ? objectReading(results.data, found, true) : undefined;
        return (
          <div className="table-scroll" key={object}>
            <table className="column-table">
              <caption>
                {object}: {objectReading(results.data, found)}
                {out_of_date && " (out of date)"}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Column</th>
                  <th scope="col">Precision</th>
                  <th scope="col">Missing</th>
                  <th scope="col">Distinct</th>
                  <th scope="col">Shortest</th>
                  <th scope="col">Longest</th>
                  <th scope="col">Average</th>
                  <th scope="col">Patterns</th>
                  <th scope="col">Values</th>
                </tr>
              </thead>
              <tbody>
                {columns.map((column) => {
                  const ofColumn = own.filter((metric) => metric.column === column);
                  const value = (name: string) => ofColumn.find((metric) => metric.metric === name)?.value;
                  const patterns = value("masked_patterns");
                  const patternsScope = ofColumn.find((metric) => metric.metric === "masked_patterns")?.scope;
                  const fromSample = method === "full" && patternsScope === "sample";
                  return (
                    <tr key={column}>
                      <th scope="row">{column}</th>
                      <td>{PRECISION_LABELS[ofColumn[0].precision]}</td>
                      <td id={evidenceId(column, "missing_count", object)} tabIndex={-1}>
                        {withPercent(value("missing_count"), value("missing_percent"))}
                      </td>
                      <td id={evidenceId(column, "distinct_count", object)} tabIndex={-1}>
                        {withPercent(value("distinct_count"), value("distinct_percent"))}
                      </td>
                      <td>{shown(value("shortest_length"))}</td>
                      <td>{shown(value("longest_length"))}</td>
                      <td>{shown(value("average_length"))}</td>
                      <td id={evidenceId(column, "masked_patterns", object)} tabIndex={-1}>
                        {Array.isArray(patterns) ? (
                          <Patterns
                            patterns={patterns}
                            measuredOn={fromSample ? patternsReadAs : undefined}
                          />
                        ) : (
                          NOT_SAVED
                        )}
                      </td>
                      <td>
                        <button
                          type="button"
                          id={values.buttonId(column, found)}
                          aria-label={`Show values of ${column} in ${object}`}
                          onClick={() => values.show({ column, of: found })}
                        >
                          Show values
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {typeof duplicates === "number" && (
              <p id={evidenceId(null, "duplicate_rows", object)} tabIndex={-1}>
                {duplicateRowsNote(duplicates)}
              </p>
            )}
          </div>
        );
      })}
      {values.section}
    </>
  );
}

export function ProfileTab({
  source,
  openedRunId,
  onOpenRun,
}: {
  source: Source;
  openedRunId: string | null;
  onOpenRun: (runId: string | null) => void;
}) {
  const headingId = useId();
  const queryClient = useQueryClient();
  const runs = useProfileRuns();
  // A database's full profile stops each object at this many minutes (DRIVN-45 AC-20).
  const [minutes, setMinutes] = useState("10");
  // Said once a full profile is asked for with minutes it can't take, until they're put right.
  const [triedMinutes, setTriedMinutes] = useState(false);
  const minutesInvalid = triedMinutes && timeLimit(minutes) === null;
  const minutesId = useId();
  const minutesErrorId = useId();
  const minutesField = useRef<HTMLInputElement>(null);
  const start = useMutation({
    mutationFn: ({
      mode,
      writeAccess,
      timeLimitMinutes,
    }: {
      mode: ProfileRun["mode"];
      writeAccess?: WriteAccess;
      timeLimitMinutes?: number;
    }) => startProfile(source.id, mode, writeAccess, timeLimitMinutes),
    // The new run's results are what the user asked for, once it completes.
    onSuccess: () => onOpenRun(null),
    // Also after a refusal, which can come from a run this page hasn't heard of.
    onSettled: () => queryClient.invalidateQueries({ queryKey: PROFILE_RUNS_QUERY }),
  });
  const startFull = () => {
    const limit = timeLimit(minutes);
    setTriedMinutes(true);
    if (limit === null) {
      minutesField.current?.focus();
      return;
    }
    start.mutate({ mode: "full", timeLimitMinutes: limit });
  };
  const cancel = useMutation({
    mutationFn: cancelProfile,
    onSettled: () => queryClient.invalidateQueries({ queryKey: PROFILE_RUNS_QUERY }),
  });
  const { reset: forgetRefusal } = start;
  // Where focus goes back to once a write warning is answered: the button that started it.
  const fastProfile = useRef<HTMLButtonElement>(null);
  const fullProfile = useRef<HTMLButtonElement>(null);
  const backToStart = () =>
    (start.variables?.mode === "full" ? fullProfile : fastProfile).current?.focus();
  // A database's write access the user is asked to confirm before its run starts (AC-16).
  const confirming = start.error instanceof WriteAccessError ? start.error.writeAccess : undefined;
  const running = runs.data?.find((run) => run.status === "running");
  const anyRunning = running !== undefined;
  // A refusal because a run is in progress stops being true when that run ends. Only then: a start
  // still waiting for its answer, or a write warning the user hasn't answered, is kept (AC-16).
  const keepRefusal = start.isPending || start.error instanceof WriteAccessError;
  useEffect(() => {
    if (!anyRunning && !keepRefusal) {
      forgetRefusal();
    }
    // Not on `keepRefusal`'s changes: any other refusal stays until the next run ends.
  }, [anyRunning, forgetRefusal]);
  const mine = runsOfSource(runs.data, source);
  const ofThisFile = runs.data?.filter((run) => run.source_id === source.id) ?? [];
  const completed = shownRun(runs.data, source, openedRunId);
  const showingTheNewest = completed !== undefined && completed.id === newestCompleted(runs.data, source)?.id;
  const didNotComplete = mine[0] && ["failed", "cancelled", "interrupted"].includes(mine[0].status);

  return (
    <section className="profile-tab" aria-labelledby={headingId}>
      <h2 id={headingId}>Profile</h2>
      <p>
        <button type="button" ref={fastProfile} onClick={() => start.mutate({ mode: "fast" })}>
          Fast profile
        </button>{" "}
        {source.kind === "postgres" ? (
          <>
            <button type="button" ref={fullProfile} onClick={startFull}>
              Full profile
            </button>{" "}
            <label htmlFor={minutesId}>Time limit per object, in minutes</label>{" "}
            <input
              id={minutesId}
              ref={minutesField}
              type="number"
              min={1}
              max={120}
              step={1}
              value={minutes}
              aria-invalid={minutesInvalid}
              aria-describedby={minutesInvalid ? minutesErrorId : undefined}
              onChange={(event) => setMinutes(event.target.value)}
            />
            {minutesInvalid && (
              <span id={minutesErrorId} role="alert">
                {" "}
                {MINUTES_NEEDED}
              </span>
            )}
          </>
        ) : (
          <button type="button" onClick={() => start.mutate({ mode: "full" })}>
            Full profile
          </button>
        )}
      </p>
      {confirming ? (
        <WriteAccessConfirmation
          access={confirming}
          onProfile={() => {
            // The same start, everything it asked for, with the access the user was shown.
            start.mutate({ mode: "fast", ...start.variables, writeAccess: confirming });
            backToStart();
          }}
          onCancel={() => {
            backToStart();
            start.reset();
          }}
        />
      ) : (
        start.isError && <p role="alert">{startFailure(start.error)}</p>
      )}
      {mine[0]?.read_only && (
        // No warning was needed: say why (AC-16). Of the run, as the selection can have changed since.
        <p>The account this run used could only read the objects it profiled.</p>
      )}
      {running && (
        <p role="status">
          {running.source_id === source.id
            ? running.current_object
              ? `A profile is running on ${running.current_object}. Results appear here when it finishes.`
              : "A profile is running. Results appear here when it finishes."
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
      {/* AC-38: startup marks a run that was in progress. */}
      {mine[0]?.status === "interrupted" && <p>This profile was interrupted when DrivnX stopped.</p>}
      {didNotComplete && showingTheNewest && <p>The results below are from the run before.</p>}
      {completed ? (
        source.kind === "postgres" ? (
          <DatabaseResults key={completed.id} run={completed} />
        ) : (
          <Results key={completed.id} run={completed} parquet={source.kind === "parquet"} />
        )
      ) : (
        runs.isSuccess && mine.length === 0 && <p>No profile has been run with these settings yet.</p>
      )}
      {ofThisFile.length > 0 && (
        <RunsList
          runs={ofThisFile}
          shown={completed}
          onOpen={onOpenRun}
          of={source.kind === "postgres" ? "database" : "file"}
        />
      )}
    </section>
  );
}
