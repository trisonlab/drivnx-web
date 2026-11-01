import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { establishSession } from "./session";

const fetchMock = vi.fn();

function respondWith(authenticated: boolean, signInStatus = 204) {
  fetchMock.mockImplementation(async (_url: string, init?: RequestInit) =>
    init?.method === "POST"
      ? new Response(null, { status: signInStatus })
      : Response.json({ authenticated }),
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  window.history.replaceState(null, "", "/");
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("sends the launch code once and removes it from the address bar", async () => {
  // CFX-APP-first-slice/AC-11
  window.history.replaceState(null, "", "/some/path?x=1#launch=the-code");
  respondWith(true);

  const signedIn = await establishSession();

  expect(signedIn).toBe(true);
  expect(window.location.href).not.toContain("the-code");
  expect(window.location.pathname + window.location.search).toBe("/some/path?x=1");
  const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(posts).toHaveLength(1);
  expect(posts[0][0]).toBe("/api/session");
  expect(JSON.parse(posts[0][1].body)).toEqual({ code: "the-code" });
});

test("removes the code before the request starts", async () => {
  window.history.replaceState(null, "", "/#launch=the-code");
  let hrefDuringRequest = "";
  fetchMock.mockImplementation(async () => {
    hrefDuringRequest = window.location.href;
    return Response.json({ authenticated: true });
  });

  await establishSession();

  expect(hrefDuringRequest).not.toContain("the-code");
});

test("keeps nothing in browser storage", async () => {
  // CFX-APP-first-slice/AC-11
  window.history.replaceState(null, "", "/#launch=the-code");
  respondWith(true);

  await establishSession();

  expect(localStorage).toHaveLength(0);
  expect(sessionStorage).toHaveLength(0);
});

test("without a code it only asks whether the session is valid", async () => {
  respondWith(true);

  expect(await establishSession()).toBe(true);
  expect(fetchMock.mock.calls).toEqual([["/api/session"]]);
});

test("a used code still counts as signed in when the cookie is valid", async () => {
  // Opening the same link twice in one browser, or reloading before the address bar updates.
  window.history.replaceState(null, "", "/#launch=used");
  respondWith(true, 401);

  expect(await establishSession()).toBe(true);
});

test("an invalid code without a session is not signed in", async () => {
  window.history.replaceState(null, "", "/#launch=old");
  respondWith(false, 401);

  expect(await establishSession()).toBe(false);
});

test("a network failure is not signed in", async () => {
  fetchMock.mockRejectedValue(new TypeError("failed to fetch"));

  expect(await establishSession()).toBe(false);
});
