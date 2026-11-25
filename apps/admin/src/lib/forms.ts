export type Rule = (value: string) => string | null;
export type Schema = Record<string, Rule[]>;
export type ValidationResult =
  | { ok: true; values: Record<string, string> }
  | { ok: false; errors: Record<string, string>; values: Record<string, string> };

export const required =
  (msg: string): Rule =>
  (v) =>
    v.length === 0 ? msg : null;
export const minLength =
  (n: number, msg: string): Rule =>
  (v) =>
    v.length < n ? msg : null;

/** Run on the server (server actions). One error per field, first failing rule wins. */
export function validate(schema: Schema, input: Record<string, unknown>): ValidationResult {
  const values: Record<string, string> = {};
  const errors: Record<string, string> = {};
  for (const [field, rules] of Object.entries(schema)) {
    const raw = input[field];
    values[field] = (typeof raw === "string" ? raw : "").trim();
    for (const rule of rules) {
      const e = rule(values[field]);
      if (e) {
        errors[field] = e;
        break;
      }
    }
  }
  return Object.keys(errors).length ? { ok: false, errors, values } : { ok: true, values };
}

export const REASON_SCHEMA: Schema = {
  reason: [
    required("Give a reason for this change"),
    minLength(10, "Reason must be at least 10 characters"),
  ],
};
