import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId } from "react";
import { type DeclaredKey, getObject, getObjectRows, NO_LONGER_LISTED, ObjectGoneError, type ObjectKind } from "./api";
import { SOURCES_QUERY } from "./DataExplorer";
import { failureMessage } from "./PostgresForm";
import { Preview } from "./Preview";

const VIEWS: Partial<Record<ObjectKind, string>> = { view: "view", materialized_view: "materialized view" };

/** The query for one object, or with no object for every object of the source. */
export const objectQuery = (sourceId: string, objectId?: string) =>
  objectId === undefined ? ["object", sourceId] : ["object", sourceId, objectId];

const KEY_KINDS: Record<DeclaredKey["kind"], string> = {
  primary_key: "Primary key",
  unique: "Unique constraint",
  foreign_key: "Foreign key",
};

function describe(key: DeclaredKey): string {
  const columns = key.columns.join(", ");
  const target = key.references
    ? ` → ${key.references.schema}.${key.references.object} (${key.references.columns.join(", ")})`
    : "";
  return `${KEY_KINDS[key.kind]} ${key.name}: ${columns}${target}`;
}

/** An object's columns and declared keys, from the workspace (AC-09), and a chosen one's rows (AC-12). */
export function ObjectDetails({
  sourceId,
  objectId,
  onFailed,
}: {
  sourceId: string;
  objectId: string;
  // Why reading its rows failed. If the server refused the password, the source now asks for it (AC-06).
  onFailed: (reason: string) => void;
}) {
  const headingId = useId();
  const queryClient = useQueryClient();
  const found = useQuery({
    queryKey: objectQuery(sourceId, objectId),
    queryFn: () => getObject(sourceId, objectId),
  });

  if (found.isError) {
    return (
      <p role="alert">
        {found.error instanceof ObjectGoneError
          ? NO_LONGER_LISTED
          : "DrivnX couldn't open this object. Start DrivnX again."}
      </p>
    );
  }
  if (!found.data) {
    return null;
  }
  const { data } = found;
  // PostgreSQL records NOT NULL for a table's columns only, never a view's or a materialized view's.
  const view = VIEWS[data.kind];
  const isView = view !== undefined;
  return (
    <section aria-labelledby={headingId}>
      <h4 id={headingId}>
        {data.schema}.{data.name}
      </h4>
      <div className="table-scroll">
        <table className="column-table">
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">PostgreSQL type</th>
              <th scope="col">Type</th>
              <th scope="col">Allows nulls</th>
            </tr>
          </thead>
          <tbody>
            {data.columns.map((column) => (
              <tr key={column.name}>
                <td>{column.name}</td>
                <td>{column.physical_type}</td>
                <td>{column.normalised_type}</td>
                <td>{isView ? "Not recorded" : column.allows_nulls ? "Yes" : "No"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {isView && <p>PostgreSQL doesn't record whether a {view}'s columns allow nulls.</p>}
      {data.keys.length === 0 ? (
        <p>No declared keys.</p>
      ) : (
        <ul>
          {data.keys.map((key) => (
            <li key={key.name}>{describe(key)}</li>
          ))}
        </ul>
      )}
      {/* Only a chosen object's rows are read (AC-11, AC-12). */}
      {data.chosen ? (
        <Preview
          sourceId={sourceId}
          objectId={objectId}
          load={async (offset) => {
            try {
              return await getObjectRows(sourceId, objectId, offset);
            } catch (error) {
              onFailed(failureMessage(error as Error));
              void queryClient.invalidateQueries({ queryKey: SOURCES_QUERY });
              throw error;
            }
          }}
        />
      ) : (
        <p>Choose this object and save the selection to see its rows.</p>
      )}
    </section>
  );
}
