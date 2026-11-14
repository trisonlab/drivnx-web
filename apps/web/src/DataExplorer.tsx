import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { connectSource, RefusedPathError, type Source } from "./api";
import { PostgresForm } from "./PostgresForm";
import { cleanPastedPath } from "./sourceFormat";

export const SOURCES_QUERY = ["sources"];

function failureMessage(error: Error): string {
  if (error instanceof RefusedPathError) {
    return error.reason ?? "DrivnX couldn't use this path. Check the path and try again.";
  }
  return "DrivnX couldn't connect this file. Start DrivnX again.";
}

type Props = {
  sources: Source[] | undefined;
  loadFailed: boolean;
  chosenId: string | null;
  onChoose: (id: string) => void;
};

export function DataExplorer({ sources, loadFailed, chosenId, onChoose }: Props) {
  const queryClient = useQueryClient();
  const [path, setPath] = useState("");
  const [addingDatabase, setAddingDatabase] = useState(false);
  const connect = useMutation({
    mutationFn: connectSource,
    onSuccess: async (source) => {
      setPath("");
      await queryClient.invalidateQueries({ queryKey: SOURCES_QUERY });
      onChoose(source.id);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    connect.mutate(cleanPastedPath(path));
  }

  return (
    <nav className="shell-explorer" aria-label="Data Explorer">
      <h2>Data Explorer</h2>
      <form className="connect-form" onSubmit={submit}>
        <label htmlFor="csv-path">CSV file path</label>
        <input
          id="csv-path"
          type="text"
          value={path}
          required
          spellCheck={false}
          autoComplete="off"
          onChange={(event) => setPath(event.target.value)}
        />
        {connect.isError && <p role="alert">{failureMessage(connect.error)}</p>}
        <button type="submit" disabled={connect.isPending}>
          Connect
        </button>
      </form>
      <button type="button" aria-expanded={addingDatabase} onClick={() => setAddingDatabase(!addingDatabase)}>
        Add a PostgreSQL database
      </button>
      {addingDatabase && (
        <PostgresForm
          onSaved={async (source) => {
            setAddingDatabase(false);
            await queryClient.invalidateQueries({ queryKey: SOURCES_QUERY });
            onChoose(source.id);
          }}
        />
      )}
      {loadFailed && <p role="alert">DrivnX couldn't load your sources. Start DrivnX again.</p>}
      {sources?.length === 0 && <p className="unavailable">No sources yet.</p>}
      <ul className="source-tree">
        {sources?.map((source) => (
          <li key={source.id}>
            <button
              type="button"
              className="source-link"
              aria-current={source.id === chosenId ? "true" : undefined}
              onClick={() => onChoose(source.id)}
            >
              {source.name}
            </button>
            {source.kind !== "postgres" && (
              <>
                {!source.available && <p className="unavailable">Unavailable</p>}
                <ul className="column-list">
                  {source.columns.map((column) => (
                    <li key={column.name}>{column.name}</li>
                  ))}
                </ul>
              </>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}
