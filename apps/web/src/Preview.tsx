import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { type Cell, ConnectionRefusedError, FileUnreadableError, getRows, ObjectGoneError, type RowsPage } from "./api";

const PAGE_ROWS = 50;

function failureMessage(error: Error): string {
  return error instanceof FileUnreadableError ||
    error instanceof ConnectionRefusedError ||
    error instanceof ObjectGoneError
    ? error.message
    : "DrivnX couldn't load these rows. Start DrivnX again.";
}

function skippedLinesNote(lines: number): string {
  return lines === 1
    ? "1 line above this table isn't shown."
    : `${lines} lines above this table aren't shown.`;
}

export function CellText({ cell }: { cell: Cell }) {
  return (
    <>
      {cell.value}
      {cell.cut && <span className="cut-mark"> … cut at 1,000 characters</span>}
    </>
  );
}

/** A file's rows, or with `objectId` and its `load` a chosen database object's (DRIVN-45 AC-12). */
export function Preview({
  sourceId,
  objectId,
  load = (offset) => getRows(sourceId, offset),
}: {
  sourceId: string;
  objectId?: string;
  load?: (offset: number) => Promise<RowsPage>;
}) {
  const headingId = useId();
  const [offset, setOffset] = useState(0);
  const page = useQuery({
    // A file's first page is shared with its settings (SourceSettings.tsx), under this same key.
    queryKey: objectId === undefined ? ["rows", sourceId, offset] : ["rows", sourceId, objectId, offset],
    queryFn: () => load(offset),
    // The rows on screen stay until the next page arrives, and Next stays enabled so keyboard focus
    // isn't lost from it. Paging works from those rows, so pressing again asks for the same page.
    placeholderData: keepPreviousData,
  });
  const shown = page.isError ? undefined : page.data;
  const onScreen = shown?.offset ?? offset;

  return (
    <section className="preview" aria-labelledby={headingId}>
      <h3 id={headingId}>Preview</h3>
      {page.isPending && <p role="status">Loading…</p>}
      {page.isError && (
        <>
          <p role="alert">{failureMessage(page.error)}</p>
          <div>
            <button type="button" onClick={() => void page.refetch()}>
              Try again
            </button>
          </div>
        </>
      )}
      {shown && shown.skipped_lines > 0 && <p>{skippedLinesNote(shown.skipped_lines)}</p>}
      {shown?.rows.length === 0 && <p>No rows to show here.</p>}
      {shown && shown.rows.length > 0 && (
        <>
          <div className="table-scroll">
            <table className="column-table" aria-labelledby={headingId}>
              <thead>
                <tr>
                  {shown.columns.map((column, index) => (
                    <th scope="col" key={index}>
                      <CellText cell={column} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <td key={cellIndex}>
                        <CellText cell={cell} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p role="status">
            Rows {shown.offset + 1} to {shown.offset + shown.rows.length}
            {page.isPlaceholderData && <span> Loading…</span>}
          </p>
        </>
      )}
      <div className="preview-pages">
        <button type="button" disabled={onScreen === 0} onClick={() => setOffset(onScreen - PAGE_ROWS)}>
          Previous
        </button>
        <button type="button" disabled={!shown?.has_next} onClick={() => setOffset(onScreen + PAGE_ROWS)}>
          Next
        </button>
      </div>
    </section>
  );
}
