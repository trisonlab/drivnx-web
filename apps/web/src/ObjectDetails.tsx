import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import { type DeclaredKey, getObject, ObjectGoneError, type ObjectKind } from "./api";

const VIEWS: Partial<Record<ObjectKind, string>> = { view: "view", materialized_view: "materialized view" };

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

/** An object's columns and declared keys, from the workspace: no row is read (AC-09). */
export function ObjectDetails({ sourceId, objectId }: { sourceId: string; objectId: string }) {
  const headingId = useId();
  const found = useQuery({
    queryKey: ["object", sourceId, objectId],
    queryFn: () => getObject(sourceId, objectId),
  });

  if (found.isError) {
    return (
      <p role="alert">
        {found.error instanceof ObjectGoneError
          ? "This object is no longer listed. Refresh the list."
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
    </section>
  );
}
