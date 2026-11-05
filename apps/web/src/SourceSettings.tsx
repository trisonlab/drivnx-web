import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";
import { FileUnreadableError, getRows, type Source, saveSourceSettings } from "./api";
import { SOURCES_QUERY } from "./DataExplorer";
import { delimiterName } from "./sourceFormat";

const DELIMITERS = [",", ";", "\t", "|"];
const ENCODINGS = ["UTF-8", "UTF-16 LE", "UTF-16 BE", "Latin-1", "Windows-1252"];
const BAD_SKIP = "Enter a whole number from 0 to 1,000.";

type Changes = { hasHeader?: boolean; delimiter?: string; encoding?: string; skipLines?: string };

function linesToSkip(text: string): number | null {
  return /^\d+$/.test(text) && Number(text) <= 1000 ? Number(text) : null;
}

function failureMessage(error: Error): string {
  return error instanceof FileUnreadableError
    ? error.message
    : "Conflux couldn't save these settings. Start Conflux again.";
}

export function SourceSettings({ source }: { source: Source }) {
  const id = useId();
  const queryClient = useQueryClient();
  // Only what the user has changed, so everything else follows the source after a save.
  const [changes, setChanges] = useState<Changes>({});
  const [badSkip, setBadSkip] = useState(false);
  const [open, setOpen] = useState(false);
  const firstPage = useQuery({
    queryKey: ["rows", source.id, 0],
    queryFn: () => getRows(source.id, 0),
    enabled: open && source.available && source.skip_lines === null,
  });
  const save = useMutation({
    mutationFn: (settings: Parameters<typeof saveSourceSettings>[1]) =>
      saveSourceSettings(source.id, settings),
    onSuccess: async () => {
      setChanges({});
      // Removed, not refreshed, so a page read the old way can't show for a moment.
      queryClient.removeQueries({ queryKey: ["rows", source.id] });
      await queryClient.invalidateQueries({ queryKey: SOURCES_QUERY });
    },
  });

  const hasHeader = changes.hasHeader ?? source.has_header;
  const delimiter = changes.delimiter ?? source.delimiter;
  const encoding = changes.encoding ?? source.encoding;
  const skipIsAutomatic = changes.skipLines === undefined && source.skip_lines === null;
  const skipLines = changes.skipLines ?? String(source.skip_lines ?? firstPage.data?.skipped_lines ?? "");

  function change(next: Changes) {
    save.reset();
    setChanges({ ...changes, ...next });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const chosenSkip = changes.skipLines === undefined ? source.skip_lines : linesToSkip(changes.skipLines);
    const invalid = changes.skipLines !== undefined && chosenSkip === null;
    setBadSkip(invalid);
    if (!invalid) {
      save.mutate({ has_header: hasHeader, delimiter, encoding, skip_lines: chosenSkip });
    }
  }

  return (
    <details className="source-settings" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>How this file is read</summary>
      {!open ? null : source.available ? (
        <form onSubmit={submit} noValidate>
          <label className="settings-check">
            <input
              type="checkbox"
              checked={hasHeader}
              onChange={(event) => change({ hasHeader: event.target.checked })}
            />
            The first row holds column names
          </label>
          <label htmlFor={`${id}-delimiter`}>Delimiter</label>
          <select
            id={`${id}-delimiter`}
            value={delimiter}
            onChange={(event) => change({ delimiter: event.target.value })}
          >
            {DELIMITERS.map((choice) => (
              <option key={choice} value={choice}>
                {delimiterName(choice)}
              </option>
            ))}
          </select>
          <label htmlFor={`${id}-encoding`}>Encoding</label>
          <select
            id={`${id}-encoding`}
            value={encoding}
            onChange={(event) => change({ encoding: event.target.value })}
          >
            {ENCODINGS.map((choice) => (
              <option key={choice}>{choice}</option>
            ))}
          </select>
          <label htmlFor={`${id}-skip`}>Lines to skip above the table</label>
          <input
            id={`${id}-skip`}
            type="number"
            min={0}
            max={1000}
            step={1}
            value={skipLines}
            aria-invalid={badSkip}
            aria-describedby={skipIsAutomatic ? `${id}-automatic` : undefined}
            onChange={(event) => {
              setBadSkip(false);
              change({ skipLines: event.target.value });
            }}
          />
          {skipIsAutomatic && <p id={`${id}-automatic`}>Worked out automatically</p>}
          <button type="submit" disabled={save.isPending}>
            Save settings
          </button>
          {badSkip && <p role="alert">{BAD_SKIP}</p>}
          {save.isError && <p role="alert">{failureMessage(save.error)}</p>}
          {save.isSuccess && <p role="status">Settings saved. The columns and preview now use them.</p>}
        </form>
      ) : (
        <p>These settings can't be changed until Conflux can find this file.</p>
      )}
    </details>
  );
}
