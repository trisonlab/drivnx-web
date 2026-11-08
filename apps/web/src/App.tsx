import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getWorkspace, type Workspace } from "./api";
import { SetupScreen } from "./SetupScreen";
import { Shell } from "./Shell";

const WORKSPACE_QUERY = ["workspace"];

function Message({ text }: { text: string }) {
  return (
    <main className="message">
      <h1>DrivnX</h1>
      <p role="alert">{text}</p>
    </main>
  );
}

function WorkspaceApp() {
  const queryClient = useQueryClient();
  const workspace = useQuery({ queryKey: WORKSPACE_QUERY, queryFn: getWorkspace });

  if (workspace.isPending) {
    return <main className="message" aria-busy="true" />;
  }
  if (workspace.isError) {
    return <Message text="DrivnX couldn't load this workspace. Start DrivnX again." />;
  }
  if (!workspace.data.setup_complete) {
    return (
      <SetupScreen
        location={workspace.data.location}
        onSaved={(saved: Workspace) => queryClient.setQueryData(WORKSPACE_QUERY, saved)}
      />
    );
  }
  return <Shell workspace={workspace.data} />;
}

export function App({ signedIn }: { signedIn: boolean }) {
  if (!signedIn) {
    return (
      <Message text="This browser isn't signed in. Start DrivnX again and use the new link it opens or prints." />
    );
  }
  return <WorkspaceApp />;
}
