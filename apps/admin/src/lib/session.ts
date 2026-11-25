import "server-only";
import { randomBytes } from "node:crypto";
import { cookies, headers } from "next/headers";
import { SESSION_COOKIE } from "./access";
import { AppwriteAuthProvider } from "./auth/appwrite";
import { loadConfig } from "./config";
import { redirect } from "next/navigation";
import { cache } from "react";
import { adminFromState } from "./auth/guard";
import { canAccessPath } from "./roles";
import type { AdminAccount } from "./auth/types";

export const MFA_COOKIE = "hs_admin_mfa";
export { SESSION_COOKIE };

export const cookieOptions = (maxAge: number) => ({
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "strict" as const,
  path: "/",
  maxAge,
});

export const nowMs = () => Date.now();

let devKey: string | undefined;
/**
 * HMAC key for the pending-MFA cookie. Required in production (ADMIN_COOKIE_SECRET, 32+ chars, same
 * on every instance). Outside production a random per-process key is used, so dev cookies simply
 * stop working after a restart.
 */
export function pendingKey(): string {
  const k = process.env.ADMIN_COOKIE_SECRET ?? "";
  if (k.length >= 32) return k;
  if (process.env.NODE_ENV === "production")
    throw new Error("ADMIN_COOKIE_SECRET is not set (32+ characters required)");
  return (devKey ??= randomBytes(32).toString("hex"));
}

export function authProvider() {
  return new AppwriteAuthProvider(loadConfig());
}

/** Validates the cookie against Appwrite as that admin (setSession). Null if missing/expired/no MFA/not an admin. */
export const currentAdmin = cache(async (): Promise<AdminAccount | null> => {
  const secret = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!secret) return null;
  return adminFromState(await authProvider().accountState(secret));
});

/**
 * Call at the top of EVERY admin page (and server action / route handler): `await requireAccess("/payments")`.
 * The layout guard alone is not enough: Next.js skips re-rendering a layout on client navigations
 * and on RSC requests that already hold it, so the page itself must check (verified on a built app).
 */
export async function requireAccess(route: string): Promise<AdminAccount> {
  const admin = await currentAdmin();
  if (!admin) redirect("/api/session/clear");
  if (!canAccessPath(route, admin.roles)) redirect("/forbidden");
  return admin;
}

export async function requestPathname(): Promise<string> {
  return (await headers()).get("x-pathname") ?? "/";
}
