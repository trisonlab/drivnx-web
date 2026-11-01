import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { establishSession } from "./session";

// Before rendering, so the launch code leaves the address bar first and is only ever sent once.
const signedIn = await establishSession();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App signedIn={signedIn} />
  </StrictMode>,
);
