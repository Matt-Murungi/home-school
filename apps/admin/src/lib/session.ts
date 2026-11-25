import "server-only";
import { cookies, headers } from "next/headers";
import { SESSION_COOKIE } from "./access";
import { AppwriteAuthProvider } from "./auth/appwrite";
import { loadConfig } from "./config";
import { rolesFromLabels } from "./roles";
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

export function authProvider() {
  return new AppwriteAuthProvider(loadConfig());
}

/** Validates the cookie against Appwrite as that admin (setSession). Null if missing/expired/not an admin. */
export async function currentAdmin(): Promise<AdminAccount | null> {
  const secret = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!secret) return null;
  const state = await authProvider().accountState(secret);
  if (state.status !== "ok") return null;
  const roles = rolesFromLabels(state.account.labels);
  if (roles.length === 0) return null;
  const { id, name, email } = state.account;
  return { id, name, email, roles };
}

export async function requestPathname(): Promise<string> {
  return (await headers()).get("x-pathname") ?? "/";
}
