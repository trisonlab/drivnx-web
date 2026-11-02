import type { AiMode, Workspace } from "./api";

const NOT_AVAILABLE = "Not available yet";
const TABS = ["Overview", "Profile", "Dictionary", "Contract"];
const AI_MODE_LABELS: Record<AiMode, string> = { off: "Off", external: "External", local: "Local" };
const PRIVACY_STATE: Record<AiMode, string> = {
  off: "External AI: Off",
  external: "External AI: On",
  local: "Local AI: On",
};

export function Shell({ workspace }: { workspace: Workspace }) {
  return (
    <div className="shell">
      <header className="shell-header">
        <h1>{workspace.name}</h1>
        <p>Profile status: {NOT_AVAILABLE}</p>
        <p>AI mode: {AI_MODE_LABELS[workspace.ai_mode]}</p>
        <p role="status" className="privacy">
          Processing: Local · {PRIVACY_STATE[workspace.ai_mode]}
        </p>
        <button type="button" disabled>
          Export <span className="unavailable">({NOT_AVAILABLE})</span>
        </button>
      </header>
      <nav className="shell-explorer" aria-label="Data Explorer">
        <h2>Data Explorer</h2>
        <p>{workspace.name}</p>
      </nav>
      <main className="shell-centre">
        <div role="tablist" aria-label="Understanding Workspace">
          {TABS.map((tab) => (
            <button type="button" role="tab" aria-selected="false" disabled key={tab}>
              {tab} <span className="unavailable">{NOT_AVAILABLE}</span>
            </button>
          ))}
        </div>
      </main>
      <aside className="shell-review" aria-label="Intelligence & Review">
        <h2>Intelligence &amp; Review</h2>
        <p className="unavailable">{NOT_AVAILABLE}</p>
      </aside>
    </div>
  );
}
