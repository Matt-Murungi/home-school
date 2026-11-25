import { describe, expect, it } from "vitest";
import {
  formatKampala,
  formatValue,
  kampalaLocalToIso,
  parseInput,
  sameValue,
} from "@/lib/settings/validate";
import { FLOAT, INT, WEIGHTS } from "./helpers/settings-fixtures";

describe("parseInput", () => {
  it.each(["24", " 24 ", "1", "720"])("accepts %j", (t) => {
    expect(parseInput(INT, t)).toEqual({ ok: true, value: Number(t.trim()) });
  });
  it.each(["", "-1", "0", "721", "24.5", "abc", "1e3", "0x10", "٢٤", "24 hours", "9".repeat(20)])(
    "refuses %j",
    (t) => {
      expect(parseInput(INT, t).ok).toBe(false);
    },
  );
  it("a negative window is a validation error with the bounds in the message", () => {
    const r = parseInput(INT, "-5");
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toMatch(/between 1 and 720/);
  });
  it("floats take decimals within bounds only", () => {
    expect(parseInput(FLOAT, "25.5")).toEqual({ ok: true, value: 25.5 });
    expect(parseInput(FLOAT, "4.9").ok).toBe(false);
    expect(parseInput(FLOAT, "NaN").ok).toBe(false);
    expect(parseInput(FLOAT, "Infinity").ok).toBe(false);
  });
  const w = (over: Record<string, string> = {}) => ({
    travel_time: "30",
    tier: "20",
    struggle_fit: "15",
    workload_balance: "15",
    reliability: "15",
    cluster_bonus: "5",
    ...over,
  });
  it("weights that add up to 100 are accepted", () => {
    expect(parseInput(WEIGHTS, w({ tier: "25", travel_time: "25" })).ok).toBe(true);
  });
  it("weights not summing to 100 are blocked, with the sum in the message", () => {
    const r = parseInput(WEIGHTS, w({ tier: "21" }));
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toMatch(/add up to 100 \(now 101\)/);
  });
  it("a weight must be a whole number within its bounds", () => {
    expect(parseInput(WEIGHTS, w({ tier: "-5", travel_time: "45" })).ok).toBe(false);
    expect(parseInput(WEIGHTS, w({ tier: "20.5" })).ok).toBe(false);
    expect(parseInput(WEIGHTS, w({ tier: "" })).ok).toBe(false);
  });
});

describe("helpers", () => {
  it("sameValue compares scalars and maps by content", () => {
    expect(sameValue(1, 1)).toBe(true);
    expect(sameValue(1, 2)).toBe(false);
    expect(sameValue({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(true);
    expect(sameValue({ a: 1 }, { a: 1, b: 2 })).toBe(false);
    expect(sameValue({ a: 1 }, 1)).toBe(false);
  });
  it("formatValue", () => {
    expect(formatValue(48, "hours")).toBe("48 hours");
    expect(formatValue({ a: 1, b: 2 }, "percent")).toBe("a 1, b 2");
  });
  it("Kampala local time is UTC+3 and invalid input is null", () => {
    expect(kampalaLocalToIso("2027-03-02T00:00")).toBe("2027-03-01T21:00:00.000Z");
    expect(kampalaLocalToIso("2027-13-02T00:00")).toBeNull();
    expect(kampalaLocalToIso("tomorrow")).toBeNull();
    expect(formatKampala("2027-03-01T21:00:00.000Z")).toBe("2027-03-02 00:00 (Kampala)");
    expect(formatKampala("junk")).toBe("junk");
  });
});
