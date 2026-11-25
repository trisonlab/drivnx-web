import { useId } from "react";
import type { ParquetSource } from "./api";
import { fileSize } from "./sourceFormat";

/** ADR 0003 has no type for these, so a column of one is a string that says what it holds (AC-09). */
function nested(physicalType: string): string | null {
  if (/\[\d*\]$/.test(physicalType)) {
    return "list";
  }
  if (physicalType.startsWith("STRUCT(")) {
    return "struct";
  }
  if (physicalType.startsWith("MAP(")) {
    return "map";
  }
  return null;
}

export function ParquetDetails({ source }: { source: ParquetSource }) {
  const headingId = useId();

  return (
    <section className="source-details" aria-labelledby={headingId}>
      <h2 id={headingId}>{source.name}</h2>
      <p className="scope-label">Metadata</p>
      {!source.available && (
        <p role="status">
          DrivnX can't find this file. It may have moved or been deleted. What is shown here is from
          when it was connected.
        </p>
      )}
      <dl className="source-facts">
        <dt>Path</dt>
        <dd>{source.path}</dd>
        <dt>Size</dt>
        <dd>{fileSize(source.size_bytes)}</dd>
        <dt>Modified</dt>
        <dd>{new Date(source.modified_at).toLocaleString()}</dd>
        <dt>Rows</dt>
        <dd>{source.row_count.toLocaleString()} rows</dd>
        <dt>Row groups</dt>
        <dd>{source.row_group_count.toLocaleString()} row groups</dd>
        {source.created_by !== null && (
          <>
            <dt>Writer</dt>
            <dd>Written by {source.created_by}</dd>
          </>
        )}
      </dl>
      <div className="table-scroll">
        <table className="column-table">
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Type</th>
              <th scope="col">Original type</th>
            </tr>
          </thead>
          <tbody>
            {source.columns.map((column) => {
              const holds = nested(column.physical_type);
              return (
                <tr key={column.name}>
                  <td>{column.name}</td>
                  <td>{holds ? `${column.normalised_type} (${holds})` : column.normalised_type}</td>
                  <td>{column.physical_type}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
