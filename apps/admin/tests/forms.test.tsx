import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReasonField } from "@/components/ReasonField";
import { validate, required, minLength, type Schema } from "@/lib/forms";

const schema: Schema = {
  name: [required("Name is required")],
  reason: [
    required("Give a reason for this change"),
    minLength(10, "Reason must be at least 10 characters"),
  ],
};

describe("form validation helpers (server-side)", () => {
  it("returns one error per field", () => {
    const r = validate(schema, { name: "", reason: "short" });
    expect(r.ok).toBe(false);
    if (!r.ok)
      expect(r.errors).toEqual({
        name: "Name is required",
        reason: "Reason must be at least 10 characters",
      });
  });
  it("trims and accepts valid input", () => {
    const r = validate(schema, { name: " Ann ", reason: "Updated per ticket 12" });
    expect(r).toEqual({ ok: true, values: { name: "Ann", reason: "Updated per ticket 12" } });
  });
  it("treats missing fields as empty", () => {
    const r = validate(schema, {});
    expect(r.ok).toBe(false);
  });
});

describe("reason for change field", () => {
  it("renders a required labelled textarea with its error", () => {
    render(<ReasonField error="Give a reason for this change" />);
    const box = screen.getByLabelText(/reason for change/i);
    expect(box).toBeRequired();
    expect(box).toHaveAttribute("name", "reason");
    expect(screen.getByRole("alert")).toHaveTextContent("Give a reason");
  });
});
