import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { type AiMode, InvalidInputError, saveWorkspace, type Workspace } from "./api";

const AI_MODES: { value: AiMode; label: string; description: string }[] = [
  { value: "off", label: "Off", description: "No AI. Everything works without it." },
  {
    value: "external",
    label: "External",
    description: "An external AI provider, only after you approve what is sent.",
  },
  { value: "local", label: "Local", description: "A local model on this machine." },
];

type Props = {
  location: string;
  onSaved: (workspace: Workspace) => void;
};

export function SetupScreen({ location, onSaved }: Props) {
  const [name, setName] = useState("");
  const [aiMode, setAiMode] = useState<AiMode>("off");
  const save = useMutation({ mutationFn: saveWorkspace, onSuccess: onSaved });

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate({ name, ai_mode: aiMode });
  }

  return (
    <main className="setup">
      <h1>Set up your workspace</h1>
      <p className="setup-location">
        Stored in <code>{location}</code>
      </p>
      <p>
        No credentials are stored in this workspace yet. Passwords and API keys will stay in your
        operating system's credential store.
      </p>
      <form onSubmit={submit}>
        <label htmlFor="workspace-name">Workspace name</label>
        <input
          id="workspace-name"
          type="text"
          value={name}
          required
          maxLength={100}
          onChange={(event) => setName(event.target.value)}
        />
        <fieldset>
          <legend>AI mode</legend>
          {AI_MODES.map((mode) => (
            <div className="choice" key={mode.value}>
              <input
                id={`ai-mode-${mode.value}`}
                type="radio"
                name="ai-mode"
                value={mode.value}
                checked={aiMode === mode.value}
                aria-describedby={`ai-mode-${mode.value}-description`}
                onChange={() => setAiMode(mode.value)}
              />
              <label htmlFor={`ai-mode-${mode.value}`}>{mode.label}</label>
              <p id={`ai-mode-${mode.value}-description`}>{mode.description}</p>
            </div>
          ))}
          <p>AI isn't available yet, so nothing is sent whichever you choose.</p>
        </fieldset>
        {save.isError && (
          <p role="alert">
            {save.error instanceof InvalidInputError
              ? "DrivnX couldn't save this. Check the workspace name and try again."
              : "DrivnX couldn't save this. Start DrivnX again."}
          </p>
        )}
        <button type="submit" disabled={save.isPending}>
          Create workspace
        </button>
      </form>
    </main>
  );
}
