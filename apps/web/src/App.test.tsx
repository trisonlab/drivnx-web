import { render, screen } from "@testing-library/react";
import { App } from "./App";

test("shows the product name", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "Conflux" })).toBeInTheDocument();
});
