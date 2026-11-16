import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";
import { connectStored, givePassword, type PostgresSource, UNTIL_STOP } from "./api";
import { DatabaseObjects } from "./DatabaseObjects";
import { connectQuery, SOURCES_QUERY } from "./DataExplorer";
import { failureMessage, serverFacts } from "./PostgresForm";

/** After a restart, before anything is read from the database (AC-05), or when the server refused
 * the stored password, which `refusal` says (AC-06). */
function PasswordPrompt({ source, refusal }: { source: PostgresSource; refusal: string | null }) {
  const id = useId();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const give = useMutation({
    mutationFn: () => givePassword(source.id, password),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SOURCES_QUERY }),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    give.mutate();
  }

  return (
    <form className="connect-form" aria-label={`Password for ${source.user}`} onSubmit={submit}>
      <p>
        {refusal ?? "DrivnX has restarted."} Enter the password for {source.user} to connect again.
      </p>
      <label htmlFor={`${id}-password`}>Password</label>
      <input
        id={`${id}-password`}
        type="password"
        value={password}
        required
        autoComplete="off"
        onChange={(event) => setPassword(event.target.value)}
      />
      {give.isError && <p role="alert">{failureMessage(give.error)}</p>}
      <button type="submit" disabled={give.isPending}>
        Connect
      </button>
    </form>
  );
}

/** With a password from the credential store, the server is asked on opening the source, so one it
 * now refuses is found then (AC-04, AC-06). A query, so opening it twice asks once. */
function useConnectOnOpen(source: PostgresSource, onFailed: (reason: string) => void) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: connectQuery(source.id),
    queryFn: async () => {
      try {
        return await connectStored(source.id);
      } catch (error) {
        onFailed(failureMessage(error as Error));
        throw error;
      } finally {
        // Not awaited: a refusal is the query's error before the listing asks for the password.
        void queryClient.invalidateQueries({ queryKey: SOURCES_QUERY });
      }
    },
    enabled: !source.password_needed && source.server_version === null,
    retry: false,
  });
}

export function PostgresDetails({ source }: { source: PostgresSource }) {
  const headingId = useId();
  // What the server last said when the password was used: the prompt's reason, after a refusal on
  // opening the source or on listing its tables (AC-06).
  const [failure, setFailure] = useState<string | null>(null);
  const connect = useConnectOnOpen(source, setFailure);

  return (
    <section className="source-details" aria-labelledby={headingId}>
      <h2 id={headingId}>{source.name}</h2>
      <dl className="source-facts">
        <dt>Host</dt>
        <dd>{source.host}</dd>
        <dt>Port</dt>
        <dd>{source.port}</dd>
        <dt>Database</dt>
        <dd>{source.database}</dd>
        <dt>User</dt>
        <dd>{source.user}</dd>
        <dt>TLS mode</dt>
        <dd>{source.tls_mode}</dd>
        {source.ca_path !== null && (
          <>
            <dt>CA certificate file</dt>
            <dd>{source.ca_path}</dd>
          </>
        )}
      </dl>
      {source.password_kept === UNTIL_STOP && (
        <p>DrivnX keeps this password only until it stops, and will ask for it again after a restart.</p>
      )}
      {source.password_needed && (
        <PasswordPrompt
          key={source.id}
          source={source}
          refusal={failure}
        />
      )}
      {/* A server that doesn't answer keeps the password, as it may answer later (AC-06). Once
          connected, a failure on opening is out of date. */}
      {connect.error && !source.password_needed && source.server_version === null && (
        <p role="alert">{failureMessage(connect.error)}</p>
      )}
      {source.server_version !== null && source.encrypted !== null && (
        <p>{serverFacts({ server_version: source.server_version, encrypted: source.encrypted })}</p>
      )}
      {/* After a restart, nothing about the source is asked for until the password is (AC-05). */}
      {!source.password_needed && <DatabaseObjects key={source.id} source={source} onFailed={setFailure} />}
    </section>
  );
}
