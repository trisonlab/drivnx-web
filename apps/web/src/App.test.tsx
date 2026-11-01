import { render, screen } from "@testing-library/react";
import { App } from "./App";

test("shows the product name when signed in", () => {
  render(<App signedIn />);
  expect(screen.getByRole("heading", { name: "Conflux" })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("tells a signed-out user how to get back in", () => {
  render(<App signedIn={false} />);
  expect(screen.getByRole("alert")).toHaveTextContent(/start conflux again/i);
});
