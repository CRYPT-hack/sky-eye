import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";
import { ApiProvider } from "../apiContext";
import { makeClient } from "../live/fakes";
import TokenPrompt from "./TokenPrompt";

beforeEach(() => window.localStorage.clear());

function setup() {
  return render(
    <ApiProvider client={makeClient()} initialToken={null}>
      <TokenPrompt />
    </ApiProvider>,
  );
}

test("is a labelled dialog with a labelled password input focused", () => {
  setup();
  expect(screen.getByRole("dialog", { name: "Access token required" })).toBeInTheDocument();
  const input = screen.getByLabelText("Access token");
  expect(input).toHaveAttribute("type", "password");
  expect(input).toHaveFocus();
  expect(screen.getByText(/SKYEYE_API_TOKEN in the engine's \.env file/)).toBeInTheDocument();
});

test("save is disabled until a token is typed, and trims it", async () => {
  const user = userEvent.setup();
  setup();
  const save = screen.getByRole("button", { name: "Save" });
  expect(save).toBeDisabled();
  await user.type(screen.getByLabelText("Access token"), "   ");
  expect(save).toBeDisabled();
  await user.type(screen.getByLabelText("Access token"), " secret ");
  expect(save).toBeEnabled();
  await user.click(save);
  expect(window.localStorage.getItem("skyeye.token")).toBe("secret");
});
