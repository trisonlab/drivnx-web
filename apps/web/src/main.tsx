import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { establishSession } from "./session";
import "./styles.css";

// The API is on this machine, so a failure is real and retrying only delays saying so.
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

// Before rendering, so the launch code leaves the address bar first and is only ever sent once.
const signedIn = await establishSession();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App signedIn={signedIn} />
    </QueryClientProvider>
  </StrictMode>,
);
