import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useId, useRef, useState } from "react";
import {
  type ConnectionChoice,
  ConnectionRefusedError,
  type PostgresSource,
  type ServerFacts,
  saveConnection,
  type TlsMode,
  testConnection,
} from "./api";
import { cleanPastedPath } from "./sourceFormat";

type Fields = {
  host: string;
  port: string;
  database: string;
  user: string;
  password: string;
  tls_mode: TlsMode;
  ca_path: string;
};

// AC-01's defaults are the form's: the API takes every field as given.
const EMPTY: Fields = {
  host: "",
  port: "5432",
  database: "",
  user: "",
  password: "",
  tls_mode: "prefer",
  ca_path: "",
};

function choiceOf({ port, ca_path, ...fields }: Fields): ConnectionChoice {
  const choice = { ...fields, port: Number(port) };
  return fields.tls_mode === "verify-full" ? { ...choice, ca_path: cleanPastedPath(ca_path) } : choice;
}

export function serverFacts(facts: ServerFacts): string {
  return `Connected to PostgreSQL ${facts.server_version}. ${
    facts.encrypted ? "The connection is encrypted." : "The connection isn't encrypted."
  }`;
}

export function failureMessage(error: Error): string {
  return error instanceof ConnectionRefusedError
    ? error.message
    : "DrivnX couldn't try this connection. Start DrivnX again.";
}

/** Each detail in its own field, so there's no connection string or URL to type (AC-01). */
export function PostgresForm({ onSaved }: { onSaved: (source: PostgresSource) => void }) {
  const id = useId();
  const [fields, setFields] = useState<Fields>(EMPTY);
  const form = useRef<HTMLFormElement>(null);
  const test = useMutation({ mutationFn: testConnection });
  const save = useMutation({ mutationFn: saveConnection, onSuccess: onSaved });
  const busy = test.isPending || save.isPending;
  // Only the last attempt's answer: starting one clears the other's.
  const failure = test.error ?? save.error;

  function startTest() {
    if (form.current?.reportValidity()) {
      save.reset();
      test.mutate(choiceOf(fields));
    }
  }

  function change(name: keyof Fields, value: string) {
    // A test's answer was for the details as they were.
    test.reset();
    setFields((current) => ({ ...current, [name]: value }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    test.reset();
    save.mutate(choiceOf(fields));
  }

  return (
    <form ref={form} className="connect-form" aria-label="PostgreSQL database" onSubmit={submit}>
      <label htmlFor={`${id}-host`}>Host</label>
      <input
        id={`${id}-host`}
        type="text"
        value={fields.host}
        required
        spellCheck={false}
        autoComplete="off"
        onChange={(event) => change("host", event.target.value)}
      />
      <label htmlFor={`${id}-port`}>Port</label>
      <input
        id={`${id}-port`}
        type="number"
        min={1}
        max={65535}
        value={fields.port}
        required
        onChange={(event) => change("port", event.target.value)}
      />
      <label htmlFor={`${id}-database`}>Database</label>
      <input
        id={`${id}-database`}
        type="text"
        value={fields.database}
        required
        spellCheck={false}
        autoComplete="off"
        onChange={(event) => change("database", event.target.value)}
      />
      <label htmlFor={`${id}-user`}>User</label>
      <input
        id={`${id}-user`}
        type="text"
        value={fields.user}
        required
        spellCheck={false}
        autoComplete="off"
        onChange={(event) => change("user", event.target.value)}
      />
      <label htmlFor={`${id}-password`}>Password</label>
      <input
        id={`${id}-password`}
        type="password"
        value={fields.password}
        required
        autoComplete="off"
        onChange={(event) => change("password", event.target.value)}
      />
      <label htmlFor={`${id}-tls`}>TLS mode</label>
      <select id={`${id}-tls`} value={fields.tls_mode} onChange={(event) => change("tls_mode", event.target.value)}>
        <option value="prefer">prefer</option>
        <option value="require">require</option>
        <option value="verify-full">verify-full</option>
      </select>
      {fields.tls_mode === "verify-full" && (
        <>
          <label htmlFor={`${id}-ca`}>CA certificate file</label>
          <input
            id={`${id}-ca`}
            type="text"
            value={fields.ca_path}
            required
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => change("ca_path", event.target.value)}
          />
        </>
      )}
      {test.isSuccess && <p role="status">{serverFacts(test.data)}</p>}
      {failure && <p role="alert">{failureMessage(failure)}</p>}
      <button type="button" disabled={busy} onClick={startTest}>
        Test connection
      </button>
      <button type="submit" disabled={busy}>
        Save
      </button>
    </form>
  );
}
