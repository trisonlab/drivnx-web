import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";
import { givePassword, type PostgresSource, UNTIL_STOP } from "./api";
import { SOURCES_QUERY } from "./DataExplorer";
import { failureMessage, serverFacts } from "./PostgresForm";

/** After a restart, before anything is read from the database (AC-05). */
function PasswordPrompt({ source }: { source: PostgresSource }) {
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
      <p>DrivnX has restarted. Enter the password for {source.user} to connect again.</p>
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

export function PostgresDetails({ source }: { source: PostgresSource }) {
  const headingId = useId();

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
      {source.password_needed && <PasswordPrompt key={source.id} source={source} />}
      {source.server_version !== null && source.encrypted !== null && (
        <p>{serverFacts({ server_version: source.server_version, encrypted: source.encrypted })}</p>
      )}
    </section>
  );
}
