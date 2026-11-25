import type { AdminAccount } from "../auth/types";
import type { CoreSettingsClient } from "./core";
import { CoreSettingsError } from "./core";
import type { ChangeRequest } from "./types";

export type SettingsResult = { status: number; body: unknown };

export const KEY_RE = /^[a-z][a-z0-9_.]{0,63}$/;
const ALLOWED = new Set(["op", "key", "value", "reason", "revision", "effectiveAt"]);
const MAX_REASON = 2000;
const MAX_VALUE_CHARS = 1000;

const bad = (code: string): SettingsResult => ({ status: 400, body: { error: code } });

/**
 * Strict parse of what the browser may send. Unknown fields are refused (nothing about the actor,
 * the audit key or the revision history can ride along), the key must look like a setting key,
 * the reason is a bounded string, the revision a non-negative integer.
 */
export function parseChange(body: unknown): ChangeRequest | SettingsResult {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return bad("invalid");
  const b = body as Record<string, unknown>;
  if (Object.keys(b).some((k) => !ALLOWED.has(k))) return bad("unknown_field");
  const { op, key, reason, revision } = b;
  if (op !== "set" && op !== "rollback" && op !== "cancel") return bad("invalid_op");
  if (typeof key !== "string" || !KEY_RE.test(key)) return bad("invalid_key");
  if (typeof reason !== "string" || reason.length > MAX_REASON) return bad("invalid_reason");
  if (typeof revision !== "number" || !Number.isSafeInteger(revision) || revision < 0)
    return bad("invalid_revision");
  if (op !== "set") {
    if ("value" in b || "effectiveAt" in b) return bad("unknown_field");
    return { op, key, reason, revision };
  }
  const value = b.value;
  if (
    typeof value !== "number" &&
    (typeof value !== "object" || value === null || Array.isArray(value))
  )
    return bad("invalid_value");
  if (JSON.stringify(value).length > MAX_VALUE_CHARS) return bad("invalid_value");
  const out: ChangeRequest = { op, key, value: value as never, reason, revision };
  if (b.effectiveAt !== undefined) {
    if (typeof b.effectiveAt !== "string" || Number.isNaN(Date.parse(b.effectiveAt)))
      return bad("invalid_effective_at");
    out.effectiveAt = new Date(b.effectiveAt).toISOString();
  }
  return out;
}

const isResult = (x: ChangeRequest | SettingsResult): x is SettingsResult => "status" in x;

function fromCore(e: unknown): SettingsResult {
  if (e instanceof CoreSettingsError) {
    if (e.status >= 500 || e.status === 0) return { status: 503, body: { error: "unavailable" } };
    // 4xx: the core's own message (validation text, no secrets) so the form can show it
    return {
      status: e.status === 401 ? 403 : e.status,
      body: { error: e.code, message: e.message, currentRevision: e.currentRevision },
    };
  }
  return { status: 503, body: { error: "unavailable" } };
}

/** Defence in depth: requireAccess already limits the route to super admins; the core checks too. */
const mayManage = (a: AdminAccount) => a.roles.includes("superadmin");

export async function runChange(o: {
  admin: AdminAccount;
  body: unknown;
  client: CoreSettingsClient;
}): Promise<SettingsResult> {
  if (!mayManage(o.admin)) return { status: 403, body: { error: "forbidden" } };
  const req = parseChange(o.body);
  if (isResult(req)) return req;
  try {
    return { status: 200, body: await o.client.change(req) };
  } catch (e) {
    return fromCore(e);
  }
}

export async function runRead(o: {
  admin: AdminAccount;
  query: URLSearchParams;
  client: CoreSettingsClient;
}): Promise<SettingsResult> {
  if (!mayManage(o.admin)) return { status: 403, body: { error: "forbidden" } };
  const key = o.query.get("key");
  try {
    if (key === null) return { status: 200, body: await o.client.list() };
    if (!KEY_RE.test(key)) return bad("invalid_key");
    const beforeText = o.query.get("before");
    let before: number | undefined;
    if (beforeText !== null) {
      if (!/^\d{1,9}$/.test(beforeText) || Number(beforeText) < 1) return bad("invalid_before");
      before = Number(beforeText);
    }
    return { status: 200, body: await o.client.history(key, before) };
  } catch (e) {
    return fromCore(e);
  }
}
