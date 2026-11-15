import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";
import {
  type DatabaseObject,
  getObjects,
  type ObjectKind,
  type ObjectsList,
  type PostgresSource,
  refreshObjects,
  saveSelection,
} from "./api";
import { ObjectDetails } from "./ObjectDetails";
import { failureMessage } from "./PostgresForm";

const KINDS: Record<ObjectKind, string> = {
  table: "table",
  partitioned_table: "partitioned table",
  view: "view",
  materialized_view: "materialized view",
};

function estimate(rows: number | null): string {
  return rows === null ? "no row estimate" : `about ${rows.toLocaleString()} rows (estimate)`;
}

/** Why an object can't be chosen, or null when it can (AC-10). */
function unchoosable(found: DatabaseObject): string | null {
  if (!found.readable) {
    return "No read access";
  }
  return found.populated ? null : "Not populated: refresh the materialized view in PostgreSQL first";
}

type ItemProps = {
  found: DatabaseObject;
  checked: boolean;
  onChange: (checked: boolean) => void;
  onOpen: () => void;
};

function ObjectItem({ found, checked, onChange, onOpen }: ItemProps) {
  const nameId = useId();
  const reason = unchoosable(found);
  return (
    <li aria-labelledby={nameId}>
      <input
        type="checkbox"
        aria-label={`Choose ${found.name}`}
        checked={checked}
        disabled={reason !== null}
        onChange={(event) => onChange(event.target.checked)}
      />{" "}
      <button type="button" id={nameId} className="object-name" onClick={onOpen}>
        {found.name}
      </button>{" "}
      · {KINDS[found.kind]} · {estimate(found.estimated_rows)}
      {reason !== null && <span className="unavailable"> · {reason}</span>}
    </li>
  );
}

function chosenIds(listed: ObjectsList): Set<string> {
  return new Set(listed.schemas.flatMap((schema) => schema.objects.filter((found) => found.chosen).map(({ id }) => id)));
}

type SelectionProps = {
  sourceId: string;
  listed: ObjectsList;
  onSaved: (listed: ObjectsList) => void;
  onOpen: (objectId: string) => void;
};

/** What the user chooses, starting from what is saved (AC-11). */
function Selection({ sourceId, listed, onSaved, onOpen }: SelectionProps) {
  const [chosen, setChosen] = useState(() => chosenIds(listed));
  const save = useMutation({
    // In the list's order, which is the order the user sees.
    mutationFn: () =>
      saveSelection(
        sourceId,
        listed.schemas.flatMap((schema) => schema.objects.filter(({ id }) => chosen.has(id)).map(({ id }) => id)),
      ),
    onSuccess: onSaved,
  });

  function choose(ids: string[], checked: boolean) {
    const next = new Set(chosen);
    for (const id of ids) {
      if (checked) {
        next.add(id);
      } else {
        next.delete(id);
      }
    }
    setChosen(next);
    save.reset();
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate();
  }

  return (
    <form aria-label="Choose what to profile" onSubmit={submit}>
      {listed.schemas.map((schema) => {
        // A whole schema is its objects that can be chosen, as they are now: not a rule for later.
        const choosable = schema.objects.filter((found) => unchoosable(found) === null).map(({ id }) => id);
        return (
          <fieldset key={schema.name}>
            <legend>{schema.name}</legend>
            {choosable.length > 0 && (
              <label>
                <input
                  type="checkbox"
                  checked={choosable.every((id) => chosen.has(id))}
                  onChange={(event) => choose(choosable, event.target.checked)}
                />{" "}
                Choose all of {schema.name}
              </label>
            )}
            {schema.objects.length === 0 ? (
              <p className="unavailable">No tables or views</p>
            ) : (
              <ul className="object-list">
                {schema.objects.map((found) => (
                  <ObjectItem
                    key={found.id}
                    found={found}
                    checked={chosen.has(found.id)}
                    onChange={(checked) => choose([found.id], checked)}
                    onOpen={() => onOpen(found.id)}
                  />
                ))}
              </ul>
            )}
          </fieldset>
        );
      })}
      {save.isError && <p role="alert">{failureMessage(save.error)}</p>}
      {save.isSuccess && <p role="status">Selection saved.</p>}
      <button type="submit" disabled={save.isPending}>
        Save selection
      </button>
    </form>
  );
}

/** The database's schemas and objects, from the workspace: only listing again reads the database. */
export function DatabaseObjects({ source }: { source: PostgresSource }) {
  const headingId = useId();
  const queryClient = useQueryClient();
  const queryKey = ["objects", source.id];
  const objects = useQuery({ queryKey, queryFn: () => getObjects(source.id) });
  const [openedId, setOpenedId] = useState<string | null>(null);
  const refresh = useMutation({
    mutationFn: () => refreshObjects(source.id),
    onSuccess: (listed: ObjectsList) => {
      queryClient.setQueryData(queryKey, listed);
      // Its columns may have changed, and with them its id.
      setOpenedId(null);
    },
  });

  return (
    <section className="database-objects" aria-labelledby={headingId}>
      <h3 id={headingId}>Schemas and tables</h3>
      {objects.isError && (
        <p role="alert">DrivnX couldn't load this database's schemas and tables. Start DrivnX again.</p>
      )}
      {objects.data?.discovered_at === null && (
        <>
          <p>DrivnX hasn't listed this database's schemas and tables yet.</p>
          <button type="button" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
            List schemas and tables
          </button>
        </>
      )}
      {objects.data?.discovered_at && (
        <p>
          Listed {new Date(objects.data.discovered_at).toLocaleString()}{" "}
          <button type="button" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
            Refresh
          </button>
        </p>
      )}
      {refresh.isError && <p role="alert">{failureMessage(refresh.error)}</p>}
      {/* A new listing starts again from what is saved: its objects may have changed. */}
      {objects.data?.discovered_at && (
        <Selection
          key={objects.data.discovered_at}
          sourceId={source.id}
          listed={objects.data}
          onSaved={(listed) => queryClient.setQueryData(queryKey, listed)}
          onOpen={setOpenedId}
        />
      )}
      {openedId !== null && <ObjectDetails key={openedId} sourceId={source.id} objectId={openedId} />}
    </section>
  );
}
