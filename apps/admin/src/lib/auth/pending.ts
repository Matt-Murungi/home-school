import { createHmac, timingSafeEqual } from "node:crypto";
import type { AuthProvider } from "./types";
import type { PendingSession } from "./flow";

/** Server-enforced lifetime of the pending-MFA cookie (the browser maxAge is only a hint). */
export const PENDING_MAX_AGE_SECONDS = 60 * 10;
const MIN_KEY = 32;

export type OpenedPending =
  | { status: "ok"; pending: PendingSession }
  | { status: "expired"; pending: PendingSession }
  | { status: "invalid" };

const mac = (body: string, key: string) =>
  createHmac("sha256", key).update(body).digest("base64url");

/** `base64url(json{secret,challengeId,factor,iat}).hmac`. The HMAC key is a server-only secret. */
export function sealPending(p: PendingSession, key: string, nowMs: number): string {
  if (key.length < MIN_KEY) throw new Error("ADMIN_COOKIE_SECRET must be at least 32 characters");
  const body = Buffer.from(
    JSON.stringify({ s: p.secret, c: p.challengeId, f: p.factor, iat: Math.floor(nowMs / 1000) }),
  ).toString("base64url");
  return `${body}.${mac(body, key)}`;
}

export function openPending(cookie: string, key: string, nowMs: number): OpenedPending {
  const invalid: OpenedPending = { status: "invalid" };
  if (key.length < MIN_KEY) return invalid;
  const [body, sig, extra] = cookie.split(".");
  if (!body || !sig || extra !== undefined) return invalid;
  const want = Buffer.from(mac(body, key));
  const got = Buffer.from(sig);
  if (want.length !== got.length || !timingSafeEqual(want, got)) return invalid;
  try {
    const j = JSON.parse(Buffer.from(body, "base64url").toString());
    if (
      typeof j.s !== "string" ||
      typeof j.c !== "string" ||
      (j.f !== "totp" && j.f !== "email") ||
      typeof j.iat !== "number"
    )
      return invalid;
    const pending: PendingSession = { secret: j.s, challengeId: j.c, factor: j.f };
    const age = Math.floor(nowMs / 1000) - j.iat;
    if (age < 0) return invalid;
    return age > PENDING_MAX_AGE_SECONDS
      ? { status: "expired", pending }
      : { status: "ok", pending };
  } catch {
    return invalid;
  }
}

/**
 * Best effort: delete the password-only Appwrite session behind an expired or cancelled pending
 * MFA. A failure is swallowed (the session still dies when Appwrite expires it).
 */
export async function abandonPending(auth: AuthProvider, p: PendingSession): Promise<void> {
  try {
    await auth.deleteSession(p.secret);
  } catch {
    /* best effort */
  }
}
