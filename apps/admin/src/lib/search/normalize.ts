import type { QueryInfo } from "./types";

export const MAX_QUERY_CHARS = 64;
const MAX_RAW_UNITS = 256;
const KAMPALA_OFFSET_MS = 3 * 3600_000;

export type Normalized =
  { ok: true; query: QueryInfo } | { ok: false; reason: "invalid" | "too_short" | "too_long" };

function dayRange(y: number, m: number, d: number): { from: string; to: string } | null {
  const t = Date.UTC(y, m - 1, d);
  const chk = new Date(t);
  if (chk.getUTCFullYear() !== y || chk.getUTCMonth() !== m - 1 || chk.getUTCDate() !== d)
    return null;
  const from = t - KAMPALA_OFFSET_MS;
  return { from: new Date(from).toISOString(), to: new Date(from + 24 * 3600_000).toISOString() };
}

function parseDate(s: string) {
  let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (m) return dayRange(+m[1], +m[2], +m[3]);
  m = /^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/.exec(s);
  if (m) return dayRange(+m[3], +m[2], +m[1]);
  return null;
}

function phoneFragment(s: string): string | null {
  if (!/^\+?[\d\s().-]+$/.test(s)) return null;
  let digits = s.replace(/\D/g, "");
  if (digits.length < 3) return null;
  if (digits.startsWith("00")) digits = digits.slice(2);
  else if (digits.startsWith("0")) digits = digits.slice(1);
  return digits.length >= 3 ? digits : null;
}

/**
 * Turns whatever the browser sent into a safe query. Nothing here interprets the text: it is only
 * ever passed on as a value. Control, zero-width and bidi-override characters are removed so a
 * result cannot be visually spoofed; long input is refused, not truncated.
 */
export function normalizeQuery(raw: unknown): Normalized {
  if (typeof raw !== "string") return { ok: false, reason: "invalid" };
  if (raw.length > MAX_RAW_UNITS) return { ok: false, reason: "too_long" };
  const text = raw
    .normalize("NFKC")
    .replace(/\s+/gu, " ")
    .replace(/[\p{Cc}\p{Cf}]/gu, "")
    .trim();
  const chars = [...text].length;
  if (chars > MAX_QUERY_CHARS) return { ok: false, reason: "too_long" };
  if (chars < (/\p{Nd}/u.test(text) ? 3 : 2)) return { ok: false, reason: "too_short" };
  const dateRange = parseDate(text);
  const frag = dateRange ? null : phoneFragment(text);
  const kind = dateRange
    ? "date"
    : frag
      ? "phone"
      : /^[A-Za-z0-9._/-]{6,}$/.test(text) && /\d/.test(text) && /[A-Za-z]/.test(text)
        ? "reference"
        : "text";
  return { ok: true, query: { text, kind, phoneFragment: frag, dateRange } };
}
