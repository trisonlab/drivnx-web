import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import { type Finding, getFindings, type Metric, type Source } from "./api";
import { evidenceId, shownRun, useProfileRuns } from "./ProfileTab";

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

/** The Profile tab shows the same run, so the cited metric is there, with the run's ID. */
function showEvidence(finding: Finding) {
  const cited = finding.evidence[0];
  if (cited) {
    document.getElementById(evidenceId(cited.column, cited.metric))?.focus();
  }
}

function FindingItem({ finding }: { finding: Finding }) {
  const headingId = useId();
  return (
    <article aria-labelledby={headingId}>
      <h4 id={headingId}>
        {finding.interpretation} <span className="trust-state">Inferred</span>
      </h4>
      <p>
        Observed: {observed(finding)}
        {/* AC-24: only when the run read just the first rows, as its metrics say. */}
        {fromFirstRows(finding) && " in the first 100,000 rows"}.
      </p>
      <p>Why it matters: {finding.why_it_matters}</p>
      <p>Recommended action: {finding.recommended_action}</p>
      <button type="button" aria-describedby={headingId} onClick={() => showEvidence(finding)}>
        Show the evidence
      </button>
    </article>
  );
}

export function Findings({ source }: { source: Source | undefined }) {
  const headingId = useId();
  const runs = useProfileRuns();
  const run = source && shownRun(runs.data, source);
  const findings = useQuery({
    queryKey: ["findings", run?.id],
    queryFn: () => getFindings(run?.id ?? ""),
    enabled: run !== undefined,
  });
  return (
    <section aria-labelledby={headingId}>
      <h3 id={headingId}>Findings</h3>
      {/* Only once the runs are known, and with a file to profile, as the centre covers having none. */}
      {source && runs.isSuccess && !run && <p>Profile this file to see what it finds.</p>}
      {(runs.isError || findings.isError) && (
        <p role="alert">DrivnX couldn't load the findings. Start DrivnX again.</p>
      )}
      {findings.data?.length === 0 && <p>This profile found nothing to point out.</p>}
      {findings.data?.map((finding) => (
        <FindingItem key={finding.id} finding={finding} />
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
