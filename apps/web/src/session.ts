const SESSION_PATH = "/api/session";

function takeLaunchCode(): string | null {
  const code = /^#launch=(.+)$/.exec(window.location.hash)?.[1] ?? null;
  if (code !== null) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  return code;
}

/**
 * Exchanges the launch code from the URL for the session cookie (ADR 0001), and reports whether the
 * browser ends up signed in. A code that is refused still counts if the cookie is already valid.
 */
export async function establishSession(): Promise<boolean> {
  const code = takeLaunchCode();
  try {
    if (code !== null) {
      await fetch(SESSION_PATH, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
    }
    const status = await fetch(SESSION_PATH);
    const body: { authenticated: boolean } = await status.json();
    return body.authenticated;
  } catch {
    return false;
  }
}
