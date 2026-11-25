export const SESSION_COOKIE = "hs_admin_session";
const PUBLIC_PATHS = ["/login", "/api/session"];

export type AccessDecision = { action: "next" } | { action: "redirect"; to: string };

export function decideAccess(i: { pathname: string; search: string; hasSession: boolean }): AccessDecision {
  const isPublic = PUBLIC_PATHS.some((p) => i.pathname === p || i.pathname.startsWith(p + "/"));
  if (i.pathname === "/login" && i.hasSession) return { action: "redirect", to: "/" };
  if (isPublic || i.hasSession) return { action: "next" };
  return { action: "redirect", to: `/login?next=${encodeURIComponent(i.pathname + i.search)}` };
}

/** Only same-site absolute paths are allowed as a post-login target. */
export function safeNext(value: string | undefined | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  return value;
}
