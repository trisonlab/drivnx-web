import { useId } from "react";
import type { Source } from "./api";
import { delimiterName, fileSize } from "./sourceFormat";

export function SourceDetails({ source }: { source: Source }) {
  const headingId = useId();

  return (
    <section className="source-details" aria-labelledby={headingId}>
      <h2 id={headingId}>{source.name}</h2>
      <p className="scope-label">Metadata</p>
      {!source.available && (
        <p role="status">
          Conflux can't find this file. It may have moved or been deleted. What is shown here is from
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
        <dt>Delimiter</dt>
        <dd>{delimiterName(source.delimiter)}</dd>
        <dt>Encoding</dt>
        <dd>{source.encoding}</dd>
        <dt>Header</dt>
        <dd>
          {source.has_header
            ? "A row from the file was used as column names"
            : "No header row found. Column names are generated."}
        </dd>
      </dl>
      <div className="table-scroll">
        <table className="column-table">
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Detected type</th>
              <th scope="col">Original type</th>
            </tr>
          </thead>
          <tbody>
            {source.columns.map((column) => (
              <tr key={column.name}>
                <td>{column.name}</td>
                <td>{column.normalised_type}</td>
                <td>{column.physical_type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
