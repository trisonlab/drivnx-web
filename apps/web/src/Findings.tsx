import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import { type Finding, getFindings, getProfileRun, type Metric, type ProfileRunDetail, type Source } from "./api";
import { evidenceId, objectReading, shownRun, useProfileRuns } from "./ProfileTab";

function count(value: Metric["value"] | undefined): string {
  return typeof value === "number" ? value.toLocaleString() : "";
}

function values(value: Metric["value"] | undefined, what: string): string {
  return [count(value), what, value === 1 ? "value" : "values"].filter(Boolean).join(" ");
}

function cites(finding: Finding, metric: string): boolean {
  return finding.evidence.some((cited) => cited.metric === metric);
}

/** Whether what was observed comes from the first rows only. A name doesn't, though the missing
 * count a possibly-sensitive finding cites for it may. */
function fromFirstRows(finding: Finding): boolean {
  return finding.evidence.some(
    (cited) => cited.scope === "sample" && (finding.kind !== "possibly_sensitive" || cited.metric === "masked_patterns"),
  );
}

/** What was observed, from the observations a finding cites, as the saved text holds no names. */
function observed(finding: Finding): string {
  const value = (metric: string) => finding.evidence.find((cited) => cited.metric === metric)?.value;
  const column = finding.evidence[0]?.column;
  switch (finding.kind) {
    case "not_fitting": {
      const found = value("not_fitting_count");
      return found === 1
        ? `${column} has 1 value that doesn't fit its type`
        : `${column} has ${count(found)} values that don't fit its type`;
    }
    case "duplicate_rows": {
      const found = value("duplicate_rows");
      return found === 1
        ? "1 row is an exact copy of another row"
        : `${count(found)} rows are exact copies of another row`;
    }
    case "missing_values":
      return `${column} is missing ${values(value("missing_count"), "")} (${value("missing_percent")}%)`;
    case "possible_identifier":
      return `${column} has ${values(value("distinct_count"), "distinct")} (${value("distinct_percent")}%)`;
    case "single_value":
      return `every value in ${column} is the same`;
    case "possibly_sensitive": {
      // It cites the missing count when the column's name matched, and the patterns when they did.
      const named = cites(finding, "missing_count");
      const shaped = cites(finding, "masked_patterns");
      return [
        named && `the column is named ${column}`,
        shaped && `${named ? "it" : column} has a value of this shape among its most common masked patterns`,
      ]
        .filter(Boolean)
        .join(", and ");
    }
    default:
      return "";
  }
}

/** Which rows a finding was observed in, when not every row: a file's first rows, or how a database
 * run read the finding's object, with a full profile's patterns from its fast reading. */
function observedIn(finding: Finding, detail: ProfileRunDetail | undefined): string | undefined {
  if (!fromFirstRows(finding)) {
    return undefined;
  }
  if (!finding.object) {
    return "the first 100,000 rows";
  }
  const found = detail?.objects?.find((read) => read.object === finding.object);
  const patterns = finding.evidence.some((cited) => cited.metric === "masked_patterns" && cited.scope === "sample");
  return detail && found && objectReading(detail, found, patterns);
}

/** The Profile tab shows the same run, so the cited metric is there, with the run's ID. */
function showEvidence(finding: Finding) {
  const cited = finding.evidence[0];
  if (cited) {
    document.getElementById(evidenceId(cited.column, cited.metric, cited.object))?.focus();
  }
}

function FindingItem({ finding, detail }: { finding: Finding; detail: ProfileRunDetail | undefined }) {
  const rows = observedIn(finding, detail);
  const headingId = useId();
  return (
    <article aria-labelledby={headingId}>
      <h4 id={headingId}>
        {finding.interpretation} <span className="trust-state">Inferred</span>
      </h4>
      <p>
        Observed{finding.object && ` in ${finding.object}`}: {observed(finding)}
        {/* AC-24: only when the run didn't read every row, as its metrics say. */}
        {rows && ` in ${rows}`}.
      </p>
      <p>Why it matters: {finding.why_it_matters}</p>
      <p>Recommended action: {finding.recommended_action}</p>
      <button type="button" aria-describedby={headingId} onClick={() => showEvidence(finding)}>
        Show the evidence
      </button>
    </article>
  );
}

export function Findings({ source, openedRunId }: { source: Source | undefined; openedRunId: string | null }) {
  const headingId = useId();
  const runs = useProfileRuns();
  const run = source && shownRun(runs.data, source, openedRunId);
  const findings = useQuery({
    queryKey: ["findings", run?.id],
    queryFn: () => getFindings(run?.id ?? ""),
    enabled: run !== undefined,
  });
  // A database run's findings say how each object was read, which its results hold. The Profile tab
  // asks for the same run, so this is one request.
  const database = source?.kind === "postgres";
  const detail = useQuery({
    queryKey: ["profile-run", run?.id],
    queryFn: () => getProfileRun(run?.id ?? ""),
    enabled: run !== undefined && database,
  });
  const shown = database ? detail.data && findings.data : findings.data;
  return (
    <section aria-labelledby={headingId}>
      <h3 id={headingId}>Findings</h3>
      {/* Only once the runs are known, and with a source to profile, as the centre covers having none. */}
      {source && runs.isSuccess && !run && (
        <p>Profile this {database ? "database" : "file"} to see what it finds.</p>
      )}
      {(runs.isError || findings.isError || detail.isError) && (
        <p role="alert">DrivnX couldn't load the findings. Start DrivnX again.</p>
      )}
      {/* AC-36 */}
      {run && !run.current_settings && (
        <p>These findings are from a profile that used earlier settings, so they aren't current.</p>
      )}
      {shown?.length === 0 && <p>This profile found nothing to point out.</p>}
      {shown?.map((finding) => (
        <FindingItem key={finding.id} finding={finding} detail={detail.data} />
      ))}
      {/* AC-28: whatever was found, as the rules can't find every kind of personal data. */}
      {findings.isSuccess && (
        <p>
          Having no possibly-sensitive flag doesn't mean a column has no personal data. Only column names, and
          values shaped like email addresses, phone numbers or payment card numbers, are checked.
        </p>
      )}
      {run?.mode === "fast" && <p>Duplicate rows are checked only by a full profile.</p>}
    </section>
  );
}
