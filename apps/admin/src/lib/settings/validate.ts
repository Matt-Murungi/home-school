import type { SettingValue, SettingView } from "./types";

/**
 * Browser-side checks that mirror the core service so the form can explain a problem before the
 * request. The server is the authority (it re-validates everything, including cross-setting rules
 * such as grace < cancel limit); nothing here is trusted.
 */
export type Parsed = { ok: true; value: SettingValue } | { ok: false; error: string };

const INT = /^-?\d{1,15}$/;
const FLOAT = /^-?\d{1,15}(\.\d{1,6})?$/;

export const unitLabel = (s: SettingView) => (s.unit ? ` ${s.unit}` : "");

function parseNumber(text: string, kind: "int" | "float", label: string): number | string {
  const t = text.trim();
  if (!(kind === "int" ? INT : FLOAT).test(t))
    return `${label} must be ${kind === "int" ? "a whole number" : "a number"}`;
  return Number(t);
}

/** `input` is the text of the single field, or one text per map field. */
export function parseInput(s: SettingView, input: string | Record<string, string>): Parsed {
  if (s.kind === "map") {
    const fields = s.fields ?? [];
    const text = typeof input === "string" ? {} : input;
    const out: Record<string, number> = {};
    for (const f of fields) {
      const n = parseNumber(text[f.name] ?? "", "int", f.name);
      if (typeof n === "string") return { ok: false, error: n };
      if (n < f.min || n > f.max)
        return { ok: false, error: `${f.name} must be between ${f.min} and ${f.max}` };
      out[f.name] = n;
    }
    const sum = Object.values(out).reduce((a, b) => a + b, 0);
    if (s.sum_to !== undefined && sum !== s.sum_to)
      return { ok: false, error: `The values must add up to ${s.sum_to} (now ${sum})` };
    return { ok: true, value: out };
  }
  const n = parseNumber(typeof input === "string" ? input : "", s.kind, s.key);
  if (typeof n === "string") return { ok: false, error: n };
  if (s.min !== undefined && s.max !== undefined && (n < s.min || n > s.max))
    return { ok: false, error: `Must be between ${s.min} and ${s.max}${unitLabel(s)}` };
  return { ok: true, value: n };
}

export function sameValue(a: SettingValue, b: SettingValue): boolean {
  if (typeof a === "number" || typeof b === "number") return a === b;
  const ka = Object.keys(a).sort();
  const kb = Object.keys(b).sort();
  return ka.length === kb.length && ka.every((k, i) => k === kb[i] && a[k] === b[k]);
}

export function formatValue(v: SettingValue, unit = ""): string {
  const body =
    typeof v === "number"
      ? String(v)
      : Object.entries(v)
          .map(([k, x]) => `${k} ${x}`)
          .join(", ");
  return unit && typeof v === "number" ? `${body} ${unit}` : body;
}

/** Kampala has no daylight saving: a local "YYYY-MM-DDTHH:mm" is exactly UTC+3. */
export function kampalaLocalToIso(local: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(local)) return null;
  const d = new Date(`${local}:00+03:00`);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export function formatKampala(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const k = new Date(d.getTime() + 3 * 3600_000).toISOString();
  return `${k.slice(0, 10)} ${k.slice(11, 16)} (Kampala)`;
}
