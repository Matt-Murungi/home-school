import { describe, expect, it } from "vitest";
import { normalizeQuery, MAX_QUERY_CHARS } from "@/lib/search/normalize";
import { highlightSegments, maskEmail, maskPhone } from "@/lib/search/text";

const ok = (raw: unknown) => {
  const r = normalizeQuery(raw);
  if (!r.ok) throw new Error(`expected ok, got ${r.reason}`);
  return r.query;
};
const bad = (raw: unknown) => {
  const r = normalizeQuery(raw);
  return r.ok ? "ok" : r.reason;
};

describe("query normalisation", () => {
  it("rejects non-strings", () => {
    for (const v of [undefined, null, 5, {}, [], ["abc"], true]) expect(bad(v)).toBe("invalid");
  });
  it("enforces a minimum length: 2 characters for words, 3 once a digit is present", () => {
    expect(bad("")).toBe("too_short");
    expect(bad("   ")).toBe("too_short");
    expect(bad("a")).toBe("too_short");
    expect(bad("ab")).toBe("ok");
    expect(bad("07")).toBe("too_short");
    expect(bad("077")).toBe("ok");
  });
  it("rejects long input instead of searching for it", () => {
    expect(bad("a".repeat(MAX_QUERY_CHARS))).toBe("ok");
    expect(bad("a".repeat(MAX_QUERY_CHARS + 1))).toBe("too_long");
    expect(bad("a".repeat(100_000))).toBe("too_long");
  });
  it("counts code points, not UTF-16 units", () => {
    expect(bad("😀".repeat(MAX_QUERY_CHARS))).toBe("ok");
    expect(bad("😀".repeat(MAX_QUERY_CHARS + 1))).toBe("too_long");
  });
  it("trims, collapses whitespace and applies NFKC", () => {
    expect(ok("  Amina \t\n  Nakato ").text).toBe("Amina Nakato");
    expect(ok("ＡＢＣ").text).toBe("ABC");
  });
  it("strips control, zero-width and bidi override characters", () => {
    expect(ok("Am\u0000in‮a​").text).toBe("Amina");
    expect(ok("⁦Amina⁩").text).toBe("Amina");
  });
  it("keeps unicode and RTL text intact", () => {
    expect(ok("مريم").text).toBe("مريم");
    expect(ok("Nakató Mugisha").text).toBe("Nakató Mugisha");
    expect(ok("王小明").text).toBe("王小明");
  });
  it("passes special characters through as plain text (no interpretation)", () => {
    for (const s of [
      "'; DROP TABLE x;--",
      "<img src=x onerror=alert(1)>",
      "%_%",
      "(.*)",
      "a\\b",
      "{$ne:1}",
    ]) {
      const q = normalizeQuery(s);
      expect(q.ok).toBe(true);
      if (q.ok) expect(q.query.text).toBe(s.replace(/\s+/g, " ").trim());
    }
  });
  it("partial phone numbers: local, international and punctuated forms give a digit fragment", () => {
    expect(ok("0772123456").phoneFragment).toBe("772123456");
    expect(ok("0772 123").phoneFragment).toBe("772123");
    expect(ok("+256 772 123").phoneFragment).toBe("256772123");
    expect(ok("(0772)-123").phoneFragment).toBe("772123");
    expect(ok("00256772").phoneFragment).toBe("256772");
    expect(ok("772").phoneFragment).toBe("772");
    expect(ok("4567").phoneFragment).toBe("4567");
    expect(ok("0772").kind).toBe("phone");
  });
  it("words and references do not produce a phone fragment", () => {
    expect(ok("Amina").phoneFragment).toBeNull();
    expect(ok("MP240101.1234.A12345").phoneFragment).toBeNull();
    expect(ok("MP240101.1234.A12345").kind).toBe("reference");
    expect(ok("Amina").kind).toBe("text");
  });
  it("dates: ISO and day-first forms become the Africa/Kampala day in UTC", () => {
    const want = { from: "2026-11-24T21:00:00.000Z", to: "2026-11-25T21:00:00.000Z" };
    expect(ok("2026-11-25").dateRange).toEqual(want);
    expect(ok("25/11/2026").dateRange).toEqual(want);
    expect(ok("25-11-2026").dateRange).toEqual(want);
    expect(ok("2026-11-25").kind).toBe("date");
  });
  it("impossible dates are plain text, not a date", () => {
    expect(ok("2026-13-45").dateRange).toBeNull();
    expect(ok("31/02/2026").dateRange).toBeNull();
  });
});

describe("masking", () => {
  it("masks phone numbers keeping the country code and last three digits", () => {
    expect(maskPhone("+256772123456")).toBe("+256•••••456");
    expect(maskPhone("0772123456")).toBe("077•••••456");
    expect(maskPhone("12")).toBe("•••");
    expect(maskPhone("")).toBe("•••");
    expect(maskPhone(undefined)).toBe("");
  });
  it("masks emails keeping the first character and the domain", () => {
    expect(maskEmail("amina@example.com")).toBe("a•••@example.com");
    expect(maskEmail("not-an-email")).toBe("•••");
    expect(maskEmail(undefined)).toBe("");
  });
});

describe("highlighting never produces markup", () => {
  it("splits into text segments, matching case-insensitively", () => {
    expect(highlightSegments("Amina Nakato", "NAK")).toEqual([
      { text: "Amina ", hit: false },
      { text: "Nak", hit: true },
      { text: "ato", hit: false },
    ]);
  });
  it("returns the whole text when there is no match or the query is empty", () => {
    expect(highlightSegments("Amina", "zzz")).toEqual([{ text: "Amina", hit: false }]);
    expect(highlightSegments("Amina", "")).toEqual([{ text: "Amina", hit: false }]);
  });
  it("treats regex metacharacters and markup literally", () => {
    expect(highlightSegments("a.b (x) <b>", ".*")).toEqual([{ text: "a.b (x) <b>", hit: false }]);
    expect(highlightSegments("a<b>c", "<b>")).toEqual([
      { text: "a", hit: false },
      { text: "<b>", hit: true },
      { text: "c", hit: false },
    ]);
  });
  it("highlights every occurrence and handles unicode", () => {
    const segs = highlightSegments("ababab", "ab");
    expect(segs.filter((s) => s.hit)).toHaveLength(3);
    expect(highlightSegments("مريم علي", "علي").some((s) => s.hit)).toBe(true);
  });
  it("the segments always concatenate back to the original text", () => {
    for (const [t, q] of [
      ["İstanbul", "i"],
      ["ǅ x", "ǆ"],
      ["a😀b", "😀"],
      ["", "a"],
    ]) {
      expect(
        highlightSegments(t, q)
          .map((s) => s.text)
          .join(""),
      ).toBe(t);
    }
  });
});
